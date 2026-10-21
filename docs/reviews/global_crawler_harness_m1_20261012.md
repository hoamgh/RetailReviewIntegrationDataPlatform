# Global Crawler Harness — M1 implementation and review

Status: M1 implemented and offline verified; STOP pending review. M2–M7 not started. This extends the approved M0 design without replacing any crawler or writer.

## Files and scope

New files: `crawler_harness/__init__.py`, `crawler_harness/state.py`, `tests/test_harness_state.py`, and this review. Appended project milestone notes to `docs/agent_state/current_task.md` and `docs/work_status.md`, then explicitly generated an agent checkpoint. Existing TikTok persistence, Google production, dependencies, browser settings and all pre-existing uncommitted/untracked work remain unchanged. No providers, browser execution or live calls are introduced.

`JobStore` opens an explicitly selected EXISTING TikTok SQLite file with `mode=rw`. It rejects missing/unknown databases, the protected Google filename and databases containing Google review tables. Call the existing `CommentPersistence` to establish TikTok storage first; harness does not create a second comments store. No production/historical database was migrated during this milestone; test databases are isolated under output.

## SQLite schema and migration/rollback

Additive schema version 1:

| Table | Contract |
|---|---|
| `harness_schema` | Own version marker; does not change SQLite user_version or TikTok schema |
| `harness_jobs` | Full SHA-256 stable identity, definition/input digest, source/place/entity, durable data run ID, state, attempt count, owner, increasing generation, lease expiry, checkpoint and outcome |
| `harness_attempts` | Unique attempt ID and attempt run ID, job/generation uniqueness, owner, started/ended times, outcome |
| `harness_dependencies` | Foreign-key prerequisite relationships; claim blocked until prerequisite COMPLETE; NO_MATCH does not release dependent collection |
| `harness_receipts` | Job receipt with original attempt/generation, source completeness/evidence, exact event membership, stop reason, publication/reconciliation attestations and checkpoint reference |
| `harness_events` | Transactional state/claim/checkpoint/receipt/ownership history and source/job/place/entity/run/attempt/generation correlations |

Leases live in `harness_jobs`, not a separate lease table. Each creation/claim/mutation uses `BEGIN IMMEDIATE`; state, attempt and event changes commit or roll back together. Lease times use UTC epoch seconds; no stage-duration instrumentation is added in M1.

Initialization creates all six tables and version marker in one transaction. Repeat opening verifies version, table set and DDL; partial schemas or drift fail closed without automatic repair. TikTok table definitions and rows are untouched. Job creation is idempotent for the same definition, including dependencies; explicit workflow/revisit changes produce distinct jobs.

Job ID remains full SHA-256. Data run ID is `h-` plus 24 digest characters to reduce Windows path length; its UNIQUE constraint fails closed on collision. Persistence recovery retains this data run ID; a new attempt receives a distinct attempt run ID. Long output roots can still exceed third-party Windows path limits; use short roots. SESSION_FALLBACK identities still do not guarantee cross-run source deduplication.

Migration procedure: stop all writers/publishers, make a consistent SQLite backup through the SQLite backup API, select the intended non-Google database explicitly, open JobStore, inspect version/table set, then run tests before any adapter adoption. No implicit migration of historical outputs. Future changes require a new reviewed schema version/migration, not checksum refresh.

Rollback plan: preferably leave additive tables intact and stop using the harness. For an explicitly approved removal, first stop every worker, save the consistent backup and export operational history, then drop ONLY harness tables in child-before-parent order (events, receipts, dependencies, attempts, jobs, schema) within a transaction. Do not drop TikTok tables, delete Parquet or restore a stale backup over later comments. No destructive rollback command was run or automated.

## State transitions implemented in M1

| From | To | Preconditions |
|---|---|---|
| PENDING | RUNNING | Atomic `claim`, dependencies ready; new owner/generation/attempt/lease |
| RUNNING | PERSISTING | Valid live claim and committed receipt |
| RUNNING or PERSISTING | FAILED / PAUSED_NEEDS_HUMAN / ACCESS_RESTRICTED | Valid live claim; optional bounded error code; closes attempt, preserves receipt/checkpoint |
| PERSISTING | COMPLETE | Receipt certifies source completion with explicit evidence, verified publication, passed reconciliation; exact scoped outbox membership and all events acknowledged |
| PERSISTING | PARTIAL | Verified publication/reconciliation and acknowledged exact membership; source coverage may be incomplete |
| Expired RUNNING/PERSISTING | Same active state under new attempt | Explicit `takeover`; expected generation matches and trusted quiescence verifier confirms old worker AND unfenced processes stopped |

