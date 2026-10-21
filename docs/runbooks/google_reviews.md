# Google Reviews production runbook

Read component_registry.md and baselines/google_reviews_baseline.md first. Do not reset Git or use historical experiment subclasses. Run from repository root.

## Canonical implementation

CLI: scripts/coles_small_set_full_crawl.py. API: crawl_experiment.runners.full_crawl_runner.run_full_crawl. CLI has seven fixed Australian stores; --store-id does NOT accept Leetea, and there is no --query, --resume, --state-database or --output flag. For Leetea call the existing API below; no new crawler or browser override.

## Leetea incremental command — VERIFIED

Equivalent command was run by the user, evidence data/full_crawl/leetea/20261011-095927/summary.json and incremental_metrics.json. It preserves existing state, uses Unicode escapes for PowerShell stdin encoding, and retains default production BrowserFactory. No guarantee of future access.

```powershell
Set-Location '<repository-root>'
$env:PYTHONIOENCODING = 'utf-8'
@'
from pathlib import Path
from crawl_experiment.core.models import Store
from crawl_experiment.runners.full_crawl_runner import run_full_crawl
from crawl_experiment.orchestration.review_incremental import IncrementalConfig
state = Path("data/state/crawler_state.sqlite3")
if not state.is_file():
    raise SystemExit("Existing state missing; stopped without creating a database.")
name = "Tr\u00e0 S\u1eefa Leetea"
raise SystemExit(run_full_crawl(
    (Store("overture-367ea9203b0d061cc9e012aa", name, name),),
    output_root=Path("data/full_crawl/leetea"),
    state_database=state,
    parquet_root=Path("data/lake/google_maps_reviews"),
    timeout_seconds=180, max_scrolls=20, concurrency=1,
    validation_mode=True, crawl_mode="REVIEW_INCREMENTAL",
    incremental_config=IncrementalConfig(known_streak_threshold=5, max_scrolls=20),
))
'@ | & 'python' -B -u -
```

## Flags and configuration

CLI --help: VERIFIED offline. Flags: --store-limit, --store-id (repeatable, fixed choices), --timeout-seconds, --max-scrolls, --validation-mode, --crawl-mode REVIEW_FULL/REVIEW_INCREMENTAL/REVIEW_RECONCILIATION, --known-streak-threshold, --incremental-max-scrolls, --recent-review-limit, --deletion-miss-threshold. Defaults: 7200 seconds, 5000 scrolls, concurrency=1, full crawl; use explicit bounded limits. validation_mode disables configured-max-scroll quarantine; it does NOT make a live call offline.

Changing the Leetea command to REVIEW_FULL or REVIEW_RECONCILIATION is UNVERIFIED live in this audit; offline behavior is tested. ReconciliationConfig(recent_review_limit=100,max_scrolls=20,deletion_miss_threshold=3) is the existing interface; do not infer deletion from a partial window.

## Storage and resume

State: data/state/crawler_state.sqlite3 (existing). Lake: data/lake/google_maps_reviews/crawl_date=<UTC date>/run_id=<run>/part-<event fingerprint>.parquet. Run: data/full_crawl/leetea/<UTC run_id>/{summary.json,progress.log,run_metadata.json,checkpoints/,metadata/,scroll_metrics.jsonl,review_id_trace.jsonl,incremental_metrics.json,access_job_metrics.json}. Keep terminal active until final summary.

SQLite key (source,source_review_id); mutable content hash detects INSERT/UPDATE/UNCHANGED. Only INSERT/UPDATE produce Parquet events. SQLite last_seen/state updates are expected; schema migration adds missing timestamp columns. Parquet is event history, not a deduplicated current-state table. Checkpoints preserve diagnostics and partial progress but do NOT resume browser or scroll position. Incremental opens Newest again and uses durable state/known streak (5), with verified timestamp safety boundary when available.

Separate validation databases output/leetea_full_validation/state.sqlite3 and output/leetea_reviewer_validation/state.sqlite3 each contain 23 Leetea reviews; never merge/reset them implicitly or replace production state. No new state DB is necessary. Do not run concurrent writers against the same state. Run IDs have second precision: a same-second collision fails rather than silently overwriting a run directory. Existing outbox replay/Parquet fingerprints provide idempotency; SQLite migrations and updates are not read-only.

## Troubleshooting

FULL is review-surface classification, not proof extraction succeeded. Read status, failure_stage/reason, parse errors and final summary. COMPLETE run 20261011-095927 examined 23, INSERT=1 UPDATE=6 UNCHANGED=16, Parquet=7; reviews_persisted=23 is NOT 23 new lake rows. LIMITED/UNKNOWN/AUTH_REQUIRED/challenge are different outcomes. Respect existing access policy; do not reset sessions/state to evade gates. Header sign-in alone is not authentication failure.

Timestamp collector uses local CDP and browser-received responses, not API replay. Failure may leave timestamps MISSING/PARSE_FAILED without preventing DOM collection. Google relative dates are not exact publication timestamps. Pandas currently warns about numexpr/bottleneck versions; tests pass, do not upgrade shared dependencies without Google regression. Browser version/driver match must be checked for future runs; full actual launch arguments of the successful session were not captured (UNOBSERVED).

AGENTS.md is intentionally local/ignored; share protection rules via committed docs, do not force-add it. Never stage state, lake, credentials or raw runtime artifacts.
