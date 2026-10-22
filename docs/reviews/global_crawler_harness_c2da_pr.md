# Proposed PR: Contained local UC lifecycle, terminal resolution and Windows coverage

Draft description only. Nothing is staged, committed, pushed or opened.

## Problem and result

The managed Harness previously had only a fake-driver path. This increment adds
an explicitly local synthetic-page SeleniumBase UC provider in the existing Windows
Job-contained worker. It also resolves an ended worker's durable launch history
while the original controller retains verifiable native handles and exclusive
ownership, without reopening the paused job or weakening access restrictions.

## Implemented and verified

- Dedicated profile, exact native creation identities/Job membership, guarded
  IPC/DOM operations and separate-connection heartbeat.
- Audited UC service startup reuse with sealed restart/replacement refusal.
- One-shot operator diagnostics for synthetic basic DOM, exact worker crash and
  in-flight timeout; unknown outcomes are not redispatched.
- Atomic, idempotent retained-controller terminal-resolution audit/quiescent facts.
- Four deterministic C2d-A suites added to the existing Windows offline gate;
  no real browser smoke/fault execution in hosted CI.
- Prior operator-submitted local validation passed basic UC, crash, timeout and
  terminal resolution.
- Fresh local Windows validation: **619 offline gate cases**, **165 focused cases**
  (including32 CI/security contracts), zero failures/errors/skips; Google47/47,
  actionlint and whitespace PASS. Hosted execution is pending.

## Limitations and unsupported operations

No TikTok/Google navigation or source collection is enabled by the real provider.
No runtime restriction migration, genuine access verification/release, automatic
terminal-job reopening, controller-death recovery, session adoption or arbitrary
reconnect/CDP/script surface. UNKNOWN remains UNKNOWN. Native ownership checks are
cooperative safeguards, not a boundary against malicious local Python code.
Hosted compatibility for the newly added test selection is not yet verified.

## Compatibility, privacy and rollback

No schema or dependency changes. Existing MIT LICENSE/attribution and frozen Google
47/47 hashes are preserved. The PR contains only the exact18 reviewed source/test/
CI/document paths; runtime databases, profiles, cookies, screenshots, raw DOM,
machine-specific review notes and agent artifacts are excluded.

Rollback uses reviewed revert commits or leaves the real local provider unused;
no database/history reset, deletion of evidence or source restriction release.

See [publication audit](global_crawler_harness_c2da_publication.md) for exact scope,
commands, final validation and the gated C2d-B plan. No live crawling, deployment,
LLM integration or shared-upstream operation is included.
