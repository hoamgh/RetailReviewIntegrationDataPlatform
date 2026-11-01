"""Offline descriptive profiling of fixed Foody/Overture inputs; no crawling or source writes."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

import pyarrow.parquet as pq
from shapely.geometry import shape

from evaluate import FOODY, OVERTURE, WARD, clip, dedup, digest, foody_rows, norm, overture_rows, stats, valid_coords


def grouped_duplicates(rows, key_fn):
    groups = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        if key and all(key):
            groups[key].append(row['id'])
    candidates = [ids for ids in groups.values() if len(ids) > 1]
    return {'groups': len(candidates), 'rows_in_groups': sum(map(len, candidates)),
            'surplus_rows': sum(len(ids)-1 for ids in candidates)}


def quantiles(values):
    values = sorted(float(x) for x in values if x is not None)
    if not values:
        return None
    return {'n': len(values), 'min': values[0], 'median': statistics.median(values),
            'p90': values[round((len(values)-1)*.90)], 'max': values[-1]}


def source_lifecycle(records, ward_ids, release_day='2026-10-21'):
    """Observed source attribution and timestamp age, counting each POI once per source."""
    release = datetime.fromisoformat(release_day).replace(tzinfo=timezone.utc)
    providers, age_buckets = Counter(), Counter()
    missing_time, future_time, source_links, multi_source = 0, 0, 0, 0
    upstream_ages = Counter()
    for record in records:
        if str(record.get('id')) not in ward_ids:
            continue
        sources = record.get('sources') or []
        source_links += len(sources)
        multi_source += len(sources) > 1
        providers.update(set(str(s.get('dataset') or '<MISSING>') for s in sources))
        times, upstream_times = [], []
        for source in sources:
            value = source.get('update_time')
            if value:
                try:
                    moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=timezone.utc)
                    times.append(moment)
                    if str(source.get('dataset') or '').casefold() != 'overture':
                        upstream_times.append(moment)
                except ValueError:
                    continue
        if not upstream_times:
            upstream_ages['UNKNOWN'] += 1
        else:
            upstream_days = (release - max(upstream_times)).total_seconds()/86400
            upstream_ages['FUTURE_TIMESTAMP' if upstream_days < 0 else
                          '0-30d' if upstream_days <= 30 else
                          '31-90d' if upstream_days <= 90 else
                          '91-180d' if upstream_days <= 180 else
                          '181-365d' if upstream_days <= 365 else '>365d'] += 1
        if not times:
            missing_time += 1
            age_buckets['UNKNOWN'] += 1
            continue
        days = (release - max(times)).total_seconds()/86400
        if days < 0:
            future_time += 1
            age_buckets['FUTURE_TIMESTAMP'] += 1
        elif days <= 30:
            age_buckets['0-30d'] += 1
        elif days <= 90:
            age_buckets['31-90d'] += 1
        elif days <= 180:
            age_buckets['91-180d'] += 1
        elif days <= 365:
            age_buckets['181-365d'] += 1
        else:
            age_buckets['>365d'] += 1
    return dict(providers=dict(providers.most_common()), source_links=source_links,
                multi_source_pois=multi_source, missing_source_update_time=missing_time,
                future_timestamp_pois=future_time, age_buckets=dict(age_buckets),
                upstream_age_buckets=dict(upstream_ages))


def summarize(rows):
    s = stats(rows)
    coordinate_groups = grouped_duplicates(rows, lambda r: (round(r['lat'], 6), round(r['lon'], 6))
                                            if valid_coords(r['lat'], r['lon']) else ())
    named_address_groups = grouped_duplicates(rows, lambda r: (norm(r['name']), norm(r['address'])))
    timestamps = sorted(str(r['updated']) for r in rows if r.get('updated'))
    s.update(coordinate_collision=coordinate_groups, exact_normalized_name_address=named_address_groups,
             names_duplicated=grouped_duplicates(rows, lambda r: (norm(r['name']),)),
             names_blank_or_placeholder=sum(norm(r['name']) in ('', 'unknown', 'none', 'n a', 'khong ten') for r in rows),
             addresses_blank_or_placeholder=sum(norm(r['address']) in ('', 'unknown', 'none', 'n a') for r in rows),
             timestamp_min=timestamps[0] if timestamps else None,
             timestamp_max=timestamps[-1] if timestamps else None,
             lat_quantiles=quantiles([r['lat'] for r in rows if valid_coords(r['lat'], r['lon'])]),
             lon_quantiles=quantiles([r['lon'] for r in rows if valid_coords(r['lat'], r['lon'])]),
             distinct_categories=len(set(r['category'] for r in rows)),
             review_count_quantiles=quantiles([r['reviews'] for r in rows if isinstance(r.get('reviews'), (int, float))]),
             price_examples_count=len(set(str(r['price']) for r in rows if r.get('price'))))
    return s


def tabulate(rows, source, scope, metric_csv):
    s = summarize(rows)
    for field, count in s['present'].items():
        metric_csv.append([source, scope, f'{field}_present', count, len(rows), f'{count/len(rows):.5f}' if rows else ''])
    for field in ('names_blank_or_placeholder', 'addresses_blank_or_placeholder'):
        metric_csv.append([source, scope, field, s[field], len(rows), f'{s[field]/len(rows):.5f}' if rows else ''])
    for field in ('coordinate_collision', 'exact_normalized_name_address', 'names_duplicated'):
        metric_csv.append([source, scope, f'{field}_groups', s[field]['groups'], len(rows), ''])
        metric_csv.append([source, scope, f'{field}_rows_in_groups', s[field]['rows_in_groups'], len(rows), ''])
    return s


def run(output):
    began = time.monotonic()
    state = json.loads(FOODY.read_text(encoding='utf8'))
    ward = json.loads(WARD.read_text(encoding='utf8'))
    polygon = shape(ward['features'][0]['geometry'] if ward.get('type') == 'FeatureCollection' else ward['geometry'])
    f_raw = foody_rows(state)
    ov_table = pq.read_table(OVERTURE)
    o_raw = overture_rows(ov_table)
    f, f_dup, f_missing = dedup(f_raw)
    o, o_dup, o_missing = dedup(o_raw)
    fw, f_exc = clip(f, polygon)
    ow, o_exc = clip(o, polygon)
    lifecycle = source_lifecycle(ov_table.to_pylist(), {row['id'] for row in ow})
    metrics = []
    profiles = {source: {scope: tabulate(rows, source, scope, metrics) for scope, rows in scopes.items()}
                for source, scopes in [('foody', {'district4_input': f, 'khanh_hoi': fw}),
                                       ('overture', {'district4_input': o, 'khanh_hoi': ow})]}
    # Raw field-type inventory is observational; nested source data is never included in outputs.
    foody_field_names = Counter(name for record in state.get('records', {}).values()
                                for name in (record.get('fields') or {}))
    foody_category_names = Counter(name for record in state.get('records', {}).values()
                                   for name in (record.get('categories') or {}))
    overview = {
        'inputs_sha256': {str(path.relative_to(FOODY.parents[2])): digest(path) for path in (FOODY, OVERTURE, WARD)},
        'input_bytes': {'foody_state_json': FOODY.stat().st_size, 'overture_fnb_parquet': OVERTURE.stat().st_size},
        'foody_coverage': state.get('coverage'),
        'scope': 'Khánh Hội polygon; same clip rule; source-specific F&B filters not fully harmonized',
        'source_counts': {'foody': {'input': len(f_raw), 'unique': len(f), 'duplicate_ids': f_dup,
                                    'missing_ids': f_missing, 'ward': len(fw), 'excluded': f_exc},
                          'overture': {'input': len(o_raw), 'unique': len(o), 'duplicate_ids': o_dup,
                                      'missing_ids': o_missing, 'ward': len(ow), 'excluded': o_exc}},
        'source_schemas': {'foody_record_fields': sorted(set(k for r in state.get('records', {}).values() for k in r)),
                           'foody_nested_field_presence': dict(sorted(foody_field_names.items())),
                           'foody_nested_category_presence': dict(sorted(foody_category_names.items())),
                           'overture_arrow_schema': str(ov_table.schema)},
        'overture_source_lifecycle_khanh_hoi': lifecycle,
        'profiles': profiles,
    }
    output.mkdir(parents=True, exist_ok=True)
    with (output/'detailed_metrics.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['source', 'scope', 'metric', 'count', 'denominator', 'rate'])
        writer.writerows(metrics)
    (output/'profile_summary.json').write_text(json.dumps(overview, indent=2, ensure_ascii=False, default=str)+'\n', encoding='utf8')
    lines = ['# Detailed offline data profiling — Khánh Hội', '',
             'Snapshot evidence; this report does not establish ground-truth accuracy, business activity or district-wide coverage.',
             '', '## Input lineage and scope', '',
             f'- Foody: first 1,000 discovered detailed URLs; coverage `{state.get("coverage")}`.',
             '- Overture: release `2026-10-21.0`, strict F&B District 4 Parquet.',
             '- Same current Khánh Hội polygon, point-in-polygon covers boundary. Overture area geometry represented by point for this clip.',
             '- All counts are from the cached files; no live requests. Input hashes and full Arrow schema are in `profile_summary.json`.',
             '', '## Population, exclusions and fields', '',
             '| Source | Input distinct IDs | In ward | Outside | Invalid/missing geometry | Duplicate source IDs |',
             '|---|---:|---:|---:|---:|---:|']
    for source in ('foody', 'overture'):
        c = overview['source_counts'][source]
        lines.append(f"| {source} | {c['unique']} | {c['ward']} | {c['excluded'].get('outside_polygon',0)} | {c['excluded'].get('invalid_geometry',0)+c['excluded'].get('missing_or_invalid_coordinates',0)} | {c['duplicate_ids']} |")
    lines += ['', 'Field presence is conditional on **inside-polygon** records; 0% can mean the source mapping has no such field.', '',
              '| Field | Foody | Overture |', '|---|---:|---:|']
    for field in ('name', 'address', 'category', 'price', 'cuisine', 'reviews', 'phone', 'updated', 'status'):
        lines.append(f"| {field} | {profiles['foody']['khanh_hoi']['present'][field]}/{len(fw)} | {profiles['overture']['khanh_hoi']['present'][field]}/{len(ow)} |")
    lines += ['', '## Uniqueness and anomalies (candidate signals only)', '',
              '| Source | Same coordinate ≤6 decimals (groups/rows) | Same normalized name+address (groups/rows) | Repeated name (groups/rows) | Placeholder names |',
              '|---|---:|---:|---:|---:|']
    for source in ('foody', 'overture'):
        s = profiles[source]['khanh_hoi']
        fmt = lambda k: f"{s[k]['groups']}/{s[k]['rows_in_groups']}"
        lines.append(f"| {source} | {fmt('coordinate_collision')} | {fmt('exact_normalized_name_address')} | {fmt('names_duplicated')} | {s['names_blank_or_placeholder']} |")
    lines += ['', 'Coordinates shared across stores may reflect a building or geocoding approximation; names shared across stores may be distinct branches. **Do not deduplicate automatically.**',
              '', '## Categories, spatial and numeric distributions', '']
    for source in ('foody','overture'):
        s = profiles[source]['khanh_hoi']
        lines += [f'### {source.title()}', '',
                  f"- Distinct observed category strings: **{s['distinct_categories']}**; labels are source-specific, not yet mapped into one common F&B taxonomy.",
                  f"- Lat quantiles: `{s['lat_quantiles']}`; lon quantiles: `{s['lon_quantiles']}`.",
                  f"- Snapshot/source metadata time range: `{s['timestamp_min']}` to `{s['timestamp_max']}`. This is **not** business-last-open time.",
                  f"- Review-count numeric quantiles (when typed): `{s['review_count_quantiles']}`; distinct populated price strings: {s['price_examples_count']}.",
                  '', '| Category label | Count |', '|---|---:|']
        for category, n in list(s['category_distribution'].items())[:20]:
            lines.append(f"| {category.replace('|','/')} | {n} |")
        lines.append('')
    fp = profiles['foody']['khanh_hoi']
    op = profiles['overture']['khanh_hoi']
    lines += ['## Source cadence and 5V operational profile', '',
              'File bytes below describe **the selected cached inputs**, not the size of each complete source or all published artifacts.', '',
              '| Dimension | Foody | Overture |', '|---|---|---|',
              f'| Volume (observed) | {len(f):,} distinct input places; {len(fw):,} inside ward; cached state {FOODY.stat().st_size:,} bytes | {len(o):,} F&B input places; {len(ow):,} inside ward; filtered Parquet {OVERTURE.stat().st_size:,} bytes |',
              '| Velocity (measured) | Single collected sample, **no measured daily new/updated/deleted records** | One fixed 2026-10-21.0 release, **no observed release-to-release delta** |',
              f'| Variety | {fp["distinct_categories"]} observed category strings; price, cuisine, displayed review counts | {op["distinct_categories"]} category strings; nested contacts, names, addresses, source metadata |',
              f'| Veracity (observed signals) | {f_dup} duplicate IDs; {fp["names_blank_or_placeholder"]} placeholder names; no independently verified status | {o_dup} duplicate IDs; {op["names_blank_or_placeholder"]} placeholder names; no independently verified status |',
              '| Value (potential) | Cuisine/price/review-count enrichment; marginal verified uplift UNKNOWN | Candidate primary catalog with phone/address; verified coverage and active rate UNKNOWN |',
              '', '### What does "updated" mean?', '',
              f'- Foody `updated` observed range: {fp["timestamp_min"]} to {fp["timestamp_max"]}. Treat as crawler/record metadata until proven to be a source edit timestamp.',
              f'- Overture `updated` observed range: {op["timestamp_min"]} to {op["timestamp_max"]}. A constant timestamp **cannot establish business freshness**.',
              '- Source publication cadence: Foody UNKNOWN from this single crawl; Overture release identifier is known, but release cadence and per-POI change frequency are NOT measured here.',
              '- To measure velocity reproducibly: collect at least two legally usable snapshots per source; compare stable IDs and normalized row fingerprints; report interval days, additions, deletions, changed attributes, and rate per day. Keep source edits separate from crawler observations.',
              '- To measure cumulative volume: inventory the full raw source datasets (bytes/rows) and projected pilot ingest rate; never extrapolate from the first-discovered Foody 1,000.',
              '- To estimate operational value: independent branch/activity validation, rights verification, matched-field enrichment and cost per verified novel POI are still required.', '']
    lines += ['## Overture source lifecycle — evidence for DAG design', '',
              'Source reference: https://docs.overturemaps.org/schema/v1.18.0/reference/places/place/ ; '
              'GERS changelog: https://docs.overturemaps.org/gers/changelog/ ; '
              'download/change artifacts: https://docs.overturemaps.org/getting-data/cloud-sources/.', '',
              f'- Observed in ward: {len(ow)} Overture POIs, {lifecycle["source_links"]} source links; '
              f'{lifecycle["multi_source_pois"]} POIs have multiple source links.',
              f'- Source timestamp missing for {lifecycle["missing_source_update_time"]} POIs; '
              f'future-dated relative to release: {lifecycle["future_timestamp_pois"]}.',
              '- Attribution counts below count distinct POIs per source label. A POI may appear under multiple labels, '
              'so counts must not be summed as unique POIs.', '',
              '| Source dataset label | POIs |', '|---|---:|']
    for label, count in lifecycle['providers'].items():
        lines.append(f'| {label.replace("|", "/")} | {count} |')
    lines += ['', 'Age of latest available `sources[].update_time` relative to the 2026-10-21 release '
              '(source metadata age; **not** last business verification):', '',
              '| Metadata age | POIs |', '|---|---:|']
    for bucket in ('0-30d', '31-90d', '91-180d', '181-365d', '>365d', 'UNKNOWN', 'FUTURE_TIMESTAMP'):
        lines.append(f'| {bucket} | {lifecycle["age_buckets"].get(bucket, 0)} |')
    lines += ['', '**Caution:** Overture-produced source links can have a newer timestamp than upstream '
              'source links. The table above takes the latest of all links, so it overstates upstream recency. '
              'The following table excludes dataset label `Overture` and is better for upstream-source '
              'metadata freshness (still not verified business activity).', '',
              '| Upstream source metadata age | POIs |', '|---|---:|']
    for bucket in ('0-30d', '31-90d', '91-180d', '181-365d', '>365d', 'UNKNOWN', 'FUTURE_TIMESTAMP'):
        lines.append(f'| {bucket} | {lifecycle["upstream_age_buckets"].get(bucket, 0)} |')
    lines += ['', '### Release handling and scheduling contract (proposal, not implemented)', '',
              '1. **Daily lightweight check:** inspect published release identifier. Skip when equal to the last '
              'successfully published version; do not redownload daily.',
              '2. **New release:** pin release ID and query footprint, ingest a raw snapshot to an isolated staging '
              'directory, record hashes and source attribution, validate schema, row counts, geometry, IDs and '
              'licensing before atomic publication.',
              '3. **Record-level reconciliation:** compare old/new same-scope releases by GERS ID and stable '
              'attribute fingerprints: ADDED, REMOVED_FROM_RELEASE, CHANGED, UNCHANGED. '
              'Treat missing IDs as candidate removals, never automatically CLOSED.',
              '4. **Provenance diff separately:** Overture changelog can report UNCHANGED when only `sources` '
              'metadata changes. Compare source arrays independently if provenance freshness matters.',
              '5. **Identity and downstream:** retain old versions and unmatched IDs for review; preserve '
              'branch-level identity before Silver updates; publish Gold only after quality gates pass.',
              '6. **Failure and retries:** no replacement of current snapshot on failed download, validation or '
              'reconciliation; checkpoint release and stage; retry only idempotently.',
              '', 'Actual release-to-release adds/changes/removals, ID stability, source update latency and SLA '
              'remain **UNMEASURED** because only one fixed Overture release is included in this profiling run. '
              'A second version and GERS changelog/bridge audit are required before choosing incremental downloads.', '']
    lines += ['## Foody ingestion scheduling — conditional proposal', '',
              '- Foody is a collected browser snapshot, not a published release feed. No source-level change '
              'cadence, stable update timestamp, completeness SLA or legal retention entitlement has been verified.',
              '- Do not schedule a production Foody crawl until collection rights and pilot scope are confirmed. '
              'If approved, use a bounded manual/pilot schedule with persisted discovery cursor, source IDs, '
              'collection timestamp, failure reason and per-run fingerprint; preserve previously published data.',
              '- Compare snapshots per Foody ID plus normalized attributes. A listing missing from a partial '
              'crawl is UNKNOWN, not deleted or closed. Revalidate branch identity and operating status '
              'independently before Silver/Gold decisions.',
              '- Proposed DAG separation: `check_overture_release` -> conditional Overture ingest/validate/diff '
              '-> atomic publish; separately `foody_rights_scope_gate` -> bounded collection -> DQ/coverage '
              '-> review queue. Only then consider cross-source ER and catalog enrichment.', '']
    lines += ['## Required data-quality dimensions / evidence gaps', '',
              '| Dimension | What is measured | What remains unresolved |', '|---|---|---|',
              '| Schema / integrity | Raw Arrow schema, nested field occurrence, source-ID duplicates | Source contract changes across releases |',
              '| Completeness | Field presence and in-polygon counts | True real-world business coverage and discovery recall |',
              '| Validity | Geometry validity and coordinate bounds, simple placeholders | Address parsing, price normalization, category semantic validation |',
              '| Uniqueness | Same normalized name+address, coordinates, source-ID collisions | Branch-aware false duplicates |',
              '| Distribution | Categories, geo quantiles, review counts | Spatial density by grid and district representativeness |',
              '| Consistency | Both sources clipped to same polygon | Taxonomy alignment; conflict checks against external evidence |',
              '| Timeliness | Metadata timestamp range and fixed Overture release | Actual business update age, open/closed verification |',
              '| Accuracy | No verified ground truth available | Sampled independent address/location/branch verification |',
              '| Provenance | Input SHA-256 and source release | Foody rights and record-level Overture licenses |',
              '| Reliability | Prior crawler logs and state | Repeated-run completion rate and cost per verified POI |',
              '', 'Foody is a **biased first-discovered sample**. Field completeness must not be used as a source coverage rate. No auto-merges or active/closed labels are inferred.',
              '', 'Reference frameworks: DAMA/DQ dimensions (UK Government Data Quality Framework) and Great Expectations missingness, uniqueness, and distribution expectations.',
              'https://www.gov.uk/government/publications/the-government-data-quality-framework/the-government-data-quality-framework',
              'https://docs.greatexpectations.io/docs/reference/learn/data_quality_use_cases/uniqueness/',
              '']
    (output/'05_detailed_data_profile.md').write_text('\n'.join(lines), encoding='utf8')
    manifest = {'executed_at_utc': datetime.now(timezone.utc).isoformat(), 'duration_s': round(time.monotonic()-began, 2),
                'input_sha256': overview['inputs_sha256'],
                'outputs_sha256': {p.name: digest(p) for p in output.iterdir() if p.is_file()}}
    (output/'profile_manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
    print(json.dumps({'output': str(output), 'foody': len(fw), 'overture': len(ow), 'seconds': manifest['duration_s']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).parent/'results'/'20261101-khanh-hoi-profile-detail-v1')
    run(parser.parse_args().output)