All other transitions are rejected. COMPLETE/PARTIAL cannot be reclaimed. FAILED/human/restricted jobs have no retry/release API in M1: M2 policy/operator control is not implemented. MAX_SCROLLS without explicit completeness evidence cannot become COMPLETE, but can become reconciled PARTIAL. NO_MATCH can complete discovery, never collection. Current code does not schedule jobs or enforce source-wide pause policies; a later controller must do so before live integration.

`heartbeat`, `save_checkpoint`, `record_receipt` and `transition` validate job owner, generation, attempt ID, active state and unexpired lease under the write transaction. Expired workers cannot renew their lease. `claim` never takes over an expired active job. Fencing generation increments on each committed claim/takeover and is not reset.

## Side-effect safety and receipt boundary

SQLite fencing proves control-plane mutation rejection, not browser/process/filesystem fencing. `takeover` is an explicit trusted administrative primitive. Its verifier callback is an operator attestation, NOT an OS process-health detector. Returning True without actually establishing quiescence is unsafe. Callback should perform a short local verification and not start side effects or wait for browser interaction while SQLite is locked. The release evidence reference is durably recorded; no automatic caller or live adapter exists in M1.

Existing crawler/writer does not receive claim tokens in M1. Until later source transaction hooks and exclusive publisher ownership are tested, keep one local writer and prohibit live takeover while the old process/publisher could run. Do not restart browser sessions to evade restrictions. Gemini/Telegram and all live execution are absent/disabled.

Receipt event IDs must already exist and belong to that TikTok place/video/data-run scope. Finalization compares exact scoped event membership and rejects pending events. Publication/reconciliation and source completeness remain attestations from a trusted future adapter/operator; JobStore does not read Parquet or determine actual DOM coverage. A caller can lie about evidence flags, so this is not end-to-end verification or a security boundary against malicious code. The existing writer/reconciler must supply genuine evidence in M3. Empty-event receipts are legitimate for zero-change/discovery outcomes, not proof of collection coverage.

Receipts survive explicit takeover and keep their original provenance. They are not relabelled as observations made by the new worker. Expected event membership is checked again at finalization, preventing an old incomplete receipt from hiding new scoped events. Checkpoint references are bounded identifiers; harness does not persist raw DOM/comments/cookies or serialize browser state.

## Offline verification and reproduction

Final executed command on normal Windows MSI outside Codex sandbox:

```powershell
Set-Location '<repository-root>'
& 'python' -B -m pytest -q -p no:cacheprovider `
  --basetemp output/hm1-final `
  tests/test_harness_state.py `
  experiments/tiktok_comment_dom/test_persistence.py `
  tests/test_google_baseline.py
```

**56 passed, 2 existing Pandas optional-dependency warnings, 3.75s.** Breakdown: 35 M1 tests, 19 existing TikTok persistence tests, 2 Google baseline tests. The initial focused run had 22 pass/1 failure from an overly long new data run ID reaching the Windows Parquet path limit. After shortening it, intermediate regression passed 51 tests; adding final claim-crash and PERSISTING-state cases produced the 56-test result. No dependencies or existing writer changes were needed.

For repeat reproduction, use a NEW short basetemp directory to preserve previous output. Concurrency/fencing cases only:

```powershell
$testDir = 'output/hm1-' + [guid]::NewGuid().ToString('N').Substring(0,8)
& 'python' -B -m pytest -q -p no:cacheprovider `
  --basetemp $testDir tests/test_harness_state.py `
  -k 'two_workers or expired_lease or takeover or process_crash'
```

Two real SQLite connections race on one job; exactly one receives a claim and exactly one attempt/history entry is committed. Expiration tests show normal claim refuses takeover and old tokens cannot mutate; quiescence=False refuses explicit takeover, True permits generation 2, and generation 1 remains rejected. Subprocess tests terminate with `os._exit` before/after job and claim commits: before leaves no partial mutation, after preserves the complete transaction across a reopened connection. These process crashes concern the JOB ledger; they do not implement M2 comments-commit-to-Parquet recovery.

