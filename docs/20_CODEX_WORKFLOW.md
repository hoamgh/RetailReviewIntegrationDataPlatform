# 20 — Codex Working Agreement & Documentation Maintenance

**Version:** 1.0 · **Status:** Proposed · **Applies to:** AI-assisted implementation in `feature/fnb-data-platform`
**Related:** `09_ROADMAP.md`, `19_REPOSITORY_INTEGRATION.md`, `08_TEST_PLAN.md`

## 1. Codex đọc gì và theo thứ tự nào?

1. `README.md` của repository, `REUSE_MANIFEST.json`, `pyproject.toml`, `AGENTS.md` **nếu đã tồn tại**.
2. `docs/01_BRD.md` → `02_PRD.md` → `03_SRS.md` → `04_USE_CASES.md` (what and why).
3. `docs/05_SYSTEM_ARCHITECTURE.md` → `06_DATA_MODEL.md` → `07_PIPELINE_DESIGN.md` → `10_DATA_SOURCES.md` → `11_ENTITY_RESOLUTION.md` (how).
4. `docs/08_TEST_PLAN.md` → `09_ROADMAP.md` → `12_ORCHESTRATION_OPERATIONS.md` → `13_MONITORING_OBSERVABILITY.md` → `14_PERFORMANCE_BENCHMARK.md` (verification).
5. `docs/15_ADR_TECHNOLOGY_DECISIONS.md` → `16_GOVERNANCE_SECURITY.md` → `17_REVIEWS_RECOMMENDATION.md` → `18_DATA_PRODUCTS_CONSUMPTION.md` → `19_REPOSITORY_INTEGRATION.md` (constraints/later releases).

Read only what is needed in the current short task after understanding the index; avoid loading 20 full files unnecessarily in every prompt. If a document conflicts with actual source/tests, report the conflict and propose update; do not hide it.

## 2. Status labels / claims

- `PROPOSED`: chosen target design, not yet executed.
- `IMPLEMENTED`: code exists and is traceable.
- `VERIFIED`: correct commands, real inputs or controlled fixtures, test outputs and saved evidence.
- `BLOCKED`: permission, source, missing file or failing gate prevents progress.
- `DEPRECATED`: consciously replaced; preserve ADR and migration explanation.

Do not say source integrated, quality passed, live crawl succeeded, multi-worker safe or cloud deployment done without reproducible evidence.

## 3. Task contract

Each task proposed to Codex must state:

| Field | Required |
|---|---|
| `Task ID` | M0/M1/M2... plus unique subtask |
| `Goal / Non-goals` | business outcome + what not to build |
| `Allowed paths` | repo files that may be modified; default no deletes |
| `Input/Output contracts` | exact schema/keys/versions, manifests and paths |
| `Safety constraints` | no secrets, live crawling, paid/cloud action, state reset without permission |
| `Acceptance` | FR/AT IDs, tests and expected artifacts |
| `Evidence` | command results, benchmark figures, failure modes, known gaps |
| `Git diff summary` | files changed, reasons, other dirty paths preserved |

For M0, **allowed paths = none**: a pure analysis/report in the chat, unless the owner explicitly authorizes writing an audit report. Do not silently change `.gitignore`, `AGENTS.md`, CI, existing crawler or copied data.

## 4. Coding priorities

- Grain, identity, data provenance and correctness before performance.
- Data contracts at layer boundaries; schema evolution changes adapters/tests and requires an ADR when impactful.
- Versioned immutable publication; failures never expose half-written catalog.
- Single accountable writer for current pointer; no blind concurrent overwrite.
- Stable ER decisions, explicit manual override strategy, avoid transitive false merges.
- Local filesystem + Parquet, DuckDB processing; no default MinIO, Spark, Trino, Kafka, Redis or distributed worker farm.
- Keep Python config, CLI and SQL reproducible; don't assume MSYS2 Python wheel compatibility on Windows.
- Test offline fixtures first; distinguish simulated performance from real benchmark.

## 5. Working with existing code

`src/`, `pipelines/`, `scripts/`, `tests/` appear as untracked in screenshot. Git state may since have changed. Run inventory on actual branch first. Preserve work of previous assistants/people. If need replace module, map dependents, run tests and leave reversible migration. Existing `crawl_experiment/` modified: do not autoupdate or run until audit. Preserve `REUSE_MANIFEST.json` lineage and `NOTICE.md` obligations.

## 6. Document change rules

Documentation is **living and versioned in Git**. Updating docs later is expected, not a defect.

- **Business scope changes:** revise BRD, PRD, SRS, Use Cases as necessary; review accepted scope.
- **Requirement details/tests change:** update SRS `FR/NFR/AT` IDs and `08_TEST_PLAN.md`; retain mapping.
- **Technical architecture changes:** update 05/06/07 and write/supersede appropriate ADR entry in 15.
- **Data source/rights change:** update 10 and 16 before ingestion or publication.
- **New feature/consumer:** update 17/18, data contracts and acceptance tests.
- **Observed performance/ops change:** record raw evidence and update 12–14; don't replace a measured fact with an estimate.
- Update `Version`, `Status`, `Last reviewed`, and short changelog if a decision materially changes. Use new rule/data version for output-breaking changes.

## 7. Safe completion report template

```text
Task: Mx-y | Status: VERIFIED / IMPLEMENTED / BLOCKED
What changed: [files and short purpose]
Why: [linked BR/PRD/SRS/ADR]
Tests executed: [exact commands + pass/fail]
Observed evidence: [path, counts, runtime; or UNVERIFIED]
Safety: [no live crawling / no secret / no destructive operations]
Open issues: [risk, missing evidence]
Next small task: [single follow-up]
```

## 8. First actionable assignment

Use `CODEX_FIRST_TASK.md` for **M0 repository audit**. Follow-up implementation starts only after owner reviews inventory and preservation plan. Codex should not invent implemented modules based on the architecture drawings.
