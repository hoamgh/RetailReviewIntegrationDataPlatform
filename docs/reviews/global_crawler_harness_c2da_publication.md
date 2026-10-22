# C2d-A publication preparation

Status: preparation only; no staging, commits, push or PR creation authorized.
Base: personal main `696109b1623a327a588cc3e987abff708652d43e`.

## Scope and safety review

C2d-A adds an opt-in synthetic local-page SeleniumBase UC provider inside the
existing Windows contained worker. Original native handles and creation identities,
Job membership, dedicated profile ownership, guarded IPC and heartbeat remain
mandatory. Browser/session adoption is unsupported. There is no replacement crawler
or competing persistence writer.

The startup-only OwnedService hook reuses a proven existing driver service during
the audited UC initialization sequence, validates endpoint ownership, seals the
lifecycle, and refuses replacements/restarts. Only SeleniumBase4.54.11 and
Selenium4.49.0 are admitted. Installed packages and frozen browser factory are
unchanged. A post-seal violation poisons admission even if UC swallows an exception.

TerminalWorkerResolution is a distinct retained-controller cleanup authority:
native original handles, source mutex capability, existing publisher locks,
zero active owned processes and exact ended job/attempt/session/generation scope.
Its short SQLite transaction appends audit/quiescent facts atomically and verifies
idempotent retries. It does not renew a Claim, reopen jobs, mark UNKNOWN commands
successful, publish/ACK outbox events or release source restrictions. No schema
migration. If the controller/handles are gone, serialized evidence is insufficient.

Fault injection is opt-in local diagnostic code. Worker termination uses the
retained owned native handle; the fixed timeout script has no dynamic code/URL
arguments and is admitted only by the local synthetic provider. It is not a new
IPC opcode or a recovery policy. No successful-path automatic faults or retries.

Both controller and child require the exact synthetic document/digest/workflow.
Source collection and source commits are explicitly refused for the real local
provider. OFFLINE_FIXTURE restriction registries cannot authorize it. Arbitrary
navigation, CDP, reconnect/service replacement, personal profiles and genuine
restriction release remain unsupported. The cooperative Python boundary is not
an isolation mechanism against malicious local reflection/code execution. The
local Chrome smoke is not an OS egress firewall; no external source navigation
is authorized, and Chrome background traffic is not globally certified absent.

## Exact publication inventory — 18 paths

All paths below are required together, relative to the checkout. Existing base
modules supply dynamic worker startup, fake browser fixtures, UC factory and
SQLite/outbox dependencies. No directory-wide copy or staging is proposed.

| Path | Status vs base | Purpose |
|---|---|---|
| crawler_harness/browser_commands.py | Modified | Guarded local provider admission/lifecycle |
| crawler_harness/browser_worker.py | Modified | Contained local UC host, fixed IPC surface |
| crawler_harness/containment.py | Modified | Native creation binding, explicit resolution recognition |
| crawler_harness/publication.py | Modified | Retained source/publisher mutex ownership capabilities |
| crawler_harness/local_browser.py | New | Fixed synthetic page, provider and ownership validation |
| crawler_harness/local_faults.py | New | Explicit bounded native fault and resolution diagnostics |
| crawler_harness/local_resolution_validation.py | New | Operator-submitted one-session diagnostic runner |
| crawler_harness/local_smoke.py | New | Synthetic local basic/fault smoke only |
| crawler_harness/uc_service.py | New | Scoped audited UC service startup hook |
| crawler_harness/worker_resolution.py | New | Explicit terminal-worker audit operation |
| tests/test_harness_local_browser.py | New | Deterministic provider/service negatives and fake startup |
| tests/test_harness_local_faults.py | New | Fake-driver crash/timeout tests |
| tests/test_harness_local_resolution.py | New | Offline one-shot/preflight/resolution contracts |
| tests/test_harness_worker_resolution.py | New | 26 native offline resolution/authority cases |
| scripts/ci/windows_quality_gate.py | Modified | Four added deterministic suites |
| tests/test_ci_windows_quality_gate.py | Modified | Suite coverage and manual-runner exclusion contracts |
| docs/reviews/global_crawler_harness_c2da_publication.md | New | Sanitized publication audit and C2d-B plan |
| docs/reviews/global_crawler_harness_c2da_pr.md | New | Proposed PR description |

