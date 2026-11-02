# Detailed offline data profiling — Khánh Hội

Snapshot evidence; this report does not establish ground-truth accuracy, business activity or district-wide coverage.

## Input lineage and scope

- Foody: first 1,000 discovered detailed URLs; coverage `FIRST_DISCOVERED_URLS_SAMPLE_NOT_DISTRICT_COMPLETE`.
- Overture: release `2026-10-21.0`, strict F&B District 4 Parquet.
- Same current Khánh Hội polygon, point-in-polygon covers boundary. Overture area geometry represented by point for this clip.
- All counts are from the cached files; no live requests. Input hashes and full Arrow schema are in `profile_summary.json`.

## Population, exclusions and fields

| Source | Input distinct IDs | In ward | Outside | Invalid/missing geometry | Duplicate source IDs |
|---|---:|---:|---:|---:|---:|
| foody | 1000 | 557 | 443 | 0 | 0 |
| overture | 1531 | 725 | 806 | 0 | 0 |

Field presence is conditional on **inside-polygon** records; 0% can mean the source mapping has no such field.

| Field | Foody | Overture |
|---|---:|---:|
| name | 557/557 | 725/725 |
| address | 557/557 | 693/725 |
| category | 557/557 | 725/725 |
| price | 504/557 | 0/725 |
| cuisine | 466/557 | 0/725 |
| reviews | 416/557 | 0/725 |
| phone | 0/557 | 611/725 |
| updated | 557/557 | 725/725 |
| status | 0/557 | 0/725 |

## Uniqueness and anomalies (candidate signals only)

| Source | Same coordinate ≤6 decimals (groups/rows) | Same normalized name+address (groups/rows) | Repeated name (groups/rows) | Placeholder names |
|---|---:|---:|---:|---:|
| foody | 6/13 | 0/0 | 0/0 | 0 |
| overture | 4/8 | 0/0 | 7/14 | 1 |

Coordinates shared across stores may reflect a building or geocoding approximation; names shared across stores may be distinct branches. **Do not deduplicate automatically.**

## Categories, spatial and numeric distributions

### Foody

- Distinct observed category strings: **192**; labels are source-specific, not yet mapped into one common F&B taxonomy.
- Lat quantiles: `{'n': 557, 'min': 10.7533019, 'median': 10.7605762, 'p90': 10.76272, 'max': 10.764782}`; lon quantiles: `{'n': 557, 'min': 106.6975687, 'median': 106.7040628, 'p90': 106.7085984, 'max': 106.7107589542866}`.
- Snapshot/source metadata time range: `2026-10-29T07:13:59.399171+00:00` to `2026-10-31T17:20:16.570306+00:00`. This is **not** business-last-open time.
- Review-count numeric quantiles (when typed): `{'n': 416, 'min': 1.0, 'median': 5.5, 'p90': 41.0, 'max': 567.0}`; distinct populated price strings: 239.

| Category label | Count |
|---|---:|
| Quán ăn, Món Việt, Thịt Gà | 32 |
| Café/Dessert, Món Việt, Trà | 27 |
| Quán ăn, Món Việt, Bún | 25 |
| Ăn vặt/vỉa hè, Món Việt, Ăn vặt - Ăn nhẹ | 25 |
| Ăn vặt/vỉa hè, Món Việt, Bánh mì | 15 |
| Quán ăn, Món Việt, Hải sản | 14 |
| Café/Dessert, Món Việt, Café - Kem | 14 |
| Quán ăn, Bún | 13 |
| Quán ăn, Món Việt, Thịt Bò | 12 |
| Ăn vặt/vỉa hè, Món Việt, Thịt Gà | 10 |
| Quán ăn, Món Việt, Món nước | 10 |
| Quán ăn, Món Việt, Bánh mì | 9 |
| Shop Online, Món Việt | 9 |
| Quán ăn, Món Việt, Phở | 9 |
| Ăn vặt/vỉa hè, Món Việt, Hải sản | 9 |
| Shop/Cửa hàng | 9 |
| Ăn vặt/vỉa hè, Món Việt, Trà | 7 |
| Café/Dessert, Trà chanh | 7 |
| Quán ăn, Ăn vặt - Ăn nhẹ | 7 |
| Quán ăn, Món Việt, Bánh - Kẹo | 6 |

### Overture