Existing persistence integration test uses the actual CommentPersistence to commit and flush one fixture comment; COMPLETE fails while pending, fails with wrong event membership, and succeeds after existing publication/ack plus matching receipt. No competing writer was created. Existing schema-isolation/drift tests and frozen Google manifest/entrypoint tests pass. `git diff --check` is also required at handoff; untracked source whitespace is checked separately because Git diff does not include it.

## What M1 proves / what remains

Proven: atomic control-plane creation/claim/history, durable attempt/lease state, stale token rejection, guarded state transitions, receipt preconditions, terminal PARTIAL retention, job/claim process-crash atomicity, unchanged TikTok writer compatibility and Google baseline.

Not proven/implemented: automatic recovery/backoff/budget, actual worker-death detection, source commit/ack fencing, filesystem publication fencing, source-wide gates, process restart after COMMENTS commit and before Parquet publication, crash boundaries C/D/E at harness level, real browser resume, power-loss durability, complete comment/reply coverage, cross-run fallback dedup, exactly-once source collection, Gemini/Telegram or any live operational readiness.

Next milestone only after review: M2 deterministic recovery/fault injection and bounded policy, followed later by separately scoped M3 adapter integration. STOP after M1; no automatic progression or live execution.

## M1 direct review and hardening — 2026-10-12

This section supersedes the original verification status above. Source and tests were reviewed directly, not accepted from the earlier 56-test count. Scope remains M1 only. New pre-fix regressions reproduced **6 failures / 1 pass**; the six failures correspond to three confirmed bugs (four variants of the access-restriction bug).

### Confirmed findings and minimal fixes

Line numbers below refer to the current hardened file, with original locations stated for reproduction against the pre-review code.

| Finding / severity | Location | Trigger, violated invariant and consequence | Minimal fix |
|---|---|---|---|
| F1 — MEDIUM: lease revival / mutation after deadline | `crawler_harness/state.py:147` and `:250`; originally `_transaction:143` and `heartbeat:239–241` | Claim expires at 105. Ownership check succeeds at 100, then scheduling/clock advances to 106 before write. Original heartbeat commits a new deadline of 166; other long mutations also commit after expiry. Write locking prevents concurrent ownership takeover, but does not prevent time from advancing: the unexpired-authority invariant is violated. | Token-aware transaction captures ORIGINAL lease authority and rechecks owner/generation/attempt plus original deadline immediately before commit. Expiry rolls back all writes/history, including a heartbeat's attempted extension. Terminal transitions validate against pre-transition authority, not their now-inactive state. |
| F2 — MEDIUM: receipt source provenance overwritten | `state.py:289`, `:323–338`; originally receipt `INSERT OR REPLACE:307–309` | Worker A records source observation, then explicit takeover grants worker B. B revalidates delivery/reconciliation with the same source observation. Original replacement changes receipt generation, attempt and created time to B, falsely attributing A's source evidence to B. | Same observation updates only publication/reconciliation flags, preserving originating generation/attempt/time. Source observation becomes immutable in PERSISTING. Updates are audited under the current attempt. Receipt loading also checks that its origin is a real attempt belonging to that job. |
| F3 — HIGH: restricted run can become COMPLETE | `state.py:29`, `:375`; originally completion checks `:344–347` | Receipt has AUTH_GATE, CHALLENGE, ACCESS_DENIED or RATE_LIMITED but retains `source_complete=True` and earlier evidence. Original code marks COMPLETE when outbox is empty/reconciled. Restrictions are hidden as collection success. | Reject COMPLETE and PARTIAL for known restriction/ambiguous-required-access stop reasons. Preserve receipt and active state; caller must explicitly record the appropriate restricted/human state. No new access classifier or recovery policy was implemented. |

Regression locations: `tests/test_harness_hardening.py:38` (lease revival), `:55` (receipt origin), `:75` and `:138` (gates), `:109` (deadline rollback of checkpoint/receipt/transition), `:153` (sealed observation), `:165` (receipt origin validation). All three findings were reproduced on pre-fix code and then verified after fixes. The additional sealing/provenance validation is local receipt hardening, not a new persistence sink or receipt schema.

### Transaction and completion audit conclusions

