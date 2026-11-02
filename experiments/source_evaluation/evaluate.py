"""Offline, evidence-conservative F&B source evaluation. No network or source writes."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import random
import re
import time
import unicodedata

import pyarrow as pa
import pyarrow.parquet as pq
from shapely import from_wkb
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[2]
FOODY = ROOT / 'output/foody-q4-detail-first1000-v1/state.json'
OVERTURE = ROOT / 'output/district4-profile/runs/15d25e5b4b9e6f1c66e9/overture_fnb_district4.parquet'
WARD = ROOT / 'config/admin_areas/phuong_khanh_hoi.geojson'
BRIDGE_F = ROOT / 'output/google-resolution-foody-first400-20261101-v1/foody_mapping.parquet'
BRIDGE_O = ROOT / 'output/google-resolution-parallel-20261031-v1/overture_mapping.parquet'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def text(value):
    return str(value or '').strip()


def norm(value):
    value = unicodedata.normalize('NFD', text(value).lower())
    value = ''.join(c for c in value if unicodedata.category(c) != 'Mn').replace('đ', 'd')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', value)).strip()


def similarity(a, b):
    a, b = norm(a), norm(b)
    return round(SequenceMatcher(None, a, b).ratio(), 4) if a and b else None


def first(value):
    return next((str(v) for v in (value or []) if v), '')


def numbers(address):
    return re.match(r'^\s*(\d+[a-zA-Z]?(?:/\d+[a-zA-Z]?)*)\b', norm(address))


def branch_conflict(a, b):
    x, y = numbers(a), numbers(b)
    return bool(x and y and x.group(1) != y.group(1))


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = map(float, (a['lat'], a['lon'], b['lat'], b['lon']))
    p = math.pi / 180
    dlat, dlon = (lat2-lat1)*p, (lon2-lon1)*p
    v = math.sin(dlat/2)**2 + math.cos(lat1*p)*math.cos(lat2*p)*math.sin(dlon/2)**2
    return round(12742000 * math.asin(min(1, math.sqrt(v))), 2)


def valid_coords(lat, lon):
    return (isinstance(lat, (float, int)) and isinstance(lon, (float, int))
            and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)


def foody_rows(state):
    rows = []
    for record in state.get('records', {}).values():
        fields = record.get('fields') or {}
        cats = record.get('categories') or {}
        category = ', '.join(first(cats.get(k)) for k in ('Thể loại', 'Phong cách ẩm thực', 'Phục vụ các món') if cats.get(k))
        rows.append(dict(id=text(record.get('source_id')), name=text(record.get('name')),
                         address=first(fields.get('streetAddress')), category=category,
                         price=first(fields.get('priceRange')), cuisine=first(fields.get('servesCuisine')),
                         reviews=record.get('review_count_displayed'), phone=None,
                         lat=record.get('latitude'), lon=record.get('longitude'),
                         status=None, updated=(record.get('provenance') or {}).get('retrieved_at'),
                         geometry_valid=record.get('geometry_status') == 'VALID',
                         raw_category=cats, url=record.get('url')))
    return rows


def overture_rows(table):
    rows = []
    for r in table.to_pylist():
        geom = from_wkb(r['geometry']) if r['geometry'] else None
        pt = geom if geom is not None and geom.geom_type == 'Point' else geom.representative_point() if geom is not None and geom.is_valid and not geom.is_empty else None
        addresses = r.get('addresses') or []
        taxonomy = r.get('taxonomy') or {}
        updated = [x.get('update_time') for x in r.get('sources') or [] if x.get('update_time')]
        rows.append(dict(id=text(r.get('id')), name=text((r.get('names') or {}).get('primary')),
                         address=text(addresses[0].get('freeform')) if addresses else '',
                         category=text(taxonomy.get('primary') or r.get('basic_category')),
                         price=None, cuisine=None, reviews=None,
                         phone=first(r.get('phones')), lat=pt.y if pt else None, lon=pt.x if pt else None,
                         status=r.get('operating_status'), updated=max(updated, default=None),
                         geometry_valid=bool(geom is not None and geom.is_valid and not geom.is_empty),
                         raw_category=taxonomy, url=first(r.get('websites'))))
    return rows


def dedup(rows):
    seen, duplicates, missing = {}, 0, 0
    for row in rows:
        key = row['id']
        if not key:
            missing += 1
            continue
        if key in seen:
            duplicates += 1
            continue
        seen[key] = row
    return list(seen.values()), duplicates, missing


def clip(rows, polygon):
    inside, counts = [], Counter()
    for r in rows:
        if not valid_coords(r['lat'], r['lon']):
            counts['missing_or_invalid_coordinates'] += 1
        elif not r['geometry_valid']:
            counts['invalid_geometry'] += 1
        elif polygon.covers(Point(r['lon'], r['lat'])):
            inside.append(r)
        else:
            counts['outside_polygon'] += 1
    return inside, dict(counts)


def stats(rows):
    n = len(rows)
    attrs = ('name', 'address', 'category', 'price', 'cuisine', 'reviews', 'phone', 'updated', 'status')
    return dict(rows=n, present={k: sum(r.get(k) is not None and text(r.get(k)) != '' for r in rows) for k in attrs},
                coordinates_present=sum(valid_coords(r['lat'], r['lon']) for r in rows),
                category_distribution=dict(Counter(r['category'] or '<MISSING>' for r in rows).most_common(25)),
                operating_status=dict(Counter(text(r['status']) or 'UNKNOWN' for r in rows)))


def candidates(foody, overture):
    # Grid in degrees, with neighboring bins; a 250 m radius is bounded safely
    # by 3 cells in each direction at HCMC latitude.
    grid = defaultdict(list)
    cell = 0.001
    for row in overture:
        grid[(int(math.floor(row['lat']/cell)), int(math.floor(row['lon']/cell)))].append(row)
    pairs = []
    for f in foody:
        center = (int(math.floor(f['lat']/cell)), int(math.floor(f['lon']/cell)))
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                for o in grid.get((center[0]+dy, center[1]+dx), []):
                    d = distance_m(f, o)
                    ns, ads = similarity(f['name'], o['name']), similarity(f['address'], o['address'])
                    paths = []
                    if d <= 25: paths.append('NEAR_25M')
                    if d <= 250 and ns is not None and ns >= .7: paths.append('NAME_WITHIN_250M')
                    if d <= 100 and ads is not None and ads >= .75: paths.append('ADDRESS_WITHIN_100M')
                    if not paths:
                        continue
                    conflict = branch_conflict(f['address'], o['address'])
                    # Source taxonomies differ; field presence cannot prove compatibility.
                    category_compatible = None
                    score = round((ns or 0)*.55 + (ads or 0)*.2 + max(0, 1-d/250)*.25, 4)
                    # No auto-MATCH: identity requires independent reviewer labels.
                    reason = '|'.join(paths)
                    if conflict: reason += '|HOUSE_NUMBER_CONFLICT'
                    if not f['category'] or not o['category']: reason += '|CATEGORY_MISSING'
                    pairs.append(dict(foody_id=f['id'], overture_id=o['id'], foody_name=f['name'], overture_name=o['name'],
                                      foody_address=f['address'], overture_address=o['address'],
                                      foody_lat=f['lat'], foody_lon=f['lon'], overture_lat=o['lat'], overture_lon=o['lon'],
                                      foody_category=f['category'], overture_category=o['category'], distance_m=d,
                                      name_similarity=ns, address_similarity=ads, category_compatible=category_compatible,
                                      branch_conflict=conflict, score=score, blocking_reason=reason,
                                      decision='REVIEW_REQUIRED' if score >= .5 and not conflict else 'NON_MATCH',
                                      identity_verified=False))
    pairs.sort(key=lambda r: (r['foody_id'], -r['score'], r['overture_id']))
    return pairs


def sample_review(foody, overture, pairs, seed=20261101):
    by_f = defaultdict(list)
    for p in pairs:
        by_f[p['foody_id']].append(p)
    strong = [r for r in foody if any(p['score'] >= .8 and not p['branch_conflict'] for p in by_f[r['id']])]
    ambiguous = [r for r in foody if by_f[r['id']] and r not in strong]
    unmatched = [r for r in foody if not by_f[r['id']]]
    confident_o_ids = {p['overture_id'] for p in pairs if p['score'] >= .8 and not p['branch_conflict']}
    ov_remaining = [r for r in overture if r['id'] not in confident_o_ids]
    rng = random.Random(seed)
    reviews = []
    sizes = {}
    for label, group in [('FOODY_STRONG_CANDIDATE', strong), ('FOODY_AMBIGUOUS', ambiguous),
                         ('FOODY_NO_CANDIDATE', unmatched), ('OVERTURE_NO_CONFIDENT_MATCH', ov_remaining)]:
        sizes[label] = dict(population=len(group), sampled=min(50, len(group)))
        for row in rng.sample(sorted(group, key=lambda r: r['id']), min(50, len(group))):
            best = by_f[row['id']][0] if label.startswith('FOODY') and by_f[row['id']] else None
            reviews.append(dict(stratum=label, source_id=row['id'], source_name=row['name'], source_address=row['address'],
                                source_lat=row['lat'], source_lon=row['lon'], candidate_id=best['overture_id'] if best else '',
                                candidate_name=best['overture_name'] if best else '', candidate_address=best['overture_address'] if best else '',
                                candidate_distance_m=best['distance_m'] if best else '', candidate_score=best['score'] if best else '',
                                blocking_reason=best['blocking_reason'] if best else '', identity_review_decision='',
                                operating_status_review_decision='', evidence_source='', evidence_date='', reviewer_notes=''))
    return reviews, sizes


def bridge_audit():
    if not BRIDGE_F.exists() or not BRIDGE_O.exists():
        return {'status': 'MISSING_BRIDGE_FILES'}
    fs, os_ = pq.read_table(BRIDGE_F).to_pylist(), pq.read_table(BRIDGE_O).to_pylist()
    by_id = defaultdict(lambda: [set(), set()])
    for index, rows in enumerate((fs, os_)):
        for r in rows:
            identity = text(r.get('google_identity_value'))
            if identity:
                by_id[identity.lower()][index].add(text(r.get('source_id')))
    collisions = sorted([(key, len(a), len(b), len(a)*len(b)) for key, (a,b) in by_id.items() if a and b],
                        key=lambda t: -t[3])
    return dict(status='PROVISIONAL_GOOGLE_SEARCH_IDENTIFIERS', shared_ids=len(collisions),
                pair_count=sum(x[3] for x in collisions), worst_collisions=[dict(identifier_sha256=hashlib.sha256(x[0].encode()).hexdigest()[:12],
                        foody=x[1], overture=x[2], pairs=x[3]) for x in collisions[:8]],
                root_cause='many-to-many reuse of captured search identity; precise crawler selection cause unverified')


def markdown_table(stats_by_source, field):
    return '\n'.join(f'| {label} | {s[field]} |' for label, s in stats_by_source.items())


def run(out):
    started = time.monotonic()
    inputs = {k: digest(v) for k,v in [('foody_state',FOODY), ('overture_parquet',OVERTURE), ('ward_polygon',WARD)]}
    ward_doc = json.loads(WARD.read_text(encoding='utf8'))
    polygon = shape(ward_doc['features'][0]['geometry'] if ward_doc.get('type') == 'FeatureCollection' else ward_doc['geometry'])
    state = json.loads(FOODY.read_text(encoding='utf8'))
    f_raw = foody_rows(state)
    o_raw = overture_rows(pq.read_table(OVERTURE))
    f_unique, f_dup, f_missing = dedup(f_raw)
    o_unique, o_dup, o_missing = dedup(o_raw)
    f, f_exc = clip(f_unique, polygon)
    o, o_exc = clip(o_unique, polygon)
    pairs = candidates(f,o)
    reviews, strata = sample_review(f,o,pairs)
    by_f = Counter(p['foody_id'] for p in pairs)
    by_o = Counter(p['overture_id'] for p in pairs)
    stats_f, stats_o = stats(f), stats(o)
    bridge = bridge_audit()
    manifest = dict(run_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic()-started,2),
                    inputs=inputs, scope='Phuong Khanh Hoi polygon, EPSG:4326, polygon.covers(point)',
                    foody_discovery=state.get('coverage'),
                    source_counts=dict(foody=dict(raw=len(f_raw),unique=len(f_unique),duplicates=f_dup,missing_ids=f_missing,inside=len(f),excluded=f_exc),
                                       overture=dict(raw=len(o_raw),unique=len(o_unique),duplicates=o_dup,missing_ids=o_missing,inside=len(o),excluded=o_exc)),
                    candidates=dict(pairs=len(pairs),foody_with_candidate=len(by_f),overture_with_candidate=len(by_o),
                                    possible_many_to_many=sum(v>1 for v in by_f.values()),
                                    review_required=sum(p['decision']=='REVIEW_REQUIRED' for p in pairs),
                                    non_match_provisional=sum(p['decision']=='NON_MATCH' for p in pairs)),
                    strata=strata, bridge=bridge, config=dict(candidate_radius_m=25,name_extension_radius_m=250,name_extension_similarity=.7,
                                                               address_extension_radius_m=100,address_extension_similarity=.75,
                                                               review_score=.5, strong_sample_score=.8, seed=20261101))
    out.mkdir(parents=True, exist_ok=True)
    import csv
    with (out/'quality_metrics.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.writer(stream);writer.writerow(['source','scope','metric','count','denominator','rate'])
        for label, s in [('foody',stats_f),('overture',stats_o)]:
            for attr, count in s['present'].items():
                writer.writerow([label,'khanh_hoi',f'{attr}_present',count,s['rows'],round(count/s['rows'],4) if s['rows'] else ''])
            writer.writerow([label,'khanh_hoi','coordinates_valid',s['coordinates_present'],s['rows'],round(s['coordinates_present']/s['rows'],4) if s['rows'] else ''])
    with (out/'manual_review.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(reviews[0]) if reviews else ['stratum']);writer.writeheader();writer.writerows(reviews)
    schema=pa.schema([(name, pa.string()) for name in ('foody_id','overture_id','foody_name','overture_name','foody_address','overture_address','foody_category','overture_category','blocking_reason','decision')] +
                     [(name,pa.float64()) for name in ('foody_lat','foody_lon','overture_lat','overture_lon','distance_m','name_similarity','address_similarity','score')] +
                     [(name,pa.bool_()) for name in ('category_compatible','branch_conflict','identity_verified')])
    pq.write_table(pa.Table.from_pylist(pairs,schema=schema),out/'candidate_pairs.parquet',compression='zstd')
    (out/'run_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    inventory=f'''# Source inventory — observed offline\n\n| Source | Original rows | Unique IDs | Inside Khánh Hội | Excluded |\n|---|---:|---:|---:|---:|\n| Foody first-discovered detail sample | {len(f_raw)} | {len(f_unique)} | {len(f)} | {f_exc} |\n| Overture strict F&B district 4 | {len(o_raw)} | {len(o_unique)} | {len(o)} | {o_exc} |\n\nOverture fixed release: 2026-10-21.0; geography from historical District 4 polygon-filtered Parquet, clipped again to current Khánh Hội polygon. Foody snapshot: detail extraction during 2026-10-29 to 2026-11-01 (record timestamps may vary); sampled first 1,000 discovered URLs, coverage `{state.get('coverage')}`. Foody listing discovery incomplete, 39 unresolved street entries in earlier state. The two sources were filtered to the same current ward footprint; Overture F&B definition is the existing strict District 4 classification, Foody category labels are retained as source labels and not fully taxonomy-aligned. Point-in-polygon uses `covers`, including boundaries. Areas from Overture use a representative point; original geometry remains untouched.\n\nInput hashes and actual paths recorded in `run_manifest.json`. Foody source status and Overture source operating status are not independent proof that a business is open.\n'''
    quality=f'''# Independent source quality\n\n| Metric (Khánh Hội) | Foody | Overture |\n|---|---:|---:|\n| POIs | {len(f)} | {len(o)} |\n'''
    for key in stats_f['present']:
        quality+=f"| {key} present | {stats_f['present'][key]}/{len(f)} | {stats_o['present'][key]}/{len(o)} |\n"
    quality+=f'''\nSource-ID duplicate observations: Foody {f_dup}; Overture {o_dup}. Missing IDs: {f_missing}/{o_missing}. Invalid/missing coordinates are included in exclusions in inventory. Coordinate inclusion follows geometric validity checks and same polygon; completeness rates are conditional on this clip. Within-source suspicious naming/coordinate duplicates were not adjudicated; branches are preserved. Optional data availability measures field presence, not factual accuracy.\n\nFoody categories (top 25): `{json.dumps(stats_f['category_distribution'],ensure_ascii=False)}`. Overture categories (top 25): `{json.dumps(stats_o['category_distribution'],ensure_ascii=False)}`. Potential non-F&B listings and schema/category compatibility need sampled validation.\n\nNo independently verified ACTIVE/CLOSED labels: all business activity classifications remain UNKNOWN. Foody retrieval timestamps are collection times, not business update times; Overture provenance update_time is source-record update metadata, not proof of operating status.\n\nOperational: Foody pagination stopped/duplicated around page 51, historical access/navigation failures and partial checkpoints; no new crawl performed. Overture release is repeatable local Parquet; source generation cost, marginal engineering hours, access rights and future availability not measured: UNKNOWN. Foody collection/storage/commercial reuse authorization remains UNVERIFIED. Overture per-source licensing must be honored using original `sources` fields; no blanket license claim for every record.\n'''
    audit=f'''# Direct matching audit — provisional\n\n{len(f)} Foody vs {len(o)} Overture POIs inside identical ward polygon. Generated {len(pairs)} candidate pairs; {len(by_f)} Foody and {len(by_o)} Overture IDs have one or more candidate. {sum(v>1 for v in by_f.values())} Foody IDs have multiple candidates. Candidate paths: distance <=25m, name similarity >=0.7 within 250m, or address similarity >=0.75 within 100m (OR logic). House-number conflicts flagged; no cross-record transitive merges. `NON_MATCH` is a provisional candidate rejection, `REVIEW_REQUIRED` needs human evidence; **0 confirmed MATCH labels**. All thresholds are project-specific screening assumptions requiring validation; outside-polygon candidates were excluded, so edge misses remain possible.\n\nGoogle bridge audit (independent of direct matching): `{json.dumps(bridge,ensure_ascii=False)}`. Shared Google search identity can collide across 3×16 or other many-to-many entities, multiplying pairs without validating stores. Root cause of repeated assignment at browser selection level cannot be proven from Parquet alone.\n\nMatching precision and blocking recall: NOT YET VERIFIED (no independent labeled sample). Auto-labeling by shared Google ID, source name or proximity is prohibited.\n'''
    complement=f'''# Complementarity\n\nObserved within the selected ward: Foody {len(f)}; Overture {len(o)}. Direct proximity/name candidate pairs {len(pairs)}; Foody without any generated candidate {len(f)-len(by_f)}; Overture without any generated candidate {len(o)-len(by_o)}. **These are candidates and blocking omissions, not confirmed unique businesses.** 0 independently verified matches, 0 verified active Foody-only additions; actual verified catalog uplift, overlap rate, precision, recall and closed/stale rate: UNKNOWN. Operating status UNKNOWN for every record without independent evidence.\n\nAttribute enrichment is potential only: Foody price {stats_f['present']['price']}/{len(f)}, cuisine {stats_f['present']['cuisine']}/{len(f)}, review-count signal {stats_f['present']['reviews']}/{len(f)}; no review text was ingested in this evaluation. Overture websites/phones and source attribution remain potentially useful. Verified enrichment on matched pairs: UNKNOWN pending manual review.\n\nStratified manual-review population/sample sizes: `{json.dumps(strata,ensure_ascii=False)}`. Labels in `manual_review.csv` deliberately blank. No extrapolation to District 4 or HCMC because Foody sampling is discovery-order biased and incomplete. Acquisition/validation cost per verified addition: UNKNOWN.\n'''
    decision=f'''# Source selection decision\n\n**Decision: INCONCLUSIVE** (current evidence). Overture is the project-preferred reproducible baseline; it is not ground truth. Foody contains potentially useful price, cuisine and displayed review-count fields, but there are no independently verified active novel POIs or validated enrichment joins.\n\n| Dimension | Overture | Foody |\n|---|---|---|\n| Scope | 2026-10-21.0 District 4 strict F&B, re-clipped to Khánh Hội: {len(o)} | first 1,000 detailed discovery URLs, re-clipped: {len(f)} |\n| Quality | independent metrics in 002 | independent metrics in 002 |\n| Freshness | record provenance update timestamps where present; active status unverified | collection timestamps; active status unverified |\n| Attributes | names, taxonomy, address, optional contact and source provenance | names, address, price/cuisine/review-count signals |\n| Matching | {len(pairs)} candidate pairs jointly; no verified overlap | same unresolved entity decisions |\n| Reliability | repeatable local Parquet; extraction/API reliability not measured here | discovery incomplete; pagination/access failures recorded |\n| Cost | UNKNOWN | UNKNOWN |\n| Rights | record-specific source licenses require review | collection/storage/commercial rights UNVERIFIED |\n\nRisks: wrong branch/entity joins, stale listings, Foody discovery bias, unknown operating status, rights limitations, and browser maintenance effort. No arbitrary threshold or automatic production ingestion decision is supported.\n\n1. Independently label the review queue, including branch identity and open/closed/unknown status with source and date.\n2. Resolve Foody permitted storage/commercial use and Overture record-level attribution obligations.\n3. Once labels exist, quantify validated uplift/enrichment and maintenance effort using a comparable, less biased geography sample.\n'''
    for filename, content in [('01_source_inventory.md',inventory),('02_quality_profile.md',quality),('matching_audit.md',audit),
                              ('03_complementarity_report.md',complement),('04_source_selection_decision.md',decision)]:
        (out/filename).write_text(content,encoding='utf8')
    manifest['output_sha256'] = {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != 'run_manifest.json'}
    (out/'run_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(output=str(out),foody=len(f),overture=len(o),candidates=len(pairs),reviews=len(reviews),decision='INCONCLUSIVE'),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'experiments/source_evaluation/results/20261101-khanh-hoi-v1')
    run(parser.parse_args().out)
