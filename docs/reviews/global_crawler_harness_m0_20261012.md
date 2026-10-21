# Global Crawler Harness v1 — M0 audit and proposed architecture

Date: 2026-10-12. Status: audit/design complete; architecture approval pending. M1 has NOT started. No crawler, browser configuration, dependency, production schema or Google source changes.

## 1. Verified component reuse map

| Responsibility | Existing implementation | Harness integration boundary |
|---|---|---|
| Canonical places | `crawl_experiment/storage/place_catalog.py::load_catalog`, `core/place_catalog.py::CanonicalPlace`; existing Khánh Hội catalog | Read catalog and provenance; do not rebuild ingestion. Preserve Overture source ID and canonical place key. |
| TikTok discovery | `experiments/tiktok_place_discovery/probe.py::run`, `tiktok_access_benchmark/overture_study.py` | Wrap existing search/matching; discovery separate from collection. QUERY_CANDIDATE_UNVERIFIED stays provisional, never relabelled verified. |
| TikTok collection | `experiments/tiktok_place_intelligence/probe.py::run_dom_comments`, `tiktok_comment_dom/probe.py::collect_video`, `extractor.py::CommentSession` | Inject existing port and persistence; preserve top-level-only coverage, bounded stops and collector checkpoints. |
| TikTok browser | `tiktok_access_benchmark/session_manager.py::TikTokSessionManager`, `standard_browser_factory`, existing BrowserObserver/UIPort | Actual factory uses SeleniumBase UC; misleading historical standard-mode documentation is not configuration evidence. Manager intentionally disallows automatic session replacement. |
| TikTok storage | `tiktok_comment_dom/persistence.py::CommentPersistence`, existing `Storage.persist_collector` | Reuse transactional comments + events + checkpoint and existing flush. No batch writer replacement. |
| Reporting/access evidence | `tiktok_access_benchmark/reliability.py`, `network.py`, `auth_session.py`, existing DOM access classifier | Wrap telemetry, retain UNKNOWN and endpoint significance. Do not replace Safe Stop classifier. |
| Layout maintenance | `tiktok_comment_dom/compaction.py` | Separate sealed offline snapshot; not a live delivery sink. Never combine compacted and historical events as new data. |
| Google | `scripts/coles_small_set_full_crawl.py` → `crawl_experiment/runners/full_crawl_runner.py::run_full_crawl`; existing ReviewRepository/ParquetReviewWriter | FROZEN. Shadow monitoring only, read-only SQLite and completed artifacts. Never construct ReviewRepository for read-only inspection: its constructor can migrate and flush. |
| Existing recovery/diagnostics | `crawl_experiment/orchestration/{retry_policy,access_policy}.py`, storage checkpoint/access repositories, observability run artifacts | Patterns to study, not generic cross-source policies to execute. Frozen Google retry rules differ from required TikTok restriction policy. |
| Agent checkpoints | `scripts/agent_checkpoint.py` | Documentation snapshots only; not operational job resume or browser recovery. |

The existing Overture catalog previously audited contains 682 food/drink records. Catalog membership is not proof a business still operates or a TikTok result refers to its precise location. No live discovery was performed for M0.

## 2. Actual persistence contracts

**TikTok SQLite:** `tiktok_comments(storage_key PRIMARY KEY, … revision, run_id)`; `tiktok_comment_events(event_id PRIMARY KEY, payload, delivered)`; `tiktok_comment_checkpoints` keyed by `(run_id, place_id, video_id)` with input digest, next page, serialized collector and status. A single transaction commits comments, corresponding outbox events and collector checkpoint. Exact comment-column checks reject schema drift; Google databases/paths are rejected.

Storage identity hashes place/video/identity kind/scope/comment identity. DOM IDs are candidates, not certified canonical IDs. SESSION_FALLBACK scope is run ID: cross-run logical deduplication is NOT guaranteed. INSERT/UPDATE creates an event whose ID hashes storage key + revision. UNCHANGED can update observation metadata without a new revision/event.

Flush publishes `crawl_date=<date>/run_id=<run>/event-<event_id>.parquet` through temporary file + atomic rename, then marks the event delivered in a separate transaction. Retry checks an existing file's full payload before acknowledgement. Delivered events remain in SQLite. Historical event equality is different from current-state equality: mutable observation metadata may legitimately differ. Compare immutable outbox payloads exactly; reconcile current content/revision separately. Event-history rows are not necessarily unique current comments.

