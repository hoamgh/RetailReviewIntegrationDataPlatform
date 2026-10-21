# Google Reviews baseline — google-reviews-2026-10-11

Status: STABLE/FROZEN baseline committed as `94807421404d0f99c22f5b112bf0f3bee46ffce8` (`chore: freeze Google Maps base crawler`), branch refactor/crawl-runner-persistence. Manifest base_commit `925640243d4141d857222c5017cd90f966973739` is its parent reference, not the freeze commit. Timestamp/reviewer/persistence changes are included in 9480742. Do not reset to the parent or 21336a9 to recover this implementation. No Git tag or push performed. Review findings do not invalidate the verified 23-review run; see ../reviews/google_reviews_audit_20261011.md.

Required reading before edits: ../component_registry.md, ../runbooks/google_reviews.md, this document and google_reviews_manifest.json. Preserve Google business logic, shared dependencies, schema, paths and checkpoints. TikTok changes must not modify these without scoped user authorization and full Google regression. No blind Git reset/checkout/clean, no auto staging/commit. Never regenerate hashes merely to suppress drift. AGENTS.md is local/ignored by existing policy; registry repeats its protection for other checkouts.

## Evidence

Verified user live run: data/full_crawl/leetea/20261011-095927/summary.json and incremental_metrics.json, 2026-10-11 16:59:27–17:00:01 Asia/Saigon. COMPLETE, FULL, 23 examined (1 new, 6 updated, 16 unchanged), 4 scrolls, one browser, no restart, 34.078 s total, 7 Parquet event rows. Earlier 2026-10-06 validation runs independently saved 23. Successful access is NOT evidence against future anti-bot detection or guaranteed repeatability. Runtime full browser args UNOBSERVED; Chrome/driver installed at prior audit 155.0.8059.39, not a pinned runtime guarantee.

## Dependency observation (not a new lockfile)

```json
{
  "selenium": "4.49.0",
  "seleniumbase": "4.54.11",
  "crawlee": "1.10.2",
  "pyarrow": "23.0.1",
  "duckdb": "1.5.5",
  "websocket-client": "1.9.2",
  "psutil": "7.2.2",
  "shapely": "2.1.2",
  "pytest": "7.4.4"
}
```

requirements.txt retains existing ranges. No dependency changed by freeze task. Browser setup is production BrowserFactory → SeleniumBaseSession → Driver(uc=True,headless=False), direct identity; retain as historical observation, not an instruction to add evasion to TikTok. No claim this avoids detection. Pandas numexpr/bottleneck warnings remain.

## Protected manifest

google_reviews_manifest.json records exact normalized-text SHA256 per path; static local import closure from canonical CLI/API plus five-place smoke and offline persistence validation. Includes package initializers and shared modules for conservative impact protection; imports in inactive branches are included. It is NOT a runtime trace of every branch. File content is captured independently of dirty Git state. Exact frozen paths:

- `crawl_experiment/__init__.py`
- `crawl_experiment/browser/__init__.py`
- `crawl_experiment/browser/browser_factory.py`
- `crawl_experiment/browser/seleniumbase_session.py`
- `crawl_experiment/browser/session_manager.py`
- `crawl_experiment/core/__init__.py`
- `crawl_experiment/core/errors.py`
- `crawl_experiment/core/failure_taxonomy.py`
- `crawl_experiment/core/models.py`
- `crawl_experiment/core/place_catalog.py`
- `crawl_experiment/core/place_categories.py`
- `crawl_experiment/core/statuses.py`
- `crawl_experiment/network/__init__.py`
- `crawl_experiment/network/network_identity.py`
- `crawl_experiment/network/proxy_provider.py`
- `crawl_experiment/observability/__init__.py`
- `crawl_experiment/observability/metrics.py`
- `crawl_experiment/observability/run_artifacts.py`
- `crawl_experiment/orchestration/__init__.py`
- `crawl_experiment/orchestration/access_policy.py`
- `crawl_experiment/orchestration/crawlee_adapter.py`
- `crawl_experiment/orchestration/retry_policy.py`
- `crawl_experiment/orchestration/review_incremental.py`
- `crawl_experiment/orchestration/review_reconciliation.py`
- `crawl_experiment/runners/__init__.py`
- `crawl_experiment/runners/full_crawl_runner.py`
- `crawl_experiment/sources/__init__.py`
- `crawl_experiment/sources/google_maps/__init__.py`
- `crawl_experiment/sources/google_maps/crawler.py`
- `crawl_experiment/sources/google_maps/extractor.py`
- `crawl_experiment/sources/google_maps/health.py`
- `crawl_experiment/sources/google_maps/navigator.py`
- `crawl_experiment/sources/google_maps/paginator.py`
- `crawl_experiment/sources/google_maps/place_resolution.py`
- `crawl_experiment/sources/google_maps/review_surface.py`
- `crawl_experiment/sources/google_maps/review_timestamps.py`
- `crawl_experiment/sources/google_maps/selectors.py`
- `crawl_experiment/storage/__init__.py`
- `crawl_experiment/storage/checkpoint_repository.py`
- `crawl_experiment/storage/parquet_review_writer.py`
- `crawl_experiment/storage/place_access_repository.py`
- `crawl_experiment/storage/place_catalog.py`
- `crawl_experiment/storage/review_repository.py`
- `requirements.txt`
- `scripts/coles_small_set_full_crawl.py`
- `scripts/khanh_hoi_review_smoke.py`
- `scripts/validate_persistence_e2e.py`

