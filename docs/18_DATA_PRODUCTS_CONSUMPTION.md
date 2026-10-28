# 18 — Data Products, Consumer Interface & Delivery

**Version:** 1.0 · **Status:** Proposed · **Related:** `02_PRD.md`, `03_SRS.md` FR-10..18

## 1. Consumer contracts

MVP users are **Data Analyst** (SQL/files) and **Data Operator** (CLI/manifest). A website is not required to validate the catalog. Only **current approved publication** is exposed to default queries; readers may request a historical version explicitly.

| Data product | Grain | Minimum fields | Interface |
|---|---|---|---|
| `store_catalog` | 1 canonical branch per publication | `publication_id`, `store_id`, display name, category, latitude, longitude, match status, source count | Parquet + DuckDB SQL |
| `store_source_links` | 1 source record link per store/publication | store ID, source, source ID, snapshot ID, evidence reference | Parquet + SQL |
| `er_review_queue` | 1 ambiguous candidate pair/run | pair IDs, names/coords, evidence, current review status | CSV/Parquet + CLI |
| `source_coverage_report` | 1 source/category/publication grouping | input/accepted/rejected counts, unique store count and definitions | Parquet/CSV + report |
| `quality_report` | 1 test/metric per run | metric ID, result, numerator/denominator, status, threshold/config | JSON/Parquet |
| `publication_manifest` | 1 publication | input references, hashes, rule versions, outputs and pass/fail | JSON |

## 2. Versioning and access semantics

`current.json` references **one complete publication_id**, whose manifest points to all its file paths. Analysts resolve pointer once at query start and use immutable versioned paths. Do not read `staging/` directly in demos. Schema-breaking changes require contract version bump plus consumers tests; preserve old publication for rollback within retention policy.

## 3. Example queries (illustrative paths)

```sql
-- Current publication path resolved by caller (example placeholder).
SELECT store_id, display_name, category, latitude, longitude
FROM read_parquet('data/gold/publications/<publication_id>/catalog/*.parquet')
WHERE category = 'cafe'
ORDER BY display_name;
```

```sql
-- Multi-source matches, after substituting publication ID.
SELECT store_id, COUNT(DISTINCT source) AS source_count
FROM read_parquet('data/gold/publications/<publication_id>/source_links/*.parquet')
GROUP BY store_id
HAVING COUNT(DISTINCT source) > 1;
```

SQL is an interface contract example; filenames/column names need adjustment to actual implementation; no claim the queries have been executed.

## 4. Product acceptance checks

- Store granularity is branch-level; `store_id` unique within publication.
- Every displayed store has >=1 valid source reference.
- Name and category selections have attribute-level source evidence.
- Uncertain matches are not silently dropped; consumers can inspect review queue.
- Reports state distinct denominator and exact polygon/source/date versions.
- Search returning zero records means "not found in this publication", not "no businesses exist".

## 5. Future API design only when justified

Potential endpoints: `/catalog/stores`, `/catalog/stores/{store_id}`, `/catalog/publications`, `/quality/reports` and later `/recommendations`. Do not implement API until a real consumer needs it; plan pagination, query limits, schema version, rate controls, authorization and rights checks first. Do not expose raw third-party reviews or internal operational metadata by default.

## 6. Handoff and support

Release contains sample SQL, expected output columns, dataset dictionary, change log, quality summary, known limitations and a way to reproduce the inputs. A user should be able to obtain one store’s source lineage without asking original developer. No unsupported promise of city-wide completeness.