**Google SQLite:** `review_state` uniquely identifies `(source, source_review_id)`; `pending_review_events` holds pending delivery; checkpoint metadata and incremental/access state have separate contracts. Flush batches pending events for a run, then DELETES acknowledged pending rows. The writer uses deterministic ordered-event fingerprints, date/run partitions and footer event IDs; existing-file validation checks membership, not full payload equality. It is not TikTok's retained-delivery ledger.

Both live writers use atomic rename but do not explicitly fsync published files. Process-crash recovery tests do not certify power-loss durability. Retain existing behavior; any stronger filesystem durability change requires a separate scoped design/testing decision. No historical Parquet is overwritten.

## 3. Architecture gaps

| Gap | Consequence | Proposed minimum |
|---|---|---|
| No global durable job/dependency ledger | Completed work may be redispatched after restart | SQLite job identity, attempt history and guarded transitions |
| No cross-worker claim/fencing at source commit | Expired worker could still write | Atomic claims plus transaction-level owner/epoch checks; expiry alone insufficient |
| Final source outcome not atomically tied to job receipt | Crash after delivery could trigger recollection or false COMPLETE | Durable coverage/outcome receipt and exact expected event membership |
| Browser ownership currently closes on outer cleanup; live resume requires same port/page | Human pause/process restart is not general recovery | Separate browser health, authentication state and job lifecycle; explicit ownership transfer/hold |
| Source-specific access policies | Generic retry could evade a real restriction | Independent harness policy using existing evidence; never clear source gates automatically |
| No optional diagnosis/budget/notification ledger | Unknown failures and external calls lack durable decisions | Disabled-by-default providers with reservations, allowlist and deduplication |

Existing tests cover several SQLite/outbox interruption cases. They do NOT demonstrate harness leases, stale-worker rejection, durable final job completion or the requested subprocess-restart acceptance test. Harness is design-only.

## 4. Proposed minimal package and SQLite design

Proposed new `crawler_harness/` package: `contracts.py` (typed outcomes/config), `state.py` (planner/job store), `runtime.py` (controller/recovery/policy), `adapters.py` (thin source wrappers), `providers.py` (optional diagnosis/alerts). This layout is a proposal, not existing code. Keep responsibilities together until separation is justified. No new infrastructure or dependency installation for M1–M3.

Use additive `harness_*` control tables in an explicitly selected **non-Google TikTok SQLite database**. No second database is required for v1 and no tables are added to frozen Google state. Operational DB selection must be explicit; historical output is not silently migrated. Other sources are read-only/contracts in v1. A later writable source requires review of the transaction/fencing boundary.

| Proposed table | Essential data |
|---|---|
| `harness_jobs` | Stable job ID, source/kind/place/entity, configuration/input digest, state, durable data run ID, attempts/budget, eligibility, lease owner/epoch/expiry, checkpoint/receipt, last error |
| `harness_attempts` | Unique attempt/run IDs, job ID, timestamps, worker/session reference, stage, outcome, recovery counters |
| `harness_dependencies` | Prerequisite job IDs and required outcomes; source jobs recover independently |
| `harness_events` | Append-only transition/diagnostic events with source/job/run/attempt/place/entity correlations and sanitized payload |
| `harness_source_controls` | Restriction/human hold, reason, Retry-After lower bound and operator release history |
| Later `harness_ai_ledger`, `harness_alerts` | Atomic quota/cost reservations and reconciled usage; notification fingerprint/cooldown/delivery outcomes |

Stable job ID hashes workflow generation, source, task kind, canonical place/entity and config digest. Explicit revisit generation creates a new collection job; restarts do not. Each attempt has a new attempt ID; persistence recovery retains its original data run ID and immutable input digest.

Claim under `BEGIN IMMEDIATE`: require PENDING/due, satisfied dependencies and no active source gate; atomically set RUNNING, owner, incremented epoch and lease. Heartbeats and persisted expiry use UTC; durations use monotonic clocks. Every mutation checks owner/epoch. An expired lease does not prove a browser/worker died.

**Critical integration requirement:** before permitting takeover, protect TikTok persist/checkpoint/ack transactions with an optional owner/epoch check inside the same SQLite transaction. An unfenced crawler cannot safely share a lease-managed write path. Until this integration is tested, v1 permits only one local writer and no automatic takeover while the old process may still run. Google shadow monitoring never writes Google state. Publishing also requires exclusive local sink ownership and quiescing the old publisher before replacement; SQLite fencing alone does not fence filesystem writes.

## 5. Job state machine

