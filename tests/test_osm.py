"""Synthetic OSM snapshot fixtures; no source access or dataset publication."""
from copy import deepcopy
from itertools import permutations

import pytest
from shapely.geometry import Point, Polygon, mapping

from fnb_platform.osm import build_osm_catalog


@pytest.fixture
def ward():
    return {"type": "Feature", "properties": {
        "admin_area_id": "fixture-ward", "admin_area_name": "Fixture Ward", "is_current": True,
    }, "geometry": mapping(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]))}


@pytest.fixture
def metadata():
    return dict(source_release="fixture-cutoff", source_uri="fixture://osm",
                acquired_at="2026-10-28T00:00:00Z", input_file_sha256="fixture-checksum",
                license_review_status="synthetic-only", extractor={"name": "fixture"})


def node(**changes):
    return dict(type="node", id=7, version=1, lat=1, lon=1,
                tags={"amenity": "cafe", "name": "  Quán   Cà phê  "}, **changes)


def ingest(records, ward, metadata):
    return build_osm_catalog(records, ward, observed_at="2026-10-28T01:00:00Z", source_metadata=metadata)


def test_node_normalization_and_detached_provenance(ward, metadata):
    record = node()
    record["tags"].update({"addr:housenumber": " 12 ", "addr:street": " Đường A ",
                          "addr:city": " Thành phố B ", "cuisine": "vietnamese", "source": "survey"})
    record.update(timestamp="2026-10-01T00:00:00Z", changeset=123)
    saved_record, saved_metadata = deepcopy(record), deepcopy(metadata)
    result = ingest([record], ward, metadata)
    place = result.catalog.places[0]
    assert (place.place_key, place.source_place_id, place.source) == ("osm:node/7", "node/7", "osm")
    assert (place.name, place.address) == ("Quán Cà phê", "12 Đường A, Thành phố B")
    assert place.primary_category == "amenity=cafe" and place.product_category_group == "cafe_coffee"
    assert place.category_hierarchy == [] and place.confidence is None
    assert (place.lat, place.lng, place.admin_area_id) == (1, 1, "fixture-ward")
    assert result.metadata["mapping_version"] == "osm-fnb-v1"
    assert result.metadata["coordinates"][place.place_key] == dict(method="node_coordinates", approximate=False)
    assert result.catalog.raw_payloads[place.place_key] == saved_record
    assert record == saved_record and metadata == saved_metadata
    record["tags"]["name"] = "changed"
    metadata["extractor"]["name"] = "changed"
    assert result.observations == [saved_record]
    assert result.metadata["input_metadata"] == saved_metadata


@pytest.mark.parametrize("element_type", ["node", "way", "relation"])
def test_identity_types_and_numeric_string_ids(ward, metadata, element_type):
    record = node()
    record.update(type=element_type, id="007", center=dict(lat=1, lon=1))
    result = ingest([record], ward, metadata)
    assert result.catalog.places[0].place_key == f"osm:{element_type}/7"


def test_same_brand_nearby_and_different_element_types_stay_distinct(ward, metadata):
    records = [node(), node()]
    records[1].update(id=8, lon=1.00001)
    way = deepcopy(records[0]); way.update(type="way", center=dict(lat=1, lon=1))
    result = ingest(records + [way], ward, metadata)
    assert len(result.catalog.places) == 3


def test_polygon_representative_point_not_centroid_and_raw_geometry_preserved(ward, metadata):
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)],
                      holes=[[(1, 1), (3, 1), (3, 3), (1, 3)]])
    record = dict(type="way", id=10, tags={"shop": "bakery"}, geometry=mapping(polygon))
    result = ingest([record], ward, metadata)
    place = result.catalog.places[0]
    assert polygon.covers(Point(place.lng, place.lat))
    assert not polygon.covers(polygon.centroid)
    assert result.metadata["coordinates"][place.place_key] == dict(method="polygon_representative_point", approximate=True)
    assert result.observations[0]["geometry"] == record["geometry"]


@pytest.mark.parametrize("geometry", [
    {"type": "Point", "coordinates": [1, 1]},
    {"type": "MultiPolygon", "coordinates": [[[[0, 0], [2, 0], [2, 2], [0, 0]]]]},
])
def test_supplied_geojson_geometry(ward, metadata, geometry):
    result = ingest([dict(type="relation", id=1, tags={"amenity": "restaurant"}, geometry=geometry)], ward, metadata)
    assert result.catalog.counts["accepted"] == 1


def test_dedup_order_independent_highest_version_then_payload(ward, metadata):
    old = node(); newest = node(); newest.update(version=2)
    conflicting = deepcopy(newest); conflicting["tags"]["name"] = "ZZZ"
    results = [ingest(records, ward, metadata) for records in permutations([old, newest, conflicting])]
    assert all(r.catalog.places == results[0].catalog.places for r in results)
    assert all(r.catalog.raw_payloads == results[0].catalog.raw_payloads for r in results)
    assert all(r.catalog.counts["duplicates"] == 2 for r in results)
    assert results[0].catalog.places[0].name == "ZZZ"
    repeated = ingest([old, deepcopy(old)], ward, metadata)
    assert repeated.catalog.counts["accepted"] == repeated.catalog.counts["duplicates"] == 1