- Distinct observed category strings: **47**; labels are source-specific, not yet mapped into one common F&B taxonomy.
- Lat quantiles: `{'n': 725, 'min': 10.752934041288526, 'median': 10.76021, 'p90': 10.7627253, 'max': 10.7648497}`; lon quantiles: `{'n': 725, 'min': 106.6973994, 'median': 106.70266457, 'p90': 106.70692658, 'max': 106.71091235}`.
- Snapshot/source metadata time range: `2026-10-15T18:00:21Z` to `2026-10-15T18:00:21Z`. This is **not** business-last-open time.
- Review-count numeric quantiles (when typed): `None`; distinct populated price strings: 0.

| Category label | Count |
|---|---:|
| restaurant | 181 |
| coffee_shop | 109 |
| diner | 82 |
| bakery | 49 |
| vietnamese_restaurant | 41 |
| cafe | 38 |
| bubble_tea_shop | 28 |
| food_and_drink | 20 |
| fast_food_restaurant | 19 |
| seafood_restaurant | 17 |
| vegetarian_restaurant | 12 |
| asian_restaurant | 12 |
| chinese_restaurant | 12 |
| food_truck_stand | 7 |
| ramen_restaurant | 7 |
| japanese_restaurant | 6 |
| chicken_restaurant | 6 |
| thai_restaurant | 6 |
| barbecue_restaurant | 6 |
| bar | 6 |

## Source cadence and 5V operational profile

File bytes below describe **the selected cached inputs**, not the size of each complete source or all published artifacts.

| Dimension | Foody | Overture |
|---|---|---|
| Volume (observed) | 1,000 distinct input places; 557 inside ward; cached state 2,363,143 bytes | 1,531 F&B input places; 725 inside ward; filtered Parquet 205,591 bytes |
| Velocity (measured) | Single collected sample, **no measured daily new/updated/deleted records** | One fixed 2026-10-21.0 release, **no observed release-to-release delta** |
| Variety | 192 observed category strings; price, cuisine, displayed review counts | 47 category strings; nested contacts, names, addresses, source metadata |
| Veracity (observed signals) | 0 duplicate IDs; 0 placeholder names; no independently verified status | 0 duplicate IDs; 1 placeholder names; no independently verified status |
| Value (potential) | Cuisine/price/review-count enrichment; marginal verified uplift UNKNOWN | Candidate primary catalog with phone/address; verified coverage and active rate UNKNOWN |

### What does "updated" mean?

- Foody `updated` observed range: 2026-10-29T07:13:59.399171+00:00 to 2026-10-31T17:20:16.570306+00:00. Treat as crawler/record metadata until proven to be a source edit timestamp.
- Overture `updated` observed range: 2026-10-15T18:00:21Z to 2026-10-15T18:00:21Z. A constant timestamp **cannot establish business freshness**.
- Source publication cadence: Foody UNKNOWN from this single crawl; Overture release identifier is known, but release cadence and per-POI change frequency are NOT measured here.
- To measure velocity reproducibly: collect at least two legally usable snapshots per source; compare stable IDs and normalized row fingerprints; report interval days, additions, deletions, changed attributes, and rate per day. Keep source edits separate from crawler observations.
- To measure cumulative volume: inventory the full raw source datasets (bytes/rows) and projected pilot ingest rate; never extrapolate from the first-discovered Foody 1,000.
- To estimate operational value: independent branch/activity validation, rights verification, matched-field enrichment and cost per verified novel POI are still required.

## Overture source lifecycle — evidence for DAG design

Source reference: https://docs.overturemaps.org/schema/v1.18.0/reference/places/place/ ; GERS changelog: https://docs.overturemaps.org/gers/changelog/ ; download/change artifacts: https://docs.overturemaps.org/getting-data/cloud-sources/.

- Observed in ward: 725 Overture POIs, 1450 source links; 725 POIs have multiple source links.
- Source timestamp missing for 0 POIs; future-dated relative to release: 0.
- Attribution counts below count distinct POIs per source label. A POI may appear under multiple labels, so counts must not be summed as unique POIs.

| Source dataset label | POIs |
|---|---:|
| Overture | 725 |
| meta | 724 |
| AllThePlaces | 1 |

Age of latest available `sources[].update_time` relative to the 2026-10-21 release (source metadata age; **not** last business verification):

| Metadata age | POIs |
|---|---:|
| 0-30d | 725 |
| 31-90d | 0 |
| 91-180d | 0 |
| 181-365d | 0 |
| >365d | 0 |
| UNKNOWN | 0 |
| FUTURE_TIMESTAMP | 0 |

