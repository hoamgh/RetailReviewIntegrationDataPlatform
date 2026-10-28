# 09 — Roadmap & Delivery Milestones

**Version:** 1.0 · **Status:** Proposed · **Base:** BRD v1.3 / PRD v1.0 / SRS v1.0\
**Principle:** deliver one verifiable data product at a time; no big-bang build.

## 1. Releases and milestones

| Milestone | Outcome / Deliverables | Exit criteria | Depends on |
|---|---|---|---|
| **M0 — Repository audit & safe baseline** | Inventory transferred crawler, existing modules/tests, ignored data; config/dependency map, reusable adapters, access/rights status; safe archival plan | Report VERIFIED/UNVERIFIED/BLOCKED; no deletion; backup plan reviewed | None |
| **M1 — POI source discovery** | Overture + OSM snapshots, Khánh Hội boundary, category mapping, data profiling | Two real extracts with versioned manifests and rights notes; filtered counts reproducible | M0 |
| **M2 — Entity Resolution** | Standardization, geospatial blocking, pair decisions, manual-review queue, evaluation set | Fixture tests, labeled evaluation report, false-merge examples; acceptance AT-03/04/05/10 | M1 |
| **M3 — Published catalog MVP** | Canonical stores, source links, Silver/Gold, quality report, atomic publication, SQL queries | All AT-01…AT-10 demonstrated; run reproducible; first MVP catalog released | M2 |
| **M4 — Snapshot updates** | New/changed/absent reconciliation, incremental and backfill CLI, history strategy | Repeatable diff, no false closed inference, regression vs full refresh | M3 |
| **M5 — Reviews & insights** | Rights-cleared review ingestion adapter, existing crawler integration only after audit, incremental Silver review facts, feature extraction | Source approvals, offline replay, idempotency/crash tests; data quality and review evidence | M3–M4 and rights gate |
| **M6 — Recommendation & evaluation** | Search/ranking baseline, review-aware ranking, quality evaluation | Explainable results and comparative metrics; uncertainty surfaced | M5 or an approved review dataset |
| **M7 — Operations & scalability** | Airflow and/or PostgreSQL when needed; monitoring, Docker/GCP optional; benchmarks | Measured benefit, fault-injection evidence, runbook, reasonable resource cost | After baseline |

**Note:** `M0–M7` là roadmap kỹ thuật; **Product MVP** hoàn thành ở M3, tương ứng BRD/PRD/SRS. M4–M7 không được tự động coi là điều kiện MVP.

## 2. Thứ tự thực hiện trong repo

1. Không viết lại crawler đã chuyển ngay. Đọc `README.md`, `REUSE_MANIFEST.json`, `pyproject.toml`, `src/`, `pipelines/`, `scripts/`, `tests/`, `crawl_experiment/`, `config/` và Git status.
2. Giữ mọi tracked/untracked/ignored assets; không `git clean -fdx`, `reset --hard`, `checkout` ghi đè hay tự xóa runtime SQLite.
3. Khóa schema adapter và dữ liệu thực đầu vào; bắt đầu bằng nhỏ nhưng **không biến sample thành coverage metric**.
4. Mỗi milestone chạy độc lập qua CLI, có manifests/tests, sau đó mới nối thành pipeline.
5. Refactor code cũ chỉ khi test chứng minh behavior được giữ và có đường phục hồi.

## 3. Backlog nhóm theo capability

| Capability | Task bắt buộc đầu tiên | Evidence |
|---|---|---|
| Data acquisition | Snapshot manifest + checksum + source version | raw counts / extraction log |
| Data quality | Profiling fields/ranges/nulls/classification | metrics report + tests |
| ER | Branch-safe matching + labeled pairs | confusion matrix, false-merge cases |
| Data modeling | Grain, canonical keys, source bridge, tests | DDL/Parquet schema + contract tests |
| Reliability | Idempotent rerun, staged publication, crash injection | current pointer intact after failure |
| Incremental | Snapshot diff vs full refresh | row-level diff + benchmark |
| Observability | Structured logs, counts, timings from M1 | run dashboard/JSON reports |
| Performance | 1-worker baseline before optimization | p50/p95, throughput, memory, query plan |

## 4. Status & review process

Mỗi milestone có `NOT_STARTED | IN_PROGRESS | BLOCKED | VERIFIED`. Không chuyển thành VERIFIED chỉ vì Codex báo 'done'; cần execution evidence. Khi requirement thay đổi, cập nhật BRD/PRD/SRS liên quan, ADR, tests và changelog trước khi merge lớn.

## 5. What not to build yet

Không bắt đầu bằng full HCMC 78k records, distributed Spark, Iceberg/Trino, MinIO, Kafka, full frontend, LLM-based UI crawler recovery hoặc multi-VM crawl. Chỉ đưa vào backlog khi có workload, rights, budget, failure mode và benchmark thực chứng minh cần thiết.

## 6. First Codex assignment

M0 **READ ONLY**: inventory, validate existing files/config/tests without mutation, note risks/missing evidence and propose safe implementation slices. Prompt cụ thể nằm ở `CODEX_FIRST_TASK.md`.
