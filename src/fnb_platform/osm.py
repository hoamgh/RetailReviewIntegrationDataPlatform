"""Offline OSM JSON-element adapter; no extraction or publication side effects.

Grain: one winning (node|way|relation, positive integer id) per input snapshot.
Input: JSON objects with OSM tags and node lat/lon, or way/relation GeoJSON
Point/Polygon/MultiPolygon or supplied center {lat, lon}. Overpass geometry
arrays/member assembly are deliberately unsupported. All coordinates are WGS84.
Dedup precedes filtering: highest version wins (missing version ranks zero),
then greatest canonical JSON
payload; rows sort by type/id. Invalid newest geometry never revives older data.
Unknown/review categories and invalid winners remain visible in diagnostics.
Raw input order is retained in observations; normalized results are order invariant.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
import math
import unicodedata

from shapely import get_coordinates
from shapely.errors import GEOSException
from shapely.geometry import Point, shape

from .catalog import PlaceCatalog, administrative_polygon
from .models import CanonicalPlace

MAPPING = json.loads(files(__package__).joinpath("osm_fnb_mapping.v1.json").read_text(encoding="utf-8"))
SCHEMA_VERSION = "osm-elements-v1"


@dataclass
class OSMIngestion:
    catalog: PlaceCatalog
    metadata: dict
    observations: list
    diagnostics: list[dict]


def _text(value):
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFC", value).split()) or None


def _positive_integer(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or value <= 0:
        raise ValueError("invalid_identity_or_version")
    return value


def _point(lat, lon):
    if isinstance(lat, bool) or isinstance(lon, bool):
        raise ValueError("invalid_geometry")
    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid_geometry") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("invalid_geometry")
    return Point(lon, lat)


def _geometry(record):
    if record["type"] == "node":
        if "lat" not in record or "lon" not in record:
            raise ValueError("missing_geometry")
        return _point(record.get("lat"), record.get("lon")), "node_coordinates"
    if "geometry" in record:
        if not isinstance(record["geometry"], dict):
            raise ValueError("unsupported_geometry")
        if record["geometry"].get("type") not in ("Point", "Polygon", "MultiPolygon"):
            raise ValueError("unsupported_geometry")
        try:
            geometry = shape(record["geometry"])
            if geometry.is_empty or not geometry.is_valid or geometry.has_z:
                raise ValueError("invalid_geometry")
            for lon, lat in get_coordinates(geometry):
                _point(lat, lon)
            if geometry.geom_type == "Point":
                return geometry, "source_point"
            return geometry.representative_point(), "polygon_representative_point"
        except (TypeError, ValueError, KeyError, IndexError, AttributeError, GEOSException):
            raise ValueError("invalid_geometry") from None
    center = record.get("center")
    if not isinstance(center, dict):
        raise ValueError("missing_geometry")
    return _point(center.get("lat"), center.get("lon")), "supplied_center"


def _category(tags):
    # Source tag labels are kept; application groups do not invent OSM taxonomy.
    for key in ("amenity", "shop"):
        value = _text(tags.get(key))
        if value in MAPPING["review"][key]:
            raise ValueError("review_category")
        if value in MAPPING["include"][key]:
            labels = [f"{k}={_text(tags.get(k))}" for k in ("amenity", "shop")
                      if _text(tags.get(k)) in MAPPING["include"][k]]
            return labels, MAPPING["include"][key][value]
    raise ValueError("unclassified_category")


def _address(tags):
    full = _text(tags.get("addr:full"))
    if full:
        return full
    street = " ".join(filter(None, (_text(tags.get("addr:housenumber")), _text(tags.get("addr:street")))))
    return ", ".join(filter(None, [street] + [_text(tags.get("addr:" + part))
                                            for part in ("suburb", "city", "postcode")])) or None


def build_osm_catalog(records, admin_area, *, observed_at, source_metadata) -> OSMIngestion:
    """Normalize a bounded snapshot in memory, preserving all observations.

    source_metadata must identify release/cutoff, URI and acquisition timestamp;
    its remaining fields (input checksum, rights review, extractor, etc.) are
    retained verbatim. Missing names/addresses remain null. No name-based merging.
    Boundary inclusion uses covers(representative point), including border points;
    supplied centers and polygon-derived points are explicitly approximate.
    Counts partition records_read into duplicates plus winner outcomes/invalids.
    This result is not a published dataset or proof of real-source integration.
    """
    if not isinstance(source_metadata, dict) or any(
        not _text(source_metadata.get(key)) for key in ("source_release", "source_uri", "acquired_at")
    ):
        raise ValueError("Source release, URI and acquisition timestamp are required")
    if not _text(observed_at):
        raise ValueError("observed_at is required")
    polygon = administrative_polygon(admin_area)
    observations = deepcopy(list(records))
    counts = dict(records_read=len(observations), duplicates=0, invalid=0,
                  review=0, unclassified=0, outside_polygon=0, accepted=0)
    diagnostics, winners = [], {}
    for index, record in enumerate(observations):
        try:
            if not isinstance(record, dict) or record.get("type") not in ("node", "way", "relation"):
                raise ValueError("invalid_identity")
            identity = (record["type"], _positive_integer(record.get("id")))
            version = _positive_integer(record["version"]) if "version" in record else 0
            rank = (version, json.dumps(record, sort_keys=True, ensure_ascii=False))
            if identity in winners:
                counts["duplicates"] += 1
            if identity not in winners or rank > winners[identity][0]:
                winners[identity] = (rank, record)
        except (ValueError, TypeError):
            counts["invalid"] += 1
            diagnostics.append(dict(observation_index=index, status="invalid",
                                    reason="invalid_identity_or_payload"))

    places, raw, coordinates = [], {}, {}
    props = admin_area["properties"]
    for (element_type, element_id), (_, record) in sorted(winners.items()):
        source_id = f"{element_type}/{element_id}"
        key = "osm:" + source_id
        try:
            tags = record.get("tags", {})
            if not isinstance(tags, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                 for k, v in tags.items()):
                raise ValueError("invalid_tags")
            point, method = _geometry(record)
            labels, group = _category(tags)
            if not polygon.covers(point):
                counts["outside_polygon"] += 1
                diagnostics.append(dict(place_key=key, status="outside_polygon", reason="representative_point_outside"))
                continue
            name = next((value for tag in ("name", "name:vi", "name:en", "official_name")
                         if (value := _text(tags.get(tag)))), None)
            places.append(CanonicalPlace(
                place_key=key, source="osm", source_place_id=source_id,
                name=name, lat=point.y, lng=point.x, address=_address(tags),
                primary_category=labels[0], categories=labels, category_hierarchy=[],
                product_category_group=group, category_source="osm", confidence=None,
                admin_area_id=props["admin_area_id"], admin_area_name=props["admin_area_name"],
                first_seen_at=observed_at, last_seen_at=observed_at,
            ))
            raw[key] = deepcopy(record)
            coordinates[key] = dict(method=method, approximate=method != "node_coordinates")
            counts["accepted"] += 1
        except (ValueError, TypeError, OverflowError) as error:
            known_reasons = {"invalid_tags", "invalid_geometry", "missing_geometry",
                             "unsupported_geometry", "review_category", "unclassified_category"}
            reason = str(error) if str(error) in known_reasons else "invalid_geometry"
            status = {"review_category": "review", "unclassified_category": "unclassified"}.get(reason, "invalid")
            counts[status] += 1
            diagnostics.append(dict(place_key=key, status=status,
                                    reason=reason if isinstance(error, ValueError) else "invalid_geometry"))

    metadata = dict(source_name="osm", source_schema_version=SCHEMA_VERSION,
                    input_metadata=deepcopy(source_metadata), mapping_version=MAPPING["version"],
                    observed_at=observed_at, boundary_id=props["admin_area_id"],
                    boundary_sha256=hashlib.sha256(json.dumps(admin_area["geometry"], sort_keys=True).encode()).hexdigest(),
                    attribution="OpenStreetMap contributors; ODbL", coordinates=coordinates,
                    counts=deepcopy(counts))
    return OSMIngestion(PlaceCatalog(places, raw, counts), metadata, observations, diagnostics)