**Caution:** Overture-produced source links can have a newer timestamp than upstream source links. The table above takes the latest of all links, so it overstates upstream recency. The following table excludes dataset label `Overture` and is better for upstream-source metadata freshness (still not verified business activity).

| Upstream source metadata age | POIs |
|---|---:|
| 0-30d | 637 |
| 31-90d | 88 |
| 91-180d | 0 |
| 181-365d | 0 |
| >365d | 0 |
| UNKNOWN | 0 |
| FUTURE_TIMESTAMP | 0 |

### Release handling and scheduling contract (proposal, not implemented)

1. **Daily lightweight check:** inspect published release identifier. Skip when equal to the last successfully published version; do not redownload daily.
2. **New release:** pin release ID and query footprint, ingest a raw snapshot to an isolated staging directory, record hashes and source attribution, validate schema, row counts, geometry, IDs and licensing before atomic publication.
3. **Record-level reconciliation:** compare old/new same-scope releases by GERS ID and stable attribute fingerprints: ADDED, REMOVED_FROM_RELEASE, CHANGED, UNCHANGED. Treat missing IDs as candidate removals, never automatically CLOSED.
4. **Provenance diff separately:** Overture changelog can report UNCHANGED when only `sources` metadata changes. Compare source arrays independently if provenance freshness matters.
5. **Identity and downstream:** retain old versions and unmatched IDs for review; preserve branch-level identity before Silver updates; publish Gold only after quality gates pass.
6. **Failure and retries:** no replacement of current snapshot on failed download, validation or reconciliation; checkpoint release and stage; retry only idempotently.

Actual release-to-release adds/changes/removals, ID stability, source update latency and SLA remain **UNMEASURED** because only one fixed Overture release is included in this profiling run. A second version and GERS changelog/bridge audit are required before choosing incremental downloads.

## Foody ingestion scheduling — conditional proposal

- Foody is a collected browser snapshot, not a published release feed. No source-level change cadence, stable update timestamp, completeness SLA or legal retention entitlement has been verified.
- Do not schedule a production Foody crawl until collection rights and pilot scope are confirmed. If approved, use a bounded manual/pilot schedule with persisted discovery cursor, source IDs, collection timestamp, failure reason and per-run fingerprint; preserve previously published data.
- Compare snapshots per Foody ID plus normalized attributes. A listing missing from a partial crawl is UNKNOWN, not deleted or closed. Revalidate branch identity and operating status independently before Silver/Gold decisions.
- Proposed DAG separation: `check_overture_release` -> conditional Overture ingest/validate/diff -> atomic publish; separately `foody_rights_scope_gate` -> bounded collection -> DQ/coverage -> review queue. Only then consider cross-source ER and catalog enrichment.

## Required data-quality dimensions / evidence gaps

| Dimension | What is measured | What remains unresolved |
|---|---|---|
| Schema / integrity | Raw Arrow schema, nested field occurrence, source-ID duplicates | Source contract changes across releases |
| Completeness | Field presence and in-polygon counts | True real-world business coverage and discovery recall |
| Validity | Geometry validity and coordinate bounds, simple placeholders | Address parsing, price normalization, category semantic validation |
| Uniqueness | Same normalized name+address, coordinates, source-ID collisions | Branch-aware false duplicates |
| Distribution | Categories, geo quantiles, review counts | Spatial density by grid and district representativeness |
| Consistency | Both sources clipped to same polygon | Taxonomy alignment; conflict checks against external evidence |
| Timeliness | Metadata timestamp range and fixed Overture release | Actual business update age, open/closed verification |
| Accuracy | No verified ground truth available | Sampled independent address/location/branch verification |
| Provenance | Input SHA-256 and source release | Foody rights and record-level Overture licenses |
| Reliability | Prior crawler logs and state | Repeated-run completion rate and cost per verified POI |

Foody is a **biased first-discovered sample**. Field completeness must not be used as a source coverage rate. No auto-merges or active/closed labels are inferred.

Reference frameworks: DAMA/DQ dimensions (UK Government Data Quality Framework) and Great Expectations missingness, uniqueness, and distribution expectations.
https://www.gov.uk/government/publications/the-government-data-quality-framework/the-government-data-quality-framework
https://docs.greatexpectations.io/docs/reference/learn/data_quality_use_cases/uniqueness/