Explicitly excluded local review file:
`docs/reviews/global_crawler_harness_m3c2d_real_browser.md`. It contains detailed
machine paths, process identities and private runtime evidence references; retain
it locally. This public summary preserves results without publishing those details.

Also exclude output/, agent_state/, AGENTS.md, fixture runtime directories, SQLite
and WAL/SHM, Parquet, browser profiles, cookies, screenshots, raw DOM, local logs,
installed environments and saved process/session evidence. Synthetic HTML constants
and mocked endpoint examples in source/tests are code fixtures, not captured DOM.
No historical output deletion, migration or compaction is part of publication.

## CI coverage and validation

The existing workflow already calls windows_quality_gate.py. Its YAML, locked
dependencies, action pins, Python/Node versions and sanitized artifact allowlist
are unchanged. The original25 suites remain in the same order; four deterministic
C2d-A modules are appended. Guarded collection measured **619 cases /29 suites**,
so the required floor is619, not an estimate. No skip/xfail/deselect is added.

These tests use contained Python subprocesses, fake drivers and patched service/
factory methods. Installed UC connect is exercised against a simulated service,
not Chrome. Manual local_smoke/local_resolution_validation entrypoints are not
invoked by CI. The existing cooperative network guard stays enabled; subprocess
and Job ownership tests are not weakened. Hosted verification for this new suite
selection remains unverified until a separately approved PR run.

Final fresh offline results are recorded below after bounded execution. The prior
operator-submitted local evidence established basic UC, worker crash, in-flight
timeout and retained-controller resolution. That evidence does not establish
TikTok access or production collection readiness.

## Privacy, provenance and unchanged components

Audit is limited to the exact18-path publication scope and its imports/references,
not a whole-repository security certification. Review includes embedded synthetic
fixture text, credential/private-path patterns, runtime-file extensions and dynamic
module references. Detailed evidence remains private. Source and candidate SHA256
ledger is retained locally for the later exact-path staging review.

LICENSE and its existing MIT copyright attribution retain the exact base Git blob;
no contributor attribution is removed. This increment adds project code wrapping
the installed APIs, not copied dependency source. No dependency upgrade, vendored
package, Google source/assertion or baseline-manifest update is proposed. All47
frozen Google hashes must remain unchanged. No public claim of legal review beyond
preservation of existing licensing/provenance is made.

## Proposed local commit boundaries (not executed)

1. `feat(harness): add contained local UC lifecycle and terminal resolution`
   — the ten crawler_harness paths above and four new test modules (14 paths).
   Keep these together: guarded driver host, local scripts and diagnostics import
   each other; splitting would leave a broken intermediate checkout.
2. `ci(windows): cover deterministic C2d-A lifecycle and resolution tests`
   — the CI gate and CI contract test module (2 paths), depends on commit1.
3. `docs(harness): document C2d-A verification and live integration gates`
   — the two sanitized review documents (2 paths).

Future Draft PR: personal origin only, base main after confirming its expected
reviewed SHA; never shared upstream. Require a fresh Windows offline regression
check for the eventual PR commit. No hosted success is inferred from local tests.
Approval is still required for exact-path staging/commits, push and PR creation.

## Rollback

Before publication, omit the feature or keep local_page unset to retain the existing
fake/offline path. Once merged, use separately reviewed revert commits for this
increment; never reset history or destroy artifacts. No schema rollback is needed.
Do not erase durable resolution facts, reopen terminal jobs or repurpose old profiles.
Leave unresolved controller-death/source holds intact. Rollback of CI selection
must be explicit, not silent removal of required tests to make a failing run green.