| Transition | Preconditions |
|---|---|
| PENDING → RUNNING | Atomic claim, dependencies ready, source allowed, retry budget available |
| RUNNING → PERSISTING | Durable collector checkpoint and receipt declaring expected events, source outcome and coverage |
| PERSISTING → COMPLETE | Source completion criteria certified, all required publication verified, reconciliation passed |
| PERSISTING → PARTIAL | Bounded/unverified coverage or interrupted extraction, but committed data fully reconciled |
| RUNNING/PERSISTING → FAILED | Exhausted technical recovery or irreconcilable error; preserve checkpoint/outbox |
| RUNNING/PERSISTING → PAUSED_NEEDS_HUMAN | Ambiguous gate/unknown denial requiring operator; preserve evidence and source hold |
| RUNNING/PERSISTING → ACCESS_RESTRICTED | Confirmed CAPTCHA/authentication/required denial/429; no auto retry |
| RUNNING/FAILED → PENDING | Verified technical failure only, prior owner stopped/fenced, bounded scheduled retry and explicit history |
| PAUSED_NEEDS_HUMAN → PENDING | Explicit operator release and successful fresh revalidation; same session when available |
| ACCESS_RESTRICTED → PENDING | Explicit operator authorization, fresh access revalidation, applicable cooldown respected; never timer-only release |

Unlisted transitions are rejected. PERSISTING recovery continues persistence without returning to collection. COMPLETE/PARTIAL are terminal for that job; an intentional later collection requires a new job. Recovery can move a failed persistence job back to PERSISTING under a new fenced claim when a durable receipt exists, without a browser. Gate states may replay already committed outbox safely while retaining the gate and their non-COMPLETE status.

NO_MATCH is a valid COMPLETE **discovery outcome** when its bounded discovery criteria finish; do not create collection jobs for it. It does not prove no matching video exists anywhere. COMMENT_LIMIT, MAX_SCROLLS, no-growth and unsafe-scroll stops are PARTIAL unless actual coverage evidence establishes completion. UI access and storage success remain independent fields.

## 6. Recovery policy and exact crash plan

| Boundary | Expected deterministic recovery/test |
|---|---|
| A: before SQLite commit | Roll back rows/events/checkpoint together; retry only confirmed technical error within budget |
| B: after commit, before publish | Reopen same DB, restore receipt and replay existing outbox; no browser or Gemini required |
| C: after rename, before ack | Verify existing deterministic file against immutable event payload, then ack; mismatch halts without overwriting |
| D: after ack, before job completion | Reconcile receipt/event membership and finalize COMPLETE/PARTIAL without recollecting |
| E: browser crash during partial collection | Retain committed partial rows/checkpoint, flush them; PARTIAL unless safe tested collection resume succeeds |

TikTok currently requires the existing port and same video page for live resume. Cross-process browser recovery is NOT implemented. M3 must either add the smallest TikTok-specific checkpoint-resume extension with tests or explicitly finish PARTIAL and require a new job. Never pretend a new browser preserves authenticated session or stable fallback identity.

Proposed initial retry defaults: at most two technical retries/job, persisted 5s/30s eligibility backoff, separate bounded storage replay budget; configurable and tested. Browser restart only after confirmed process crash/disconnection and no access gate, with previous owner stopped. Known ordinary onboarding uses the existing handler and fresh snapshot. CAPTCHA/auth/required 403/429 stop collection and hold the source; respect Retry-After but do not automatically retry when it expires. Optional-resource 403 remains telemetry only when existing endpoint classification and UI evidence support that conclusion; unknown 403 requires diagnosis/human review. Missing evidence stays UNKNOWN.

Acceptance B must use a child process that commits a fixture then terminates before flush; a new process recovers without a crawler/AI and verifies exact event membership/payloads, pending=0 and no duplicate publication. Repeat recovery twice. Add analogous injected boundaries A/C/D/E, stale-worker transaction/publisher tests, lease expiration, invalid transitions, checkpoint restart and completed-job non-rerun tests. SESSION_FALLBACK cross-run source dedup and exactly-once source collection are expressly excluded from guarantees.

## 7. Optional AI, policy and alerts

Defaults: `AI_ENABLED=false`, `AI_FREE_ONLY=true`, `AI_MAX_CALLS_PER_JOB=1`, `AI_MAX_CALLS_PER_DAY=30`, `AI_PAID_FALLBACK=false`, `AI_MONTHLY_SOFT_BUDGET_USD=0`. No model/project/quota/billing configuration is assumed verified. Activation requires user confirmation of actual AI Studio project, available Free Tier model/quota and billing configuration.

