> Historical planning baseline retained; authoritative checkpoint: [STATUS.md](STATUS.md).

# Current Project Status — Codex Checkpoint

Updated: 2026-10-28 (planning baseline; **NOT verified from repository**)
Branch intended: `feature/fnb-data-platform` (confirm locally)
Current milestone: **M0 — READ-ONLY AUDIT**
Current task: `docs/_delivery/CURRENT_TASK.md`

## Milestone board
- M0 repo/code/data inventory: NOT_STARTED
- M1 Overture + OSM snapshots and profiling: NOT_STARTED
- M2 Entity Resolution and evaluation: NOT_STARTED
- M3 Parquet canonical catalog publication: NOT_STARTED
- M4 incremental snapshots: NOT_STARTED
- M5 rights-cleared reviews: NOT_STARTED
- M6 recommendation evaluation: NOT_STARTED
- M7 operations/performance scale: NOT_STARTED

## Known architecture decisions (user-approved design; implementation unverified)
- MVP: pilot phường Khánh Hội; Overture + OSM; Foursquare excluded.
- Analytical system of record: local Bronze/Silver/Gold Parquet; DuckDB reads/transforms.
- Existing crawler under `crawl_experiment/` requires audit; no live access presumed.
- SQLite legacy preserved; PostgreSQL/Airflow/GCP later only if needed.

## Last verified deliverable
- NONE verified in the current repository. Screenshots/docs are not execution evidence.

## Blockers / risks
- Unknown Git status / untracked ignored source and data artifacts.
- Unknown current source rights, crawler holds, dependency and test health.

## Next exact action
Perform M0 read-only audit using the approved task in `CURRENT_TASK.md`; return findings **in chat only**. Do not edit this checkpoint until a write task is approved.

## Update rule
Keep this file <= 60 lines. Overwrite stale status, do not append transcripts. Link to code/test/evidence paths and commits instead of pasting logs.