def test_invalid_latest_does_not_resurrect_older_observation(ward, metadata):
    old = node(); newest = node(); newest.update(version=2, lat=999)
    result = ingest([old, newest], ward, metadata)
    assert result.catalog.places == [] and len(result.observations) == 2
    assert result.catalog.counts["duplicates"] == 1 and result.catalog.counts["invalid"] == 1


@pytest.mark.parametrize("changes", [
    {"id": None}, {"id": True}, {"id": -1}, {"id": 1.5}, {"type": "area"},
    {"version": False}, {"version": -1}, {"lat": None}, {"lat": "bad"},
    {"lat": float("nan")}, {"lat": float("inf")}, {"lat": 91}, {"lon": 181},
    {"lat": True}, {"tags": None}, {"tags": {"name": 42}},
])
def test_malformed_identity_tags_and_coordinates(ward, metadata, changes):
    record = node(); record.update(changes)
    result = ingest([record], ward, metadata)
    assert result.catalog.places == []
    assert result.catalog.counts["invalid"] == 1
    assert len(result.observations) == len(result.diagnostics) == 1


@pytest.mark.parametrize("record,reason", [
    ({"type": "node", "id": 1}, "missing_geometry"),
    ({"type": "way", "id": 1}, "missing_geometry"),
    ({"type": "way", "id": 1, "geometry": []}, "unsupported_geometry"),
    ({"type": "way", "id": 1, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}, "unsupported_geometry"),
    ({"type": "way", "id": 1, "geometry": {"type": "Polygon", "coordinates": []}}, "invalid_geometry"),
    ({"type": "way", "id": 1, "geometry": {"type": "Point", "coordinates": [1, 1, 2]}}, "invalid_geometry"),
    ({"type": "way", "id": 1, "geometry": {"type": "Point", "coordinates": [181, 1]}}, "invalid_geometry"),
    ({"type": "way", "id": 1, "geometry": {"type": "Point"}}, "invalid_geometry"),
    ({"type": "relation", "id": 1, "geometry": {"type": "Polygon", "coordinates": [
        [[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]}}, "invalid_geometry"),
])
def test_missing_invalid_and_unsupported_geometry(ward, metadata, record, reason):
    record["tags"] = {"amenity": "cafe"}
    # An invalid explicit geometry must not silently fall back to this center.
    if "geometry" in record:
        record["center"] = {"lat": 1, "lon": 1}
    result = ingest([record], ward, metadata)
    assert result.diagnostics[0]["reason"] == reason
    assert result.catalog.counts["invalid"] == 1


@pytest.mark.parametrize("tags,group", [
    ({"amenity": "restaurant"}, "restaurant"), ({"amenity": "fast_food"}, "fast_food"),
    ({"amenity": "food_court"}, "other_food"), ({"amenity": "ice_cream"}, "bakery_dessert"),
    ({"shop": "bakery"}, "bakery_dessert"), ({"shop": "pastry"}, "bakery_dessert"),
])
def test_versioned_fnb_mapping(ward, metadata, tags, group):
    record = node(); record["tags"] = tags
    place = ingest([record], ward, metadata).catalog.places[0]
    assert place.product_category_group == group
    assert place.name is None and place.address is None


def test_missing_name_address_fallback_review_unknown_and_reconciliation(ward, metadata):
    records = [node() for _ in range(5)]
    for i, record in enumerate(records):
        record["id"] = i + 1
    records[0]["tags"] = {"amenity": "cafe", "name:vi": " Quán A ", "addr:full": "  Địa chỉ A  "}
    records[1]["tags"] = {"amenity": "bar"}
    records[2]["tags"] = {"name": "Cafe name alone does not establish category"}
    records[3]["lon"] = 5
    records[4]["lat"] = 0  # covers includes the boundary
    result = ingest(records + [deepcopy(records[0]), None], ward, metadata)
    assert (result.catalog.places[0].name, result.catalog.places[0].address) == ("Quán A", "Địa chỉ A")
    c = result.catalog.counts
    assert c == dict(records_read=7, duplicates=1, invalid=1, review=1,
                     unclassified=1, outside_polygon=1, accepted=2)
    assert c["records_read"] == sum(v for k, v in c.items() if k != "records_read")


def test_source_metadata_and_boundary_contracts(ward, metadata):
    for field in ("source_release", "source_uri", "acquired_at"):
        broken = dict(metadata); broken.pop(field)
        with pytest.raises(ValueError, match="Source release"):
            ingest([], ward, broken)
    ward["properties"]["is_current"] = False
    with pytest.raises(ValueError, match="current ward"):
        ingest([], ward, metadata)


def test_empty_input(ward, metadata):
    result = ingest([], ward, metadata)
    assert result.catalog.places == result.observations == result.diagnostics == []
    assert all(value == 0 for value in result.catalog.counts.values())