- `claim` eligibility/dependency reads, generation increment, owner/lease update, attempt insert and claim event run under the same BEGIN IMMEDIATE transaction. `takeover` likewise holds the write transaction through generation/expiry checks, operator attestation, ending the previous attempt, ownership event and new attempt/owner/event publication. No competing connection can write between these steps.
- All worker-facing mutations now validate authority inside the same write transaction AND revalidate its original deadline before commit. No owner/generation check occurs outside the lock followed by an unfenced control-plane write. The final check is immediately before SQLite commit; it does not guarantee the OS/fsync cannot delay completion beyond the deadline. No concurrent new owner can acquire the SQLite write lock during that commit.
- Expiry alone still cannot claim a running job. `verify_quiescent` remains a trusted operator attestation for both worker and publisher, not automatic process-death detection. No automatic takeover caller exists. Source-wide cooldown, retry eligibility/budgets and gate release are still later controller responsibilities, not silently implemented here.
- Receipt event IDs must resolve to committed TikTok events for the exact place/video/data-run. Finalize rereads all scoped committed events and rejects pending or mismatched membership. Tests add another source revision AFTER sealing the receipt: finalize fails first for pending, then for membership even after existing writer flushes it. This deliberately demonstrates a fail-closed M1 ledger; safely coordinating source writes with receipt sealing remains M3.
- PERSISTING delivery-status revalidation preserves original source provenance after takeover. A different source observation cannot rewrite the sealed receipt. In RUNNING, genuinely changed source observations may still produce a new latest receipt; this is not an immutable archive of every source snapshot. The current event ledger records the mutation and acting attempt.
- MAX_SCROLLS with no explicit completeness evidence remains PARTIAL. Collection completion, publication and reconciliation remain separate attestations; arbitrary caller claims are not actual DOM coverage proof. Known access stops now override contradictory completion flags. Empty outbox does not establish completeness.

### SQLite configuration observed on actual test connections

| Connection | journal_mode | synchronous | foreign_keys |
|---|---|---|---|
| Existing `CommentPersistence` writer | delete | 2 / FULL | 0 |
| Harness `JobStore` | delete | 2 / FULL | 1 |

Measured by `test_connection_pragmas_observed_without_changing_writer`, using actual writer and harness connections to the same isolated SQLite file. These are measurements of the local test connections, not a claim that every historical/production database has identical PRAGMAs. Harness already enables foreign keys only on its own connection; the writer's settings were not modified. No journal_mode/synchronous setting or writer configuration was changed during hardening. Foreign-key enforcement is connection-specific; the existing writer's FK=0 does not fence or protect harness writes made by external connections.

With the observed rollback journal/FULL configuration, subprocess termination tests demonstrate SQLite transaction recovery and complete committed job state. They do not simulate disk loss, storage controllers, torn media or power failure. SQLite's FULL setting also cannot make Parquet durable: the existing writer uses rename without explicit file fsync. No end-to-end power-loss guarantee, exactly-once source guarantee or filesystem fencing is claimed. Harness cannot safely accept external/direct SQL mutation as an authenticated API; trusted callers and exclusive source side-effect ownership remain required.

### Stronger concurrency and crash evidence

`test_process_claims_with_forced_sqlite_contention` (`test_harness_hardening.py:260`) uses two independent spawned Windows processes, each with its own SQLite connection. Both initialize before contention. One holds BEGIN IMMEDIATE; the contender is deliberately forced to observe SQLite's locked error before the first process is released. Only the holder receives a claim; the second finds RUNNING, and only one attempt/generation is committed. The immediate-busy retry exists only in this test, not production recovery.

`test_stale_process_resumes_after_explicit_mocked_takeover` (`:387`) suspends an old process, injects an explicitly mocked takeover, then resumes it with a clock deliberately earlier than expiry. Heartbeat/checkpoint/receipt/transition all reject it by generation/attempt/owner, so rejection is not merely a time check. This is zombie-worker fault injection with NO source/publisher side effects. Its True quiescence callback is a fixture, never a production substitute for actually stopping old processes.

`test_process_crash_receipt_finalize_takeover_transactions` (`:298`) terminates a subprocess after SQL/history writes but before commit, and separately immediately after commit, for receipt insertion, PARTIAL finalization, COMPLETE finalization and takeover. The takeover pre-commit crash occurs after the new attempt/owner/generation/event have also been written. Reopen verifies no partial job/attempt/event/receipt mutation before commit, complete correlated changes after commit, preserved source receipt after takeover, SQLite integrity and foreign_key_check. Earlier job-creation/claim crash tests remain passing. This is job-ledger crash consistency, not the M2 comments-commit-to-Parquet acceptance test.

