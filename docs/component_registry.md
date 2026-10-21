# Component registry — required reading

Google Reviews: STABLE/FROZEN working-tree baseline google-reviews-2026-10-11. TikTok: DEVELOPMENT. Read baselines/google_reviews_baseline.md and runbooks/google_reviews.md BEFORE edits. Changes affecting Google/shared components require explicit scoped authorization, regression and manifest review; never silently refresh hashes to make a failure disappear.

| Component | Canonical path / owner | Boundary |
|---|---|---|
| Google review CLI | scripts/coles_small_set_full_crawl.py / Google | Fixed-store CLI; no Leetea choice |
| Google production API | crawl_experiment/runners/full_crawl_runner.py / Google orchestration | Existing API for explicit Leetea input; Newest, state, outputs |
| Google UI | crawl_experiment/sources/google_maps/{crawler,navigator,review_surface,paginator,extractor,selectors,health}.py | Google-specific; do not import into TikTok |
| Timestamp enrichment | crawl_experiment/sources/google_maps/review_timestamps.py / Google | Same-ID browser-response timestamps, Google-only parser |
| Resolution | crawl_experiment/sources/google_maps/place_resolution.py / Google | Canonical Overture matching, not TikTok search |
| Lifecycle | crawl_experiment/browser/{browser_factory,seleniumbase_session,session_manager}.py / shared-frozen | Ownership/cleanup pattern reusable, settings source-specific |
| Scheduling/access | crawl_experiment/orchestration/{crawlee_adapter,retry_policy,access_policy,review_incremental,review_reconciliation}.py | Google contracts frozen; TikTok gate policy separate |
| State and sink | crawl_experiment/storage/{review_repository,parquet_review_writer,checkpoint_repository,place_access_repository}.py | Review schema/checkpoints frozen; comments need adapter/interface |
| Contracts/telemetry | crawl_experiment/core/, crawl_experiment/observability/ | Shared-frozen modules listed in manifest |
| Canonical-place smoke | scripts/khanh_hoi_review_smoke.py | Selects FIVE POIs, resolution then production crawler; not single Leetea CLI |
| Offline persistence validation | scripts/validate_persistence_e2e.py | Synthetic SQLite/outbox/Parquet/DuckDB, never live crawl |
| TikTok current UI crawler | experiments/tiktok_access_benchmark/crawler.py | Existing Search → video click → DOM comments → JSONL/CSV/checkpoint; do not duplicate |
| TikTok support | experiments/tiktok_access_benchmark/{auth_session,ui_flow,access,network,benchmark}.py; experiments/tiktok_comment_dom/{probe,extractor}.py | Existing session/access/UI/DOM components, reuse internally |
| TikTok research | experiments/tiktok_place_discovery/, tiktok_place_intelligence/, tiktok_comment_probe/ | Research/pipeline artifacts, not new canonical Google code |

Google probes, timestamp controlled experiments and review-extraction benchmarks are NOT production entrypoints. Temporary scripts run_google_leetea_once.py, run_google_leetea_git_old.py and run_google_leetea_production.py from the Codex mirror are retired; do not use.

## TikTok reuse decision (design only; no implementation in this task)

| Capability | Reuse | Keep source-specific |
|---|---|---|
| Browser init/lifecycle | Factory injection, acquire/is_alive/close and cleanup interface | Do not propagate Google UC settings to TikTok; current TikTok BrowserObserver already injects plain Selenium |
| Session management | One owner, sequential tab/context, checkpoint/stop semantics | TikTok manual auth and current-driver resume; Google guest lifecycle does not authenticate TikTok |
| Wait/navigation | Deadline/predicate/telemetry pattern via separate interface | Google Maps selectors, Newest/entity confirmation vs TikTok result click/comment container; no deep import |
| Access/backoff | Status/decision abstraction and diagnostics pattern | TikTok document/required endpoint/ancillary roles, 403/429/challenge, Retry-After; Google policies are not TikTok cooldown thresholds |
| Extraction/pagination | Collector/sink interfaces only | Google Review, IDs, timestamp paths, cards, pane and paginator must not be copied into comment extraction |
| SQLite/Parquet/checkpoints | Outbox, idempotent event sink and atomic checkpoint patterns | Existing Review schema/SQLite is not a comment store; use a distinct adapter/schema/path only in a future authorized task |

Current TikTok Store/UIPort/DOM extraction and auth helpers already exist. No duplicate implementation proposed. First tighten these interfaces and preserve current live-tested DOM behavior; any shared extraction must be isolated and pass Google freeze/regression checks. Missing TikTok response is UNOBSERVED; rate limit/cooldown UNKNOWN. No solver/stealth/session cycling.
