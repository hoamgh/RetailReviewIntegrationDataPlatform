# 17 — Reviews, Features & Recommendation (Future Releases)

**Version:** 1.0 · **Status:** Future design; not an MVP dependency\
**Maps to:** `BR-09/10`, `PF-12..15`, `FR-20..23`

## 1. Boundaries and dependencies

The initial product is a **verified canonical store catalog**. Review ingestion requires (a) source rights and documented permitted operations, (b) approved association from review-platform place ID to correct canonical branch, and (c) reliable incremental/replay strategy. If rights are unavailable, work on synthetic or properly licensed fixtures without turning on live Google/TikTok crawlers.

## 2. Data entities and grain

| Data set | Grain | Purpose |
|---|---|---|
| `review_observation` (Bronze) | One collected observation for `(platform, review_id, observed_at or ingest_event_id)` | Preserve what was seen; duplicates across snapshots expected |
| `review_current` (Silver) | One current version per `(platform, review_id)` | Select latest according to documented source update/observation precedence |
| `review_store_link` | One review to one canonical store decision/version | Track link confidence, unresolved matches |
| `review_feature_evidence` | One review × feature/aspect × extraction version | Store evidence and uncertainty; respect rights |
| `store_feature_snapshot` | One store × snapshot date × feature version | Aggregate counts, scores, confidence/coverage |
| `recommendation_eval` | One query × candidate store × method × eval version | Comparable rankings and labels |

**Do not treat** raw append-only duplicates as distinct current reviews. Missing source timestamp cannot be silently replaced with collected_at; define fallback semantics and confidence. The absence of a review during a crawl does not necessarily prove deletion. Brand-level review association is insufficient for a branch-level store unless verified.

## 3. Ingestion reliability (only approved sources)

- Reuse vetted `crawl_experiment/` modules after M0 audit, or build an explicit adapter; review existing JobStore, SQLite, publication manifests and checkpoint rules.
- Separate **job scheduling/lease** from **published review observations**. Worker attempts may run at least once; downstream selects current deduplicated grain.
- Write append-only observation batches to temporary Parquet, validate counts, atomically publish batch+manifest and reconcile job receipt. Record attempt/fencing generation and refuse stale writer acknowledgments.
- Bounded retry for transient faults; source access restriction triggers **STOP/approval**, not workaround.

## 4. Review features

For text available under license, extract `food`, `service`, `ambience`, `price`, `wait_time` where supported. Record feature taxonomy version, extraction method/model version, text evidence reference, review count, time window, confidence and language coverage. No evidence → `unknown` rather than zero/negative. Validate manual sample by aspect and language; compare dictionary/rules baseline before introducing an LLM.

## 5. Recommendation baseline and evaluation

Start with **baseline** using distance, category, completeness/available non-review fields. Review-aware variant adds permitted features only. For identical queries and candidate sets evaluate Precision@K / NDCG@K or appropriate ranking labels, coverage, cold-start and per-category failure modes. Explainability: show factors used and uncertainty; do not invent food quality from absent reviews. Avoid using external ratings if rights forbid storage/use.

## 6. Data product / UI scope

MVP: SQL/CLI catalog query and provenance; later optional API/UI with filters, store detail, evidence, rec explanations. Public service needs separate privacy, cache and redistributability review. No external booking/orders/payment capabilities.

## 7. Exit gates

Release 2: approved rights, source-to-store linkage samples, review identity replay, no duplicate current reviews, failure/recovery evidence, feature extraction quality evaluation. Release 3: defined evaluation set, baseline comparison, explainability, costs, limitations. Any missing gate → mark BLOCKED, not "done".