Known errors use deterministic policy; success never calls AI. Unknown errors may reserve one bounded request. Offline mock first; real Gemini client later. Send only allowlisted sanitized technical facts, not raw DOM, comments, personal data, cookies, headers or session screenshots by default. Strict size-bounded JSON diagnosis must select from `NO_ACTION`, `CAPTURE_MINIMAL_EVIDENCE`, `REPLAY_OUTBOX`, `RETRY_TECHNICAL`, `RESTART_BROWSER_TECHNICAL`, `WAIT_FOR_HUMAN`, `STOP_SOURCE`. Policy independently validates evidence, ownership, retry budget and source gate. AI cannot execute shell/code, change files or bypass restrictions. Malformed output/timeout becomes an auditable human action, never guessed execution.

Reserve job/day quota atomically before dispatch, including in-flight requests. Uncertain/time-out requests are not blindly refunded. Exhaustion prevents further AI calls, allows safe deterministic operations and queues one deduplicated alert. Paid mode remains disabled; any future paid support requires separate approval/flag, known prices/max usage, atomic monthly reservation including outstanding requests, and actual usage reconciliation. Unknown cost/budget fails closed. Internal soft budget is not an absolute provider billing guarantee; never silently switch models or billing modes.

Telegram optional env credentials, bounded transport timeout, fingerprint/cooldown persisted in SQLite. Commit crawler state before notification delivery; delivery failure cannot roll it back. Alert contains safe job/source/stage/error/action fields, no raw personal content. Redact token-bearing transport URLs/errors. No notification has been sent during M0.

Emit specified job/browser/stage/recovery/access/persistence/AI/alert events with correlation IDs; use bounded evidence retention. Track source coverage, CAPTCHA observations/UNKNOWN, network evidence coverage, technical retries, reconciliation, file/byte growth and provider usage. Job, browser process and authenticated session each have separate state; a human pause does not imply browser restart.

## 8. Milestones and approval boundaries

| Milestone | Deliverable/acceptance |
|---|---|
| M0 (this document) | Repository contracts, gap analysis, architecture and scope; STOP pending approval |
| M1 | Additive job tables/state machine/claims/receipts; unit tests and schema isolation; no live adapter execution |
| M2 | Leases/fencing, deterministic recovery/events, subprocess crash acceptance B and boundaries A–E |
| M3 | Thin TikTok adapter and mocked end-to-end tests; minimal source-specific transaction hooks if needed; existing regressions; STOP to explain faults/debugging |
| M4 | Disabled-by-default Gemini provider/mock, validated diagnosis/policy/quota ledger; paid requests remain disabled without separate approval |
| M5 | Optional Telegram provider, sanitized dedup/cooldown/delivery-failure tests |
| M6 | Google read-only shadow monitor; IG/FB contracts and fixtures, no scrapers |
| M7 | Manual operator CLI/runbook, progress/checkpoints/final summary and rollout proposal; no automatic live launch |

After M1–M3 explain claim → collect → SQLite commit → outbox publish/verify → ack → reconcile → finalize, with exact fault reproduction commands and expected tables/events. Debug starts with correlated job/attempt events, receipt and pending IDs, then bounded diagnostics; never reset state as troubleshooting. Unknown failures must leave an auditable diagnosis/policy decision or actionable alert. Each milestone records diff, test command/result, limitations and reproduction instructions.

## 9. M0 verification and preserved state

Executed outside sandbox on normal MSI Windows, with no browser/live requests:

```powershell
Set-Location '<repository-root>'
& 'python' -B -m pytest -q -p no:cacheprovider `
  --basetemp output/pytest-harness-m0-audit-20261012 `
  tests/test_google_baseline.py experiments/tiktok_comment_dom/test_persistence.py
```

Result: **21 passed, 2 existing Pandas optional-dependency warnings, 1.79s**. Frozen Google manifest and canonical entrypoint tests passed. These are existing contract tests, not harness implementation acceptance. Use a fresh basetemp path when reproducing to preserve previous temporary fixtures.

Existing modified tracked files (.gitignore, baseline documentation, component registry and work status) and all untracked experiments/output/docs/scripts/tests were inspected and preserved. M0 adds this design and appends milestone notes only, plus the requested agent checkpoint. No implementation, dependency change, staging, commit, push, reset or clean. Prior verified Popeyes milestone remains 7 comments / SQLite 7 / Parquet 7 / pending 0, intentionally PARTIAL; later auth-gated runs do not invalidate that historical milestone or prove unattended readiness.

Next action: approve or revise this architecture before M1. No operational harness feature is claimed complete.
