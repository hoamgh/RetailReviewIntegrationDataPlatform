# Global Crawler Harness — M2 offline recovery

Status: M2 implemented and offline-verified; STOP for user review. No browser, source collection, Gemini, Telegram, or live crawl was used. M3–M7 are not implemented.

## Scope and implementation

M2 adds deterministic, bounded recovery of already committed TikTok comments/outbox events. It reuses the current `CommentPersistence.flush()` publisher; it adds no comments writer, batch writer, crawler, database, or change to the existing TikTok/Google persistence schemas. An explicit additive recovery extension in the selected harness SQLite records immutable per-job policy, bounded attempt reservations, durable outcomes, holds, backoff eligibility, and audit events. The extension is initialized only when `OfflineRecovery` is explicitly constructed; partial or drifted schema fails closed.

Each call does at most one recovery step. It never sleeps, loops, launches a browser, recollects, claims/takes over a job, or calls AI. A current fencing token is required for active jobs and caller-provided quiescence attestation is mandatory. Attestation is an operator/test assertion, not automatic process-death detection. A prior STARTED reservation is marked INTERRUPTED only after a new authorized generation is active and quiescence is attested; it still consumes the finite budget. In-flight recovery remains visible even if the configured budget is exhausted.

Before flushing, recovery verifies exact scoped event IDs and payload identity, checkpoint input digest, coverage evidence, receipt event membership, existing artifact schema/content, and that no unrelated outbox events are pending (the existing flush is whole-database). Existing artifacts are verified and never overwritten. The writer's atomic rename/acknowledgement behavior is reused. After flush it rechecks exact membership, payload, artifacts, SQLite current content and pending count, updates the existing receipt, and finalizes COMPLETE only with explicit source completeness evidence; otherwise the verified outcome remains PARTIAL. Repeated calls on terminal jobs are read-only integrity checks.

Known CAPTCHA, authentication gate, access denial, or HTTP 429 receipts are held as ACCESS_RESTRICTED; ambiguous required-resource denial is held for a human. No collection retry is offered. Missing/corrupt/extra artifacts, mismatched receipt/checkpoint/coverage, unrelated pending events, or budget exhaustion cannot produce COMPLETE. Artifact integrity errors fail closed without overwrite.

## Fault boundaries demonstrated

Real child processes terminate with `os._exit` after fixture comments/events/checkpoint are committed and at the following points: before Parquet publication; after the existing writer's atomic rename but before outbox acknowledgement; after acknowledgement but before receipt/finalization; and immediately before final state commit. A second process opens the same isolated SQLite database, establishes test quiescence only after process A exited, takes over under the existing M1 rules, and replays only the existing outbox. Browser/recollection entrypoints are patched to fail if called.

For each eligible fixture, recovery verifies exact event identity and payload, SQLite/Parquet equality, pending=0 and the receipt/coverage-derived terminal state. Two further calls create no outbox events or files and preserve artifact hashes. Explicit-end fixtures finalize COMPLETE; MAX_SCROLLS fixtures remain PARTIAL. The suite also verifies missing receipt, missing/corrupt/duplicate artifact, wrong payload/schema, insufficient coverage, unrelated pending events, stale token, active recovery, finite budget/backoff, interrupted reservation history, and restricted-stop holds.

The subprocess tests prove process-crash behavior for this offline fixture and existing writer on this machine. They do not prove power-loss durability, directory/file fsync behavior, exclusive production publisher ownership, races from unfenced source writers, automatic death detection, or browser/session resumption. The outbox writer may publish/ack all DB events, so this recovery refuses any unrelated pending event rather than risk acknowledging another job's data.

## Tests and reproduction

Focused M2:

```powershell
& 'python' -B -m pytest -q -p no:cacheprovider --basetemp output/hm2-final tests/test_harness_recovery.py
```

Result: **25 passed, 2 existing Pandas optional-dependency warnings, 23.60s.**

M1 + M2 + TikTok persistence + frozen Google baseline:

```powershell
& 'python' -B -m pytest -q -p no:cacheprovider --basetemp output/harness-m2-final tests/test_harness_state.py tests/test_harness_hardening.py tests/test_harness_recovery.py experiments/tiktok_comment_dom/test_persistence.py tests/test_google_baseline.py
```

Result: **118 passed, 2 existing Pandas optional-dependency warnings, 25.91s.** Breakdown: 35 M1 state tests, 37 M1 hardening tests, 25 M2 recovery tests, 19 existing TikTok persistence tests, and 2 Google baseline tests. All commands ran on Windows MSI outside Codex sandbox; no live/browser requests.

## M2 limitations and M3 dependencies

- Recovery evidence and quiescence are trusted caller/operator inputs; no OS-level process or publisher death detector exists.
- M1 control-plane fencing does not fence source persistence, checkpoint writes, or filesystem publication. M3 must establish source-writer and exclusive publisher ownership before operational use.
- A whole-database flush forces recovery to hold when unrelated events are pending.
- This tests process crashes, not power-loss/media failure. Existing SQLite TikTok writer settings are unchanged; prior M1 measurement found DELETE journal, synchronous FULL, foreign_keys off on writer connection. Harness control connection uses DELETE/FULL and foreign_keys on. No new durability claim is made for Parquet rename or hardware cache.
- `SESSION_FALLBACK` still cannot guarantee cross-run comment deduplication; source collection is not exactly once.
- No live adapter/browser resume, automatic retry after access restriction, Gemini, Telegram, source-wide scheduler, or Google production changes.

M3 should separately integrate a thin TikTok adapter only after source commit/checkpoint/ack are fenced, publisher quiescence is enforceable, and receipts/reconciliation evidence are produced by trusted existing components. Do not proceed to M3 or live execution without a separate approval.

## Whitespace check

Whole-working-tree `git diff --check` remains nonzero solely for the pre-existing uncommitted trailing whitespace at `docs/component_registry.md:14`; that file was preserved. Do not report a global pass or silently edit the unrelated line. M2 file whitespace was checked separately.