## C2d-B plan — design only

| Work | Reuse | Missing contract / acceptance gate |
|---|---|---|
| Real restriction registry | C2c latch epoch/history, Retry-After, source/session scope | Current registry validates OFFLINE_FIXTURE only. Review minimal real-environment schema/version contract, backup/restore rehearsal and rollback before migration; no existing DB migration now. |
| Genuine operator verification | C2c freshness/scope/epoch/quiescence/release checks; C2a native proof | Real verification provider with captured fresh access observations, authenticated/attributed operator protocol and fail-closed UNKNOWN; a caller boolean, fixture proof or elapsed Retry-After cannot grant access. |
| Guarded TikTok collector | Existing run_dom_comments/SeleniumDOMPort, M3-B atomic commit guard, C1 publication/receipt/replay, C2b guarded IPC | Review real operation allowlist, onboarding/reply/WebElement/network/screenshot surfaces, independent controller+worker restriction admission and scope; no arbitrary script/CDP/raw-driver escape. |
| Recovery and safe stop | M1 attempts/fencing, M2 bounded replay, C2a containment, A.3 resolution | Retain data-run identity; storage-only replay for sealed receipts; no automatic recollection, action replay, source release or controller-death takeover. Required 403/429/login/CAPTCHA and UNKNOWN durably block dispatch. |
| Validation and rollout | Existing offline fixtures and one-shot diagnostic pattern | Offline migration/release/race/admission/collector tests first; separately approve one bounded live validation only after review. No live execution is part of this PR. |

C2d-B should stop at each schema/verification/guarded-collector review gate.
No Ollama/LLM, paid API, Telegram, new scheduler or replacement crawler is needed.

## Final decision and fresh local evidence

**GO for publication review/preparation of exactly18 paths, not permission to
stage, commit, push or open a PR.** No blocking source/fixture privacy finding was
found in the reviewed scope. Six tracked modifications and twelve new files are
proposed; the separate detailed machine/runtime report remains excluded.

- Expanded gate: **619 passed**, zero failures/errors/skips, all29 suites present;
  gate time108.219s, external bound240s. This retains all original528 cases and
  adds91 deterministic offline cases. No real browser was launched.
- Focused/lifecycle/security: **165 passed**, zero failures/errors/skips,
  JUnit37.078s, external bound180s. Includes **32 CI/security cases**; these are
  part of165, not an additional count. The extra case verifies manual browser
  entrypoints remain absent from the workflow.
- Google frozen **47/47**, LICENSE/base Git blob, original25 CI suites, workflow
  pins/artifact allowlist and dependency manifests unchanged. actionlint and
  whitespace PASS. Audited installed-library source hashes unchanged.
- Exact-scope privacy/secret-pattern scan excludes runtime extensions and personal
  machine paths; synthetic URL/DOM examples were manually reviewed. This is not
  a whole-repository secret or dependency vulnerability certification.
- Fresh results are local Windows results, not GitHub-hosted success. Native
  hosted compatibility for the four new suites remains to be verified in the
  eventual approved PR. The existing required check name need not change.

Local reproduction, using direct audited Python (no redirecting venv), existing
locked dependencies and fresh report/temp directories:

```powershell
python -B scripts/ci/windows_quality_gate.py --reports <fresh-reports> --dependency-root <locked-site-packages>
# For focused pytest use the unchanged scripts/ci/offline_guard + checkout +
# locked-site-packages PYTHONPATH and a SHORT isolated temporary basetemp.
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp <short-temp> tests/test_harness_local_browser.py tests/test_harness_local_faults.py tests/test_harness_worker_resolution.py tests/test_harness_local_resolution.py tests/test_harness_browser_commands.py tests/test_ci_windows_quality_gate.py
actionlint .github/workflows/harness-windows.yml
git diff --check
```

No auto-deployment, live integration, runtime schema/restriction release, source
crawl, branch mutation or shared-upstream operation is part of this preparation.
STOP for exact-file commit-plan review.
