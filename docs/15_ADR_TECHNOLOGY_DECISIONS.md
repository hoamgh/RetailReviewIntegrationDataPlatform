# 15 — Architecture Decision Records (ADRs)

**Version:** 1.0 · **Status:** Proposed; decisions can evolve with evidence.\
**Workflow:** Proposed → Accepted → Superseded; mỗi ADR ghi date, alternatives, rationale, consequences, reversal trigger.

## ADR-001 — Local data lake: Parquet over `.duckdb` as system of record

- **Decision (proposed):** Store published analytical Bronze/Silver/Gold datasets primarily as **Parquet files on local filesystem**. DuckDB reads/writes Parquet and can use an ephemeral/work `.duckdb` database for computation, not a competing authoritative copy.
- **Alternatives:** all persisted tables in `.duckdb`; PostgreSQL as analytics warehouse; local S3-compatible object store.
- **Why:** portable to Spark/other readers and later GCS; naturally versionable snapshots; inexpensive local iteration. **Trade-off:** row-level updates require overwrite/versioned data handling; partition/file layout needs governance.
- **Revisit:** high-frequency row updates, concurrent remote clients, transactional table requirements with measured evidence.

## ADR-002 — DuckDB + Python as initial compute

- **Decision:** Python adapters and DuckDB SQL for extraction/normalization/ER joins/aggregations; optional dbt-duckdb for documented transformations/tests.
- **Alternatives:** pandas-only, Spark/Dataproc, Polars.
- **Why:** pilot fits single-machine processing and readable SQL; minimal deployment. **Trade-off:** distributed processing not native to DuckDB; concurrency must be explicitly controlled.
- **Revisit:** measured memory/runtime failures after simpler optimizations.

## ADR-003 — Two POI acquisition sources in MVP

- **Decision:** Overture Maps and OSM; preserve per-record provenance, release/schema and licenses. **Foursquare is excluded** from current scope. Existing Google/TikTok crawler artifacts are not a source-rights approval.
- **Alternatives:** FSQ OS Places, Google Places API, single source.
- **Why:** access practicality and ER problem coverage, not claim of fully independent upstream sources.
- **Revisit:** insufficient overlap/quality on pilot or new legal/open access options.

## ADR-004 — Conservative entity resolution

- **Decision:** spatial/name candidate generation + versioned features + `match/non_match/review` + conflict-safe clustering; a branch is one physical store.
- **Alternatives:** exact IDs only, global fuzzy match, naive connected components, LLM-only matching.
- **Why:** avoid false merge, preserve uncertainty and human-reviewed decisions. **Trade-off:** review queue requires operations.
- **Revisit:** labeled evaluation supports changed thresholds/model.

## ADR-005 — Atomic immutable publication

- **Decision:** build all outputs in staging, run integrity/data quality gates, publish immutable version, then switch `current` pointer with a tested atomic file replacement. Keep prior publication for rollback.
- **Alternatives:** overwrite current Parquet, per-file publication, transact via table format/catalog.
- **Why:** consumers must not read partial data. **Trade-off:** extra temporary disk and lifecycle cleanup.
- **Revisit:** multiwriter/multihost storage requires transactional catalog/table format.

## ADR-006 — Operational state vs analytical data

- **Decision:** legacy SQLite job/checkpoint DB remains archived/preserved; **PostgreSQL is a preferred target only when shared multi-worker operational state is needed**. It is not the Bronze/Silver/Gold data store.
- **Alternatives:** SQLite single-node, Redis/RabbitMQ, embedded queue.
- **Why:** PostgreSQL row-level locking/transactions can support shared claims/leases when implemented; **it does not make worker correctness automatic**.
- **Revisit:** after review ingestion concurrency and operational needs are measured.

## ADR-007 — No MinIO by default

- **Decision:** local filesystem + bind mounts for one-host workloads; future GCS for VM-independent remote storage on GCP, subject to evaluation.
- **Alternatives:** MinIO S3-compatible local server, direct cloud bucket early.
- **Why:** MinIO offers networked object APIs, not automatic data quality or scaling; introduces a server to maintain without current need.
- **Revisit:** remote/multi-engine access needs an object API; compare operating own object store vs managed GCS.

## ADR-008 — CLI-first, Airflow later

- **Decision:** independently testable CLI + manifests for MVP. Introduce Airflow for scheduled/coordinated pipelines after CLI reliability is proven. Airflow metadata and application state stay logically separate.
- **Alternatives:** Airflow from day one, cron-only forever, large workflow engine.
- **Why:** fault isolation, low MVP overhead; scheduler does not resolve job idempotency by itself.
- **Revisit:** recurring update dependencies and alert/ops complexity warrant orchestrator.

## ADR-009 — Rights before live crawling

- **Decision:** transferred crawler code under `crawl_experiment/` is reused **only after code/security/rights assessment**; never reset access restrictions or bypass gates. CI uses offline fixtures. No real reviews in published data without explicit usage approval.
- **Alternatives:** live scraping as part of every CI/integration test.
- **Why:** compliance, reliability, safety and reproducibility.
- **Revisit:** documented permissions or approved alternate review data.

## ADR-010 — Versioned evolving documentation

- **Decision:** BRD/PRD/SRS/Use Cases and technical docs are **living documents in Git**, with requirement IDs, changelog and status `DRAFT/ACCEPTED/VERIFIED`. Implementation truth is established by tests and run evidence, not aspirational diagrams.
- **Alternatives:** freeze specification permanently; overwrite docs silently.
- **Why:** source schemas, labels, legal constraints and performance evidence change over time.
- **Revisit:** any change requires an updated decision and traceability mapping.
