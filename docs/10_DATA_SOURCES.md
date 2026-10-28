# 10 — Data Sources, Scope & Access

**Version:** 1.0 · **Status:** Source design; actual acquisitions not yet verified in this branch\
**Rule:** no source is counted as integrated until real extract + provenance + permission assessment exist.

## 1. Selected and excluded sources

| Source | Role | Access plan | MVP status |
|---|---|---|---|
| **Overture Maps Places** | Primary POI catalog | Official public release, filter by bbox then verified polygon/F&B | Selected; verify release/schema |
| **OpenStreetMap** | Second POI catalog | Versioned regional extract or bounded Overpass, with timestamps and ODbL obligations | Selected; verify extract |
| **Google Maps crawler** | Existing code and potential POI/reviews integration | Existing implementation under repo; access and rights audit before any live run or redistribution | Not MVP live source |
| **TikTok crawler** | Potential future reviews/comments | Only if legal/technical feasibility verified | Out of MVP |
| **Foursquare OS Places** | Previously evaluated | Discontinued due to access/integration friction | **Excluded** |

**Avoid double counting:** Overture may itself include records originating in other public/partner sources. 'Two ingestion channels' does not guarantee fully independent underlying sources; keep available original provenance.

## 2. Geography

- Pilot: **phường Khánh Hội**, TP.HCM; baseline candidate OSM boundary relation `19260589`, administrative code `27265` from earlier exploration, **must independently verify current geometry, history and suitability before use**.
- Historical HCMC bbox `lon 106.55–106.90, lat 10.60–10.95` is **a rough acquisition window, not authoritative city boundary**.
- Use point-in-polygon on validated GeoJSON/GeoParquet, chosen CRS, documented policy `covers`/`contains` including edge points. Store `boundary_id`, `valid_from`, `boundary_sha256`.
- If data source exposes geometry rather than centerpoint, document which representative point is used; uncertain records flagged rather than silently counted.

## 3. F&B inclusion rules

| Type | Initial treatment |
|---|---|
| restaurant, cafe/coffee shop, bakery, fast food, diner, food court, trà sữa, street food | Include when source taxonomy supports |
| catering, grocery, nightclub/bar, food manufacturing, retail supermarket | Review/exclude by agreed business boundary; do not infer from name alone |
| missing/unknown category | Separate `unclassified`; do not silently delete |

**Overture schema:** releases since September 2026 removed legacy `categories`, use `taxonomy` and `basic_category`; source adapter must inspect actual release. **OSM tags:** typical `amenity=restaurant|cafe|fast_food|food_court`, `shop=bakery` etc.; use a versioned mapping file, not hard-coded arbitrary English names only.

## 4. Source ingestion manifest fields

`source_name`, `source_release`, `source_schema_version`, `source_uri`, `acquired_at`, `extract_bbox`, `boundary_version`, `input_file_sha256`, `records_read`, `records_in_polygon`, `records_fnb`, `records_invalid`, `mapping_version`, `license_review_status`, `run_id`, `code_revision`.

If raw source includes PII, licensed metadata or rate-limited API content, track separately and restrict access; do not commit to Git.

## 5. Pre-ingestion source profiling

- Cardinality: total records in source extract, unique source IDs, duplicates in same release.
- Coordinates: missing/null, out-of-range, anomalous duplicate centers, points near border.
- Categorization: mapped F&B, unknowns, counts by original taxonomy/tag and mapping version.
- Text: missing names, language/diacritics, address completeness and strings requiring normalization.
- Cross-source: candidate overlap, verified overlap, each-source-only counts; no 'ground-truth completeness' claim.
- Freshness: publication/release timestamp versus acquisition timestamp; OSM extraction cutoff.

## 6. Rights and constraints

- OSM data uses **ODbL**: comply with attribution, share-alike/derived database obligations where applicable; assess mixing/distribution of canonical database before public release.
- Overture includes multiple data providers/licenses: inspect **per-record/source and release license requirements**; avoid blanket assumption every derived output can be redistributed freely.
- Google/TikTok platform content can have storage, scraping, caching, reuse restrictions; crawler code existing does not grant permission. No bypass of platform gates/restrictions.
- Any review feature/recommendation demo must have a legal dataset or synthetic fixture when rights unclear.

## 7. Handoff checklist for M1

For each source: actual URI/version; extractor command; observed schema; source samples; permission notes; manifest checksums; coordinate system; rate/size limits; full/extract count and acquisition cost. Mark `VERIFIED` only after execution and evidence.

There is **no fixed promise of 682/78k/110M POIs** for current release: previous experiments are historical context and different area/category definitions may lead to different numbers.
