"""Owner-run Foody profiling using the verified Overture district polygon.

Default is offline. --crawl requires verified collection/storage rights. --full
uses UI pagination without a POI cap; completion evidence is reported separately.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid

import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
import crawl

STRICT = {'café/dessert', 'cafe/dessert', 'nhà hàng', 'quán ăn', 'ăn vặt/vỉa hè',
          'tiệm bánh', 'quán nhậu', 'bar/pub', 'buffet', 'beer club', 'quán chay',
          'giao cơm văn phòng', 'bistro'}
SEED = 'https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4?categorygroup=food'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def verified_boundary(root):
    pointer = json.loads((root / 'current.json').read_text(encoding='utf-8'))
    run = Path(pointer['path']).resolve()
    if not run.is_relative_to((root / 'runs').resolve()):
        raise ValueError('Unexpected Overture run path')
    manifest_path = run / 'manifest.json'
    if digest(manifest_path) != pointer['manifest_sha256']:
        raise ValueError('Overture pointer integrity failure')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    path = run / 'district4_boundary.geojson'
    if digest(path) != manifest['artifacts']['district4_boundary.geojson']:
        raise ValueError('District boundary checksum failure')
    document = json.loads(path.read_text(encoding='utf-8'))
    geom = shape(document['geometry'])
    if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError('Invalid verified district polygon')
    return geom, dict(overture_run=pointer['run_id'], sha256=digest(path),
                      metadata=manifest['boundary'], source_properties=document['properties'])


def classify(record):
    labels = (record.get('categories') or {}).get('Thể loại') or []
    return 'strict_fnb' if any(crawl.clean(x).casefold() in STRICT for x in labels) else 'uncertain'


def profile_rows(records, district):
    seen = {}; output = []; dq = Counter()
    for record in records:
        identity = record.get('source_id') or record.get('url')
        if not identity:
            dq['missing_identity'] += 1
            continue
        identity = str(identity)
        if identity in seen:
            if record != seen[identity]:
                raise ValueError('Conflicting Foody identity; manual review required')
            dq['exact_duplicates'] += 1
            continue
        seen[identity] = record
        try:
            lat, lon = float(record['latitude']), float(record['longitude'])
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            dq['unlocated_or_invalid_geometry'] += 1
            continue
        if not district.covers(Point(lon, lat)):
            dq['outside_polygon'] += 1
            continue
        fields = record.get('fields') or {}
        categories = record.get('categories') or {}
        provenance = record.get('provenance') or {}
        row = dict(source='foody', source_id=identity, url=record.get('url'), name=record.get('name'),
                   latitude=lat, longitude=lon, geometry=Point(lon, lat).wkb,
                   address=' '.join(fields.get('streetAddress') or []),
                   venue_types=categories.get('Thể loại') or [],
                   cuisines=categories.get('Phong cách ẩm thực') or fields.get('servesCuisine') or [],
                   dishes=categories.get('Phục vụ các món') or [],
                   classification=classify(record),
                   retrieved_at=provenance.get('retrieved_at'),
                   response_sha256=provenance.get('response_sha256'),
                   review_count_displayed=record.get('review_count_displayed'),
                   review_sample_count=len(record.get('review_samples') or []))
        output.append(row)
        dq['missing_name'] += not bool(row['name'])
        dq['missing_address'] += not bool(row['address'])
        dq['missing_venue_type'] += not bool(row['venue_types'])
    return sorted(output, key=lambda x: x['source_id']), dict(dq), len(seen)


SCHEMA = pa.schema([
    ('source', pa.string()), ('source_id', pa.string()), ('url', pa.string()), ('name', pa.string()),
    ('latitude', pa.float64()), ('longitude', pa.float64()), ('geometry', pa.binary()),
    ('address', pa.string()), ('venue_types', pa.list_(pa.string())),
    ('cuisines', pa.list_(pa.string())), ('dishes', pa.list_(pa.string())),
    ('classification', pa.string()), ('retrieved_at', pa.string()), ('response_sha256', pa.string()),
    ('review_count_displayed', pa.int64()), ('review_sample_count', pa.int64())],
    metadata={b'geo': json.dumps(dict(version='1.1.0', primary_column='geometry',
              columns={'geometry': dict(encoding='WKB', geometry_types=['Point'])})).encode()})


def publish(raw, root, boundary, provenance):
    records = json.loads(raw.read_text(encoding='utf-8'))
    if not isinstance(records, list):
        raise ValueError('Expected crawler pois.json list')
    rows, dq, unique = profile_rows(records, boundary)
    crawl_summary = raw.with_name('summary.json')
    crawl_evidence = json.loads(crawl_summary.read_text(encoding='utf-8')) if crawl_summary.exists() else {}
    signature = dict(raw_sha256=digest(raw), boundary_sha256=provenance['sha256'],
                     discovery_summary_sha256=digest(crawl_summary) if crawl_summary.exists() else None,
                     code_sha256=digest(Path(__file__)))
    run_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    destination = root / 'runs' / run_id
    if destination.exists():
        manifest = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['inputs'] != signature or any(digest(destination / k) != v for k, v in manifest['artifacts'].items()):
            raise ValueError('Existing Foody profile integrity failure')
        return destination
    stage = root / ('partial-' + uuid.uuid4().hex); stage.mkdir(parents=True)
    if not rows:
        crawl.save(stage / 'failure.json', dict(status='NO_LOCATED_POIS_IN_POLYGON', raw_count=len(records)))
        raise ValueError('No located POIs in district; no current pointer update')
    for filename, selected in [('foody_all_located_district4', rows),
            ('foody_fnb_district4', [x for x in rows if x['classification'] == 'strict_fnb']),
            ('foody_uncertain_district4', [x for x in rows if x['classification'] == 'uncertain'])]:
        path = stage / (filename + '.parquet')
        pq.write_table(pa.Table.from_pylist(selected, schema=SCHEMA), path, compression='zstd')
        with pq.ParquetFile(path) as f:
            if f.metadata.num_rows != len(selected):
                raise ValueError('Foody Parquet row count mismatch')
    summary = dict(raw_count=len(records), unique_count=unique, inside_polygon=len(rows),
        strict_fnb=sum(x['classification'] == 'strict_fnb' for x in rows),
        uncertain=sum(x['classification'] == 'uncertain' for x in rows), data_quality=dq,
        venue_types=dict(Counter(x for r in rows for x in r['venue_types'])),
        cuisines=dict(Counter(x for r in rows for x in r['cuisines'])),
        review_samples=sum(x['review_sample_count'] for x in rows),
        coverage=crawl_evidence.get('coverage', 'UNVERIFIED_INPUT'), is_full_district_count=False,
        crawl_status=crawl_evidence.get('status', 'UNKNOWN'),
        inclusion_rule='polygon.covers(point), including boundary', boundary=provenance,
        rights_status='Operator responsibility; not inferred from input file presence',
        matching_status='NOT_RUN; use district4_profile/run.py --foody RAW_POIS_JSON')
    crawl.save(stage / 'summary.json', summary)
    crawl.save(stage / 'manifest.json', dict(inputs=signature,
        artifacts={p.name: digest(p) for p in stage.iterdir() if p.is_file()}))
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(stage, destination)
    crawl.save(root / 'current.json', dict(run_id=run_id, path=str(destination),
                                         manifest_sha256=digest(destination / 'manifest.json')))
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--crawl', action='store_true')
    parser.add_argument('--full', action='store_true', help='UI load-more pagination; no POI/page-count cap')
    parser.add_argument('--max-minutes', type=int, default=180, help='Full-mode time budget; resumable')
    parser.add_argument('--headless', action='store_true', help='Full mode: default is headed Chrome')
    parser.add_argument('--storage-state', type=Path, help='Full mode: saved Foody session; never modifies personal Chrome')
    parser.add_argument('--profile-directory', type=Path, help='Full mode: dedicated manual Foody profile, after its window is closed')
    parser.add_argument('--collect-discovered-from', type=Path,
                        help='Collect saved detail URLs into a NEW output; never retries stalled discovery')
    parser.add_argument('--continue-discovery-from', type=Path,
                        help='Continue deferred discovery using a completed detail-only checkpoint; keep collected POIs')
    parser.add_argument('--rights-confirmed', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-places', type=int, default=100)
    parser.add_argument('--max-pages', type=int, default=200)
    parser.add_argument('--review-limit', type=int, default=5)
    parser.add_argument('--root', type=Path, default=ROOT / 'output/foody-q4-profile-v1')
    args = parser.parse_args()
    chosen_root = (ROOT / 'output/foody-q4-full-v1'
                   if args.full and args.root == ROOT / 'output/foody-q4-profile-v1' else args.root)
    root = chosen_root.resolve()
    if not root.is_relative_to((ROOT / 'output').resolve()):
        parser.error('Use a dedicated directory under repository output/')
    if args.crawl and not args.rights_confirmed:
        parser.error('--crawl requires verified collection/storage permission (--rights-confirmed)')
    if args.full and not args.crawl:
        parser.error('--full requires --crawl')
    if args.storage_state and not args.full:
        parser.error('--storage-state requires --full')
    if args.profile_directory and (not args.full or args.storage_state):
        parser.error('--profile-directory requires --full and cannot combine with --storage-state')
    if args.collect_discovered_from and not args.full:
        parser.error('--collect-discovered-from requires --full')
    if args.continue_discovery_from and (not args.full or args.collect_discovered_from):
        parser.error('--continue-discovery-from requires --full and cannot combine with --collect-discovered-from')
    district, metadata = verified_boundary(ROOT / 'output/district4-profile')
    raw = root / 'raw' / 'pois.json'
    if args.crawl:
        if args.full:
            import full
            state = full.run(SEED, raw.parent, review_limit=args.review_limit,
                             resume=args.resume, max_minutes=args.max_minutes, headless=args.headless,
                             storage_state=args.storage_state, profile_directory_override=args.profile_directory,
                             collect_discovered_from=args.collect_discovered_from,
                             continue_discovery_from=args.continue_discovery_from)
        else:
            state = crawl.run(SEED, raw.parent, max_places=args.max_places, max_pages=args.max_pages,
                              review_limit=args.review_limit, delay=3, resume=args.resume)
        if state['status'] in ('STOPPED', 'PAUSED'):
            print('Crawl stopped; partial evidence retained. No automatic profiling or retry.', flush=True)
            return 1
    if not raw.exists():
        parser.error('No local Foody input; see --crawl or supply existing authorized pois.json under ROOT/raw/')
    result = publish(raw, root / 'profile', district, metadata)
    print('Published PARTIAL Foody profile: ' + str(result), flush=True)
    print((result / 'summary.json').read_text(encoding='utf-8'), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
