# F&B Data Platform — Documentation Index

**Status:** Living design documents · **Target branch:** `feature/fnb-data-platform`\
**Usage:** Put `05_...`–`20_...` in `docs/` next to the already created `01_BRD.md`–`04_USE_CASES.md`. Use `docs/_delivery/STATUS.md` and `docs/_delivery/CURRENT_TASK.md` as the current checkpoints. **Do not overwrite existing source or `AGENTS.md`.**

## Product requirements (existing docs, unchanged)

- `01_BRD.md` — Why / business scope
- `02_PRD.md` — Product capabilities and releases
- `03_SRS.md` — Testable FR/NFR/acceptance criteria
- `04_USE_CASES.md` — Actor flows, exceptions, manual decisions

## Engineering & delivery (new docs)

| File | Purpose | Use first when... |
|---|---|---|
| `05_SYSTEM_ARCHITECTURE.md` | Components, data/control planes | Reviewing system structure |
| `06_DATA_MODEL.md` | Grain, keys, source link, contracts, modeling | Designing Silver/Gold tables |
| `07_PIPELINE_DESIGN.md` | Ingestion → ER → publish, idempotency/recovery | Implementing batch pipeline |
| `08_TEST_PLAN.md` | Fixtures, E2E gates, evaluation | Writing tests |
| `09_ROADMAP.md` | M0–M7 outcomes and exit criteria | Planning sprints/tasks |
| `10_DATA_SOURCES.md` | Overture/OSM scope, geography, rights | Ingesting real data |
| `11_ENTITY_RESOLUTION.md` | Blocking, matching, clustering, labels | Implementing ER |
| `12_ORCHESTRATION_OPERATIONS.md` | CLI, later DAGs, retry/runbook | Scheduling/operating |
| `13_MONITORING_OBSERVABILITY.md` | Logs, metrics, DQ/freshness alerts | Investigating runs |
| `14_PERFORMANCE_BENCHMARK.md` | Reproducible baseline and optimization | Measuring/optimizing |
| `15_ADR_TECHNOLOGY_DECISIONS.md` | Why Parquet/DuckDB, no default MinIO | Assessing technology choices |
| `16_GOVERNANCE_SECURITY.md` | Licenses, secrets, lineage, backups | Reviewing source rights |
| `17_REVIEWS_RECOMMENDATION.md` | Future review pipeline and ranking eval | Starting later release |
| `18_DATA_PRODUCTS_CONSUMPTION.md` | Published catalog contracts and SQL | Building analyst consumers |
| `19_REPOSITORY_INTEGRATION.md` | Existing crawler/repo preservation + reuse | Starting from current code |
| `20_CODEX_WORKFLOW.md` | Codex working rules, statuses, doc maintenance | Opening AI coding session |

## Decisions frozen for the initial implementation (may be revised with evidence)

1. **MVP = Khánh Hội:** Overture + OSM → canonical store catalog; not live crawler/recommendation.
2. **Storage = local Parquet as authoritative analytical datasets; DuckDB reads/processes it.** A `.duckdb` work DB is optional, not duplicate data lake truth.
3. **No MinIO** for single-host local lake; no default Spark/Trino/Iceberg/Kafka/RabbitMQ.
4. Legacy SQLite stays preserved; PostgreSQL is a later shared operational state option, **not** main analytics storage.
5. Airflow, reviews, recommendation, Docker/GCP are incremental release choices with access/quality/evidence gates.
6. Docs are **DRAFT/PROPOSED** until checked against actual source and tests; code audit precedes changes.

## Recommended start

Read `docs/_delivery/STATUS.md` and `docs/_delivery/CURRENT_TASK.md`; M0/M0.1 findings are already recorded. Confirm no data is lost from untracked or ignored paths. Only after reviewing M0 findings approve M1 Overture + OSM tasks.

## Change management

Keep documents in Git. When changing business scope, requirements, data source, schema, architecture or tests, update linked docs, ADRs and acceptance evidence in the same PR. Update status/version; do not require rewriting every document on each small code change.