## Verification

tests/test_google_baseline.py checks source drift and canonical CLI/API/storage contracts without browser. Existing focused suites cover runner delegation, extractor, timestamp parser, incremental/reconciliation, persistence, access policy and telemetry. See ../work_status.md for final counts and commands; offline tests do not prove current live access. synthetic validation runs in output/google-freeze-offline-20261011 only, never production state.

## Limits and storage

Production state data/state/crawler_state.sqlite3; production lake data/lake/google_maps_reviews. Separate Leetea validation DBs must not be merged implicitly. Per-run JSON checkpoint is diagnostic/progress persistence, not exact browser/scroll resume. Incremental revisits Newest against existing state; reconciliation only assesses missing reviews after sufficient coverage. Timestamp CDP can fail; DOM remains primary. SQLite migrates missing columns on real use, not during read-only audit. Event lake may contain multiple versions of an ID. Runbook holds verified command; alternative live modes/settings are UNVERIFIED unless explicitly evidenced.

## Proposed commit scope (requires human confirmation)

No files staged. Candidate existing changed source: crawl_experiment/core/models.py; crawl_experiment/orchestration/review_incremental.py; crawl_experiment/sources/google_maps/crawler.py; extractor.py; selectors.py; review_timestamps.py; crawl_experiment/storage/parquet_review_writer.py; review_repository.py; requirements.txt; scripts/khanh_hoi_review_smoke.py; scripts/validate_persistence_e2e.py; tests/test_extractor.py; tests/test_persistence_e2e.py; tests/test_review_persistence.py; tests/test_review_timestamps.py. Expand abbreviated paths using exact paths in ../baseline_commit_paths.txt. New protection docs/checks also listed there. Existing unrelated docs/decisions.md is excluded pending separate review. Do NOT stage experiments/, output/, data/, logs, secrets, unknown untracked file or AGENTS.md. Verify full diff before any stage, then explicit per-path stage only. Unchanged protected files are already in Git and need no staging. Public push requires separate ancestry/identity review under AGENTS.md; no push authorized.

## Baseline commit authorization — 2026-10-11

The user has now explicitly authorized committing the 23 paths in docs/baseline_commit_paths.txt with message `chore: freeze Google Maps base crawler`. Earlier pending-approval statements describe the pre-commit audit. This document and the manifest are included in that baseline commit; obtain its exact hash with `git log -1 --format=%H -- docs/baselines/google_reviews_manifest.json`. The manifest base_commit remains the original parent reference, not the resulting baseline commit. No push is authorized. Runtime artifacts and ignored local AGENTS.md are excluded; source hashes and Google behavior remain unchanged.

## Source provenance and license notices

This prerequisite imports the frozen source snapshot from commit `ea71400a9cdd72368ee6b05ded2ae0938e04a547` without importing its Git ancestry. The source snapshot is distributed under the MIT License with the notice **Copyright (c) 2026 hoamgh**. That notice is retained here in addition to **Copyright (c) 2026 VincentBui25** in the unchanged repository LICENSE. The MIT permission and warranty terms in LICENSE apply; independent personal Git history is not a claim of sole authorship. No runtime crawl data or browser/session evidence is included. The accompanying Google runbook only replaces machine-specific paths with portable placeholders.