### New executed commands/results

Pre-fix reproduction: `pytest -q -s -p no:cacheprovider --basetemp output/hm1r-repro tests/test_harness_hardening.py`: **6 failed, 1 passed, 0.64s**. No failure was hidden or counted as a pass.

Focused post-fix verification: `pytest -q -s -p no:cacheprovider --basetemp output/hm1r-focused tests/test_harness_state.py tests/test_harness_hardening.py`: **69 passed, 2 existing warnings, 5.46s**. Three further process crash/stale-worker cases were then added and covered by final verification.

Final actual command, outside sandbox on Windows MSI:

```powershell
Set-Location '<repository-root>'
& 'python' -B -m pytest -q -p no:cacheprovider `
  --basetemp output/hm1r-final `
  tests/test_harness_state.py tests/test_harness_hardening.py `
  experiments/tiktok_comment_dom/test_persistence.py tests/test_google_baseline.py
```

**93 passed, 2 existing Pandas optional-dependency warnings, 16.74s:** 35 original M1 tests, 37 hardening tests, 19 existing TikTok persistence regressions and 2 frozen Google baseline tests. No browser or live source/provider request was executed. Use a fresh short basetemp path for repeats, including the concurrency subset:

```powershell
$testDir = 'output/hm1r-' + [guid]::NewGuid().ToString('N').Substring(0,8)
& 'python' -B -m pytest -q -p no:cacheprovider `
  --basetemp $testDir tests/test_harness_state.py tests/test_harness_hardening.py `
  -k 'process or takeover or expiration or heartbeat'
```

Changed in this review: `crawler_harness/state.py`; `tests/test_harness_state.py` (sealed-receipt expectation); new `tests/test_harness_hardening.py`; this report; appended `current_task.md` and `work_status.md`; requested checkpoint. DDL/version unchanged: no migration or rollback change required for these Python-level fixes, and no existing operational database was edited. Before adopting the hardened code, inspect any previously stored receipt with an access stop; this review did not automatically rewrite old terminal jobs or historical artifacts.

### Remaining limitations and next scope, not implementation

M1 limits remain: operator attestations and receipt evidence are trusted, source/browser/filesystem side effects are not fenced, UTC lease clocks may shift, full reply coverage/stable cross-run fallback identity are unavailable, publication is not independently read by JobStore, and no live operational readiness is proven. Locked control-plane transactions cannot prevent an unfenced source writer from adding events immediately AFTER job completion. That is a disclosed M3 integration dependency, not resolved by the new pre-commit check.

Proposed M2 scope for separate approval: bounded deterministic technical recovery/backoff, persisted retry budgets and source holds, correlated recovery events, and subprocess acceptance at comments SQLite commit → publication/ack → completion using the existing outbox. Gemini is unnecessary for recovery and stays disabled. M3 dependencies: source persist/checkpoint/ack fencing in the same SQLite transaction, exclusive/quiescent publisher ownership, genuine reconciliation receipts, and tested existing-port/browser lifecycle integration. No automatic retry after CAPTCHA/auth/denial/429 and no browser replacement to evade restrictions.

Historical M1 review status at the time of that review: STOP after M1 hardening. M2 had not started then. The separately approved M2 work is documented in `docs/reviews/global_crawler_harness_m2_20261012.md`; M1 invariants/fixes remain in force. M3–M7 and live execution are not implemented.

### Handoff diff check caveat — M1 review
- Changed M1 source/test/report whitespace check PASS. Whole-working-tree git diff --check reports trailing whitespace in docs/component_registry.md:14, outside files changed by this review. This whitespace was present in the registry content read before hardening; registry was preserved, not automatically fixed. Do NOT report global diff --check as PASS. Existing uncommitted registry changes remain intact.
- Final test result remains 93 passed, 2 warnings, 16.74s. No code change after that verification. STOP pending M1 review; no M2/live execution.

### Subsequent milestone status — M2

M2 was separately approved and implemented after this M1 review. See the M2 report for exact recovery design, crash-boundary tests and current results: 25 focused recovery tests and 118 combined M1/M2 + TikTok persistence + Google baseline tests passed. M1 source invariants and schema remain unchanged by M2; the recovery extension is additive and opt-in. M2 does not remove the documented M3 source-writer/filesystem fencing limitations.
