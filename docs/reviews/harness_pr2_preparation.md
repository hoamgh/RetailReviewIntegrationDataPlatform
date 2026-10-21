# Harness PR2 — stacked Windows CI preparation

Status: OFFLINE PREPARED / LOCAL PASS; STOP for review. No staging, commit, push, PR, merge, reset/clean/rebase, live crawl, real browser, C2d or CD. No operation targets shared upstream.

Final Google coverage audit: **58/58 PR1 node IDs covered; 528 collected/passed cases across 25 suites; 31 CI contract/security tests passed. Technical GO for commit review only; hosted readiness remains UNVERIFIED.**

## 1. Branch, parent and scope

- Worktree: `integration-recomentdation-harness-pr2-20261021` (new sibling; did not previously exist).
- Branch: `publication/harness-ci-pr2-preparation-20261021`.
- Exact base/current HEAD: `eb70bc518ebd60ea9dd0758c5cd08438472e30ba` (reviewed PR1 head, NOT the older origin/main).
- Personal origin: `hoamgh/RetailReviewIntegrationDataPlatform`; shared upstream was not fetched or modified.
- PR1's 42 paths inherited from parent, not copied from preparation. All 42 paths have zero parent-to-worktree diff. All 47 protected normalized Google hashes still match. LICENSE and both approved attribution notices unchanged.
- Exactly B=61 + D=12 + P=1 reviewed files copied individually from the publication-preparation worktree. Synthetic replay, Node pin and documentation privacy redactions retained.
- Only three copied files changed for PR2 integration: gate helper, offline guard and CI contract tests. Harness/TikTok implementation and fixtures retain exact reviewed hashes. No Google source/test/dependency file changed.
- This report is one additional requested file. Final candidate scope: **75 paths** = 74 prepared copies plus this report. All index entries remain unchanged/empty; no git add, including intent-to-add, was used.

## 2. Exact candidate inventory

NEW means an untracked publication candidate, not a staged addition. .gitignore is the only tracked parent diff. Existing runtime data is excluded, not deleted.

| Group | Path | Change from parent |
|---|---|---|
| B | `.github/workflows/harness-windows.yml` | NEW / untracked |
| B | `crawler_harness/__init__.py` | NEW / untracked |
| B | `crawler_harness/browser_commands.py` | NEW / untracked |
| B | `crawler_harness/browser_profile.py` | NEW / untracked |
| B | `crawler_harness/browser_protocol.py` | NEW / untracked |
| B | `crawler_harness/browser_worker.py` | NEW / untracked |
| B | `crawler_harness/containment.py` | NEW / untracked |
| B | `crawler_harness/event_scope.py` | NEW / untracked |
| B | `crawler_harness/heartbeat.py` | NEW / untracked |
| B | `crawler_harness/publication.py` | NEW / untracked |
| B | `crawler_harness/recovery.py` | NEW / untracked |
| B | `crawler_harness/restrictions.py` | NEW / untracked |
| B | `crawler_harness/state.py` | NEW / untracked |
| B | `crawler_harness/tiktok_adapter.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/access.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/auth_session.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/benchmark.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/browser_state.js` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/crawler.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/existing_session.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/network.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/overture_study.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/reliability.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/session_comments.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/session_manager.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/test_network.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/test_overture_study.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/test_reliability.py` | NEW / untracked |
| B | `experiments/tiktok_access_benchmark/ui_flow.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/compaction.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/extractor.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/fixtures/replay.json` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/fixtures/replies.json` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/persistence.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/probe.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/scroll_container.js` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/snapshot.js` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/test_compaction.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/test_dom.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/test_dom_structure.js` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/test_persistence.py` | NEW / untracked |
| B | `experiments/tiktok_comment_dom/test_replies_scroll.py` | NEW / untracked |
| B | `experiments/tiktok_place_discovery/pagination.py` | NEW / untracked |
| B | `experiments/tiktok_place_discovery/probe.py` | NEW / untracked |
| B | `experiments/tiktok_place_intelligence/probe.py` | NEW / untracked |
| B | `experiments/tiktok_place_intelligence/test_live_dom_persistence.py` | NEW / untracked |
| B | `experiments/tiktok_search_probe/tiktok_search_probe.py` | NEW / untracked |
| B | `requirements/ci-windows.in` | NEW / untracked |
| B | `requirements/ci-windows.txt` | NEW / untracked |
| B | `scripts/ci/offline_guard/sitecustomize.py` | NEW / untracked |
| B | `scripts/ci/windows_quality_gate.py` | NEW / untracked |
| B | `tests/fixtures_harness_browser.py` | NEW / untracked |
| B | `tests/test_ci_windows_quality_gate.py` | NEW / untracked |
| B | `tests/test_harness_browser_commands.py` | NEW / untracked |
| B | `tests/test_harness_containment.py` | NEW / untracked |
| B | `tests/test_harness_hardening.py` | NEW / untracked |
| B | `tests/test_harness_publication.py` | NEW / untracked |
| B | `tests/test_harness_recovery.py` | NEW / untracked |
| B | `tests/test_harness_restrictions.py` | NEW / untracked |
| B | `tests/test_harness_state.py` | NEW / untracked |
| B | `tests/test_harness_tiktok_managed.py` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m0_20261012.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m1_20261012.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m2_20261012.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3a_assessment.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3b_implementation.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3c1_publication.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3c2_supervision.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3c2a_containment.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3c2b_browser_commands.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_m3c2c_restrictions.md` | NEW / untracked |
| D | `docs/reviews/global_crawler_harness_ci01.md` | NEW / untracked |
| D | `docs/tiktok.md` | NEW / untracked |
| P | `.gitignore` | MODIFIED |
| R | `docs/reviews/harness_pr2_preparation.md` | NEW / untracked |

Excluded: all group-A files from copy; original private agent notes/checkpoints; output/**, data/**, SQLite and sidecars, runtime Parquet, cookies, .env/credentials, browser profiles, screenshots, raw DOM/network/auth/session evidence, package environments, caches and nonrequired observed fixtures. No blanket source-directory copy.

## 3. CI changes and actual collection — final coverage audit

Originally prepared PR2 collected 512 cases from 20 suites. Exact set comparison against PR1 JUnit found 42/58 PR1 node IDs already present, with 16 missing in five inherited modules. All 16 were audited as offline deterministic with existing fixtures/dependencies; none requires a separate environment. No Google test/source or network-guard change was needed.

Appended all five modules, retaining every earlier suite. Fresh guarded pytest --collect-only actually collected **528 unique node IDs across 25 suites**, including **58/58 PR1 cases**, zero missing and exactly the audited 16 added. Only after measuring collection was the gate floor set to 528. One additional CI contract requires all nine inherited Google test modules, preventing this module-coverage omission from recurring.

| Suite | Actual collected cases |
|---|---:|
| `tests/test_harness_restrictions.py` | 50 |
| `tests/test_harness_browser_commands.py` | 42 |
| `tests/test_harness_containment.py` | 24 |
| `tests/test_harness_publication.py` | 41 |
| `tests/test_harness_tiktok_managed.py` | 36 |
| `tests/test_harness_state.py` | 35 |
| `tests/test_harness_hardening.py` | 37 |
| `tests/test_harness_recovery.py` | 25 |
| `experiments/tiktok_comment_dom/test_dom.py` | 29 |
| `experiments/tiktok_comment_dom/test_replies_scroll.py` | 19 |
| `experiments/tiktok_comment_dom/test_persistence.py` | 19 |
| `experiments/tiktok_comment_dom/test_compaction.py` | 15 |
| `experiments/tiktok_place_intelligence/test_live_dom_persistence.py` | 23 |
| `experiments/tiktok_access_benchmark/test_overture_study.py` | 36 |
| `experiments/tiktok_access_benchmark/test_reliability.py` | 32 |
| `experiments/tiktok_access_benchmark/test_network.py` | 7 |
| `tests/test_google_baseline.py` | 2 |
| `tests/test_architecture.py` | 10 |
| `tests/test_navigation_health.py` | 11 |
| `tests/test_review_surface.py` | 19 |
| `tests/test_browser_session.py` | 1 |
| `tests/test_extractor.py` | 2 |
| `tests/test_paginator.py` | 5 |
| `tests/test_review_pane_handoff.py` | 5 |
| `tests/test_smoke_test_runner.py` | 3 |

The full gate contains 58 Google PR1 cases plus 470 other existing cases. The 31 CI contract/security cases run in the separate existing contract step and are not included in 528. No skip/xfail/deselect, new fixture, dependency installation, network exception, guard disable or production modification was introduced. Original missing-module/minimum-case/failed-exit/artifact/zero-skip contracts remain enforced.

Workflow, Python/Node/action pins and artifact allowlist remain unchanged. No workflow was added. See section 10 for exact case-by-case coverage and dependency classification.

## 4. Windows asyncio guard diagnosis and narrow exception

Observed root cause: CPython 3.12.7 on Windows has no native `_socket.socketpair`; stdlib `_fallback_socketpair` builds a loopback TCP listener/client for BaseProactorEventLoop._make_self_pipe. Previous guard denied its bind before asyncio's default Windows loop could initialize. This was an environment-policy incompatibility, not a Google crawler defect.

Implemented exception is tied to actual stdlib code-object identity AND module globals for both the fallback and its immediate `_make_self_pipe` caller, with a real BaseProactorEventLoop instance. No hostname/IP allowlist alone, environment disable flag, broad localhost exception or global disable.

- Only AF_INET/SOCK_STREAM/protocol-zero operations at this exact call boundary qualify.
- Bind must be the fallback's own `lsock` to literal 127.0.0.1 port zero.
- A weak registration records listener identity, current thread and invocation frame identity.
- Connect must be the fallback's own `csock`, in that same invocation/thread, to the listener's actual newly assigned loopback endpoint. Registration is consumed once. CPython's own socketpair subsequently verifies both peer endpoints.
- All other connect/connect_ex/bind calls fail, including direct localhost calls while an authorized event loop exists and from another thread.
- Python DNS APIs getaddrinfo/gethostbyname/gethostbyaddr/getnameinfo remain denied, including literal-host resolution calls. UDP sendto/sendmsg are denied too; no unconnected datagram escape is deliberately permitted.
- Child test interpreters inherit guard loading through PYTHONPATH; native Windows Named Pipe/Job Object fixture IPC remains supported.

Security evidence (31 contract cases total; guard implementation unchanged by the final coverage audit): repeated actual asyncio.run creates/closes self-pipes and empties registration; wrong caller/direct socketpair/local bind/fixed port/IPv6/nonloopback bind/direct external connect/connect_ex rejected; hostname and literal DNS requests rejected; UDP sendto rejected; child interpreter denial and cross-thread noninheritance proven. Existing artifact sanitization, lock, workflow and failed-preflight contracts also pass.

Boundary: cooperative Python audit guard, NOT an OS firewall or adversarial-code sandbox. Test code/native libraries can potentially bypass Python audit coverage; approved suite selection and fake providers remain necessary. Tests verify audited Python entrypoints, not absence of all possible native/OS DNS traffic. Raw browser/native socket inheritance is not supported. The exception intentionally depends on this audited CPython stdlib layout; unsupported implementations must fail, not silently broaden permissions.

## 5. Actual local validation — final coverage audit

- Final full gate: **528 passed**, zero failures/errors/skips/deselections/xfails, 91.56s pytest / 92.359s gate. External subprocess timeout **300 seconds**; no timeout occurred.
- Final CI contract/security tests: **31 passed / 3.48s**, external timeout 60 seconds. Includes complete inherited Google module-selection invariant and all prior self-pipe/negative-network contracts.
- Fresh collection: 528 distinct IDs, 25 nonempty modules, all 58 PR1 IDs present. No unexpected added cases relative to the previous 512 beyond the audited 16.
- actionlint 1.7.7 PASS with 30-second timeout. git diff --check against PR1 PASS; supplemental untracked-text whitespace scan PASS.
- Frozen manifest **47/47** PASS; all **42 PR1 paths unchanged** against parent. LICENSE, attribution, requirements, Google production/tests and production Harness/TikTok untouched.
- Audit changes confined to scripts/ci/windows_quality_gate.py, tests/test_ci_windows_quality_gate.py and this report. Network guard and workflow hashes unchanged in this audit. No file inventory expansion beyond the already prepared 75 paths.
- Limited privacy scan over those 75 candidate text files found no candidate private keys, known provider-token formats, literal credential assignments or unnecessary personal Users/Projects paths. This is not whole-repository certification.

Historical preparation evidence remains valid but superseded for current CI count: prior full gate 512 passed / 95.13s; prior contracts 30 passed / 3.35s. The current result is the fresh 528/31 run above, not a reused earlier number.

Direct Windows CPython 3.12.7 x64 and the existing isolated CI-01 hash-locked package directory were reused, with no venv redirector or package install. Two known Pandas warnings remain because optional global numexpr/bottleneck packages are visible. This is not a fully hermetic clean install. **GitHub-hosted CI NOT RUN / UNVERIFIED**; native Job Object/Named Pipe/profile mutex/process identity on the hosted image remain unverified.

Local evidence directory: `harness-pr2-google-coverage-77a49a9d` under OS TEMP, containing original/updated node IDs, case coverage matrix, contract JUnit/log, collection log, full regression log and sanitized gate reports. Runtime fixtures/evidence are excluded from publication. No browser/live network crawl, AI, Telegram or agent checkpoint write.

## 6. Exact diff interpretation and hashes

`git diff eb70bc5 --name-status` contains only .gitignore because B/D/report are intentionally UNTRACKED. To avoid falsely omitting them, effective preparation diff is that tracked diff UNION the explicit untracked inventory above. No index mutation was needed. The 42 PR1 paths, crawl_experiment/**, requirements.txt, LICENSE and baseline files have empty tracked diff. No untracked Google source path exists in inventory.

SHA256 below uses raw bytes. Source column means reviewed preparation copy; publication column is current PR2 worktree. Runtime/source baseline verification uses its existing normalized algorithm separately. The report itself is excluded from self-hashing.

| Path | Reviewed copy SHA256 | PR2 SHA256 |
|---|---|---|
| `.github/workflows/harness-windows.yml` | `c3a6baf70015f0f55b594344c183e5d35e524524faa0f77b33d3ca0b73383a64` | `c3a6baf70015f0f55b594344c183e5d35e524524faa0f77b33d3ca0b73383a64` |
| `.gitignore` | `3e6f4fc68aada9c8a59055e70d001aa725a3941e746dd5a1b6e21be411158a06` | `3e6f4fc68aada9c8a59055e70d001aa725a3941e746dd5a1b6e21be411158a06` |
| `crawler_harness/__init__.py` | `0a16266a5998b04397a33802a79aeb45f1498b8c29b3bd3b788719abc4d0ec04` | `0a16266a5998b04397a33802a79aeb45f1498b8c29b3bd3b788719abc4d0ec04` |
| `crawler_harness/browser_commands.py` | `6c909dfa3136dd932f8e5e8725a16d7c350df1576e7bd70eaba7215c4941848a` | `6c909dfa3136dd932f8e5e8725a16d7c350df1576e7bd70eaba7215c4941848a` |
| `crawler_harness/browser_profile.py` | `33e5019bf53b7c22a3a3f86de4b3e914ee88918d8562ad084c403cc50efc2d26` | `33e5019bf53b7c22a3a3f86de4b3e914ee88918d8562ad084c403cc50efc2d26` |
| `crawler_harness/browser_protocol.py` | `ebc9f58f399e0ce013caeaf1532e808f89f90473bb630afcd96e61aad3be7ee9` | `ebc9f58f399e0ce013caeaf1532e808f89f90473bb630afcd96e61aad3be7ee9` |
| `crawler_harness/browser_worker.py` | `8dc8768ac63cd9e5cab667a1d3544bdef88e5ea6b21380cc18b216b7f99a7701` | `8dc8768ac63cd9e5cab667a1d3544bdef88e5ea6b21380cc18b216b7f99a7701` |
| `crawler_harness/containment.py` | `59c0974ea70eccdc919c7409697b530d2b322009d169629672827657ffba1eb5` | `59c0974ea70eccdc919c7409697b530d2b322009d169629672827657ffba1eb5` |
| `crawler_harness/event_scope.py` | `897c77117a62888ddaa19637c5f9a2183e6fc2b08fabec4370776d73f6abc2de` | `897c77117a62888ddaa19637c5f9a2183e6fc2b08fabec4370776d73f6abc2de` |
| `crawler_harness/heartbeat.py` | `421bd4d5099251832e35d79618713b3be4940b1e922cf41495a353f6eace71e1` | `421bd4d5099251832e35d79618713b3be4940b1e922cf41495a353f6eace71e1` |
| `crawler_harness/publication.py` | `238098125f27bf07975754622ef0e71d6f8ac3fd61840b43ff2633a5a9f77e29` | `238098125f27bf07975754622ef0e71d6f8ac3fd61840b43ff2633a5a9f77e29` |
| `crawler_harness/recovery.py` | `73cd88e52e2c82996f371e933fe1d6b2d082a264518d696759ceded8365ac89c` | `73cd88e52e2c82996f371e933fe1d6b2d082a264518d696759ceded8365ac89c` |
| `crawler_harness/restrictions.py` | `7adfdf7c4cbc558def04c4323409d6b9d0879c4cc484589cb876edfdfed749fb` | `7adfdf7c4cbc558def04c4323409d6b9d0879c4cc484589cb876edfdfed749fb` |
| `crawler_harness/state.py` | `0403ce60e62d51b4458918619d30744524a80d2b708f22c6e3889ad1e92ae368` | `0403ce60e62d51b4458918619d30744524a80d2b708f22c6e3889ad1e92ae368` |
| `crawler_harness/tiktok_adapter.py` | `76276a80948bb1df8af613e9cd9ab2a9aca470ac3b6554f39e2500853d14b5dd` | `76276a80948bb1df8af613e9cd9ab2a9aca470ac3b6554f39e2500853d14b5dd` |
| `docs/reviews/global_crawler_harness_ci01.md` | `0085aec65d9b6ed69e537bd60eee8ebae138303855dc210de2ee4d0e7c6de231` | `0085aec65d9b6ed69e537bd60eee8ebae138303855dc210de2ee4d0e7c6de231` |
| `docs/reviews/global_crawler_harness_m0_20261012.md` | `1997d08fa7da32ff4f873d79585e55713257a752e6e534d9283e84cb5533da0d` | `1997d08fa7da32ff4f873d79585e55713257a752e6e534d9283e84cb5533da0d` |
| `docs/reviews/global_crawler_harness_m1_20261012.md` | `23e4b226417502b05a9d89134f900478f82081d7e75ace9a3360ed97c698e830` | `23e4b226417502b05a9d89134f900478f82081d7e75ace9a3360ed97c698e830` |
| `docs/reviews/global_crawler_harness_m2_20261012.md` | `757e94b40f7638172516351398883bdddb5bdf099d29bf412ce051c321138f3f` | `757e94b40f7638172516351398883bdddb5bdf099d29bf412ce051c321138f3f` |
| `docs/reviews/global_crawler_harness_m3a_assessment.md` | `c669dd00c569c3a1f9eb632143623093c8d044af88f61e29daf5e6b8b406f57f` | `c669dd00c569c3a1f9eb632143623093c8d044af88f61e29daf5e6b8b406f57f` |
| `docs/reviews/global_crawler_harness_m3b_implementation.md` | `19228e383cef70d66a7b3e3bf22ff1b712369aeb2b479a8917d7e9729f80a3f5` | `19228e383cef70d66a7b3e3bf22ff1b712369aeb2b479a8917d7e9729f80a3f5` |
| `docs/reviews/global_crawler_harness_m3c1_publication.md` | `43d057c5d3ef9b5ed8079458d7b031b1a5c41030eeadaa18ca825bf345c0d0d4` | `43d057c5d3ef9b5ed8079458d7b031b1a5c41030eeadaa18ca825bf345c0d0d4` |
| `docs/reviews/global_crawler_harness_m3c2_supervision.md` | `b0070c70101e318a45e4342f5b205f54188fa5f9804f66e6de8a9e8a51e2ee4a` | `b0070c70101e318a45e4342f5b205f54188fa5f9804f66e6de8a9e8a51e2ee4a` |
| `docs/reviews/global_crawler_harness_m3c2a_containment.md` | `758fa4041998858a9c1c3cac77a2868ba9712754acba32db7d431eb2fee9ae6e` | `758fa4041998858a9c1c3cac77a2868ba9712754acba32db7d431eb2fee9ae6e` |
| `docs/reviews/global_crawler_harness_m3c2b_browser_commands.md` | `47249001a3298fdb73f34c7fa3b8aab0fd8018374c70d4049a8124683c4f92b3` | `47249001a3298fdb73f34c7fa3b8aab0fd8018374c70d4049a8124683c4f92b3` |
| `docs/reviews/global_crawler_harness_m3c2c_restrictions.md` | `de8b98810b65ef2e0578d479f0ac2815bf1831fa541d29482c117528193ca437` | `de8b98810b65ef2e0578d479f0ac2815bf1831fa541d29482c117528193ca437` |
| `docs/tiktok.md` | `ed5f89024f09a6f809bd368cc314c10d0ca8fbe1950982350b7655cb461512df` | `ed5f89024f09a6f809bd368cc314c10d0ca8fbe1950982350b7655cb461512df` |
| `experiments/tiktok_access_benchmark/access.py` | `e53c3178e8e7afc54d17adf787e0b33d49afb110fcb73998661c672267c8a807` | `e53c3178e8e7afc54d17adf787e0b33d49afb110fcb73998661c672267c8a807` |
| `experiments/tiktok_access_benchmark/auth_session.py` | `2ff98b5b09b5ac982c8c180a97a6e94ef3ca0e35e4863740197249d120154182` | `2ff98b5b09b5ac982c8c180a97a6e94ef3ca0e35e4863740197249d120154182` |
| `experiments/tiktok_access_benchmark/benchmark.py` | `38192021b572677a3f5b18cc22d2e12917969c1617e35f25550319dc0d9e203e` | `38192021b572677a3f5b18cc22d2e12917969c1617e35f25550319dc0d9e203e` |
| `experiments/tiktok_access_benchmark/browser_state.js` | `023a8b40ad6b5a4a939418079b4a88c17973701063e8e02e2cdcb1061c407c56` | `023a8b40ad6b5a4a939418079b4a88c17973701063e8e02e2cdcb1061c407c56` |
| `experiments/tiktok_access_benchmark/crawler.py` | `03391d63d905dee4d091921cfe1d070e08b1017961e19db662ae30b83067364b` | `03391d63d905dee4d091921cfe1d070e08b1017961e19db662ae30b83067364b` |
| `experiments/tiktok_access_benchmark/existing_session.py` | `572cae2ac75af668c644cd9aac4b97e986272b96b39fbb31f496fa7a4732d8a5` | `572cae2ac75af668c644cd9aac4b97e986272b96b39fbb31f496fa7a4732d8a5` |
| `experiments/tiktok_access_benchmark/network.py` | `6a48e9cd1d949dcd41f8305ea668847b9543e8359bba2e1a4dca2f2911ece83b` | `6a48e9cd1d949dcd41f8305ea668847b9543e8359bba2e1a4dca2f2911ece83b` |
| `experiments/tiktok_access_benchmark/overture_study.py` | `ed0a48aff7e8f83cf658f7ab9d3a92e9623e21304685cd98e9ef9d5a83b13401` | `ed0a48aff7e8f83cf658f7ab9d3a92e9623e21304685cd98e9ef9d5a83b13401` |
| `experiments/tiktok_access_benchmark/reliability.py` | `3fe0faa07a921be9f7d74b50cb6083680e382ab92c7f44dad4c87dc9fb3b9299` | `3fe0faa07a921be9f7d74b50cb6083680e382ab92c7f44dad4c87dc9fb3b9299` |
| `experiments/tiktok_access_benchmark/session_comments.py` | `d3b94d4846790899eab1c428b5a5a39b01d707060493420fa91615fe4ccb927a` | `d3b94d4846790899eab1c428b5a5a39b01d707060493420fa91615fe4ccb927a` |
| `experiments/tiktok_access_benchmark/session_manager.py` | `319c06b0a3c9688ba42a4b89bff9f94085831c4fdfcad8ad05e1cecaa9c71863` | `319c06b0a3c9688ba42a4b89bff9f94085831c4fdfcad8ad05e1cecaa9c71863` |
| `experiments/tiktok_access_benchmark/test_network.py` | `99cc55d1619049658e5b0023a2df7b3e77c1c5de7faebfbbe24b70d69e7bf444` | `99cc55d1619049658e5b0023a2df7b3e77c1c5de7faebfbbe24b70d69e7bf444` |
| `experiments/tiktok_access_benchmark/test_overture_study.py` | `73eef4d7ce282f5af87904bd21adb4d3ef59e22492e1a952f32ec8367f0cc590` | `73eef4d7ce282f5af87904bd21adb4d3ef59e22492e1a952f32ec8367f0cc590` |
| `experiments/tiktok_access_benchmark/test_reliability.py` | `6870438408a1c07e066bda5564d669cd4903fe9f144cf59702d7a65c29cd1474` | `6870438408a1c07e066bda5564d669cd4903fe9f144cf59702d7a65c29cd1474` |
| `experiments/tiktok_access_benchmark/ui_flow.py` | `a9c7a1c4e00b8be5358ed324192df31c901f2d0adafc6433d73cdd9b71ef55bd` | `a9c7a1c4e00b8be5358ed324192df31c901f2d0adafc6433d73cdd9b71ef55bd` |
| `experiments/tiktok_comment_dom/compaction.py` | `eb1a0ab6e137679f03a06acc48af29539ab1a19829609f952b32f7b995a9a87e` | `eb1a0ab6e137679f03a06acc48af29539ab1a19829609f952b32f7b995a9a87e` |
| `experiments/tiktok_comment_dom/extractor.py` | `b217c51d4f53b1237faafe15fb7187f3cd2890f5cd255c9379ca7fa99f80bc6d` | `b217c51d4f53b1237faafe15fb7187f3cd2890f5cd255c9379ca7fa99f80bc6d` |
| `experiments/tiktok_comment_dom/fixtures/replay.json` | `41d2a38b747b2a384d2014aef490295fc63289fcd26bb65dc51a007b2135dcc2` | `41d2a38b747b2a384d2014aef490295fc63289fcd26bb65dc51a007b2135dcc2` |
| `experiments/tiktok_comment_dom/fixtures/replies.json` | `5dfc13ec23caef044c43f68d1b53a156940dcd303f89378048ac69b1c612004d` | `5dfc13ec23caef044c43f68d1b53a156940dcd303f89378048ac69b1c612004d` |
| `experiments/tiktok_comment_dom/persistence.py` | `04838e5681d3f446ff633cccb57991983637b91c05c4f294977f97f9801614b1` | `04838e5681d3f446ff633cccb57991983637b91c05c4f294977f97f9801614b1` |
| `experiments/tiktok_comment_dom/probe.py` | `549e2feb47f2cd115d15428861e0bad09d38d2b6e684a5ee4a2daad367db76b3` | `549e2feb47f2cd115d15428861e0bad09d38d2b6e684a5ee4a2daad367db76b3` |
| `experiments/tiktok_comment_dom/scroll_container.js` | `ff922350d5c050b7844d5fed4805f91780a2ec700f59ca8de1560efbb77141e5` | `ff922350d5c050b7844d5fed4805f91780a2ec700f59ca8de1560efbb77141e5` |
| `experiments/tiktok_comment_dom/snapshot.js` | `7bc7b371f6030d8a66612d35c925603930bacba609ebf90929d4fbf297e0b201` | `7bc7b371f6030d8a66612d35c925603930bacba609ebf90929d4fbf297e0b201` |
| `experiments/tiktok_comment_dom/test_compaction.py` | `e34c3daa949c1f74e3fe6b21f3dd1f0baa4b8ee2cd32b5b50dc8b45e3041490c` | `e34c3daa949c1f74e3fe6b21f3dd1f0baa4b8ee2cd32b5b50dc8b45e3041490c` |
| `experiments/tiktok_comment_dom/test_dom.py` | `28c9f33ec78f9ad9673da31377f1d742fb06def9d0d3e33509a64104a686075e` | `28c9f33ec78f9ad9673da31377f1d742fb06def9d0d3e33509a64104a686075e` |
| `experiments/tiktok_comment_dom/test_dom_structure.js` | `3e72843912ee237afffc02d3c515da9b6448a60c04b4d6025357f791f4aa5945` | `3e72843912ee237afffc02d3c515da9b6448a60c04b4d6025357f791f4aa5945` |
| `experiments/tiktok_comment_dom/test_persistence.py` | `6b37a0df605ab642d32a6313d046c87eee648415d774269769dafec787957499` | `6b37a0df605ab642d32a6313d046c87eee648415d774269769dafec787957499` |
| `experiments/tiktok_comment_dom/test_replies_scroll.py` | `4eb31053b84018844abe6de8ce4d0ef2f804f3a73a3e4351f5a18a2c7f79fa52` | `4eb31053b84018844abe6de8ce4d0ef2f804f3a73a3e4351f5a18a2c7f79fa52` |
| `experiments/tiktok_place_discovery/pagination.py` | `f0569813bb4b1cd629b9f970d9fbc295b7ff2b53a44538dbe05e2b65cdb96dcb` | `f0569813bb4b1cd629b9f970d9fbc295b7ff2b53a44538dbe05e2b65cdb96dcb` |
| `experiments/tiktok_place_discovery/probe.py` | `b63c27b804abfdc2efa09ff7968e431f15e08036689cab469a0955b58bfc3efd` | `b63c27b804abfdc2efa09ff7968e431f15e08036689cab469a0955b58bfc3efd` |
| `experiments/tiktok_place_intelligence/probe.py` | `4de68e6dd963a4756e7bd715ebc302181eb11311d29ce786843e176afae4d4e6` | `4de68e6dd963a4756e7bd715ebc302181eb11311d29ce786843e176afae4d4e6` |
| `experiments/tiktok_place_intelligence/test_live_dom_persistence.py` | `9b0a23751a0d23c5a76b670788821b5dcbe2bbbf436625b1304080a9b8267221` | `9b0a23751a0d23c5a76b670788821b5dcbe2bbbf436625b1304080a9b8267221` |
| `experiments/tiktok_search_probe/tiktok_search_probe.py` | `2c1ac70fbb9a43ec73d15529c66bc69df50abb93debc23e09825aaf3b2d962b9` | `2c1ac70fbb9a43ec73d15529c66bc69df50abb93debc23e09825aaf3b2d962b9` |
| `requirements/ci-windows.in` | `b1b825dde7e1cb4084cbde2992d25c59fa55d9af8615074408c9fe2c17182755` | `b1b825dde7e1cb4084cbde2992d25c59fa55d9af8615074408c9fe2c17182755` |
| `requirements/ci-windows.txt` | `95e963791543519b0d19f9c90ed2cde47d88f95ba7bbf1b96a462e39f2376822` | `95e963791543519b0d19f9c90ed2cde47d88f95ba7bbf1b96a462e39f2376822` |
| `scripts/ci/offline_guard/sitecustomize.py` | `a342983c0e6d1201f05af356008316c52c86ecc0be418a1a21cd1f16801108cd` | `db0301cdf0787ac921b5ad463922afbb25746e5f18d3f2bb41d986de2e5d4766` |
| `scripts/ci/windows_quality_gate.py` | `ea955eb475aeddc40131d3f3fe3dfaa8efe6001d8246428ffbe3f78c00b85a97` | `24db6fe738e48a850f60955c49c6d710911381cb55c2471de4d7d214c9bf3327` |
| `tests/fixtures_harness_browser.py` | `ce6846c862003232c2426097a04eafae78c79c7da147efd619189c5c75ef4060` | `ce6846c862003232c2426097a04eafae78c79c7da147efd619189c5c75ef4060` |
| `tests/test_ci_windows_quality_gate.py` | `5236387cbd3cd293f759128c10e19a389fadc73e8c584c5c6d0c09c62189c408` | `2eca2e43f65d50299aed41f891e65ad73cc5bfe891295e2758c4c2a7a7a09949` |
| `tests/test_harness_browser_commands.py` | `86cb16ba954576987fc4eaf29349ffe43ed566c82b2b392a62b7d7bdf89dfdcc` | `86cb16ba954576987fc4eaf29349ffe43ed566c82b2b392a62b7d7bdf89dfdcc` |
| `tests/test_harness_containment.py` | `fb096e9fbe366ad5ead17a19fe34e6cf75e6096e08a7629b940b63ecd2c4fc0d` | `fb096e9fbe366ad5ead17a19fe34e6cf75e6096e08a7629b940b63ecd2c4fc0d` |
| `tests/test_harness_hardening.py` | `10f055663c7c839af6eca508930fe68358c8af07c79dd69de8bbc4d173f93c0f` | `10f055663c7c839af6eca508930fe68358c8af07c79dd69de8bbc4d173f93c0f` |
| `tests/test_harness_publication.py` | `c4e08ff17c9360ba83fe2ac698016beebf183ce75c2bff2d07ebcf8415802eed` | `c4e08ff17c9360ba83fe2ac698016beebf183ce75c2bff2d07ebcf8415802eed` |
| `tests/test_harness_recovery.py` | `771d2c31ddc1f4a65501f1ce295ecc2e32bdd850e9382b76bc64cda69575b5e7` | `771d2c31ddc1f4a65501f1ce295ecc2e32bdd850e9382b76bc64cda69575b5e7` |
| `tests/test_harness_restrictions.py` | `a2d99cdad91d33dcc81dba58d61668cd1ed909bb12b78c26dcac3e20f456210b` | `a2d99cdad91d33dcc81dba58d61668cd1ed909bb12b78c26dcac3e20f456210b` |
| `tests/test_harness_state.py` | `7d0794f3390d0822fb0273bb48647d4f3e8b2736f16b4b1de629e822fd51963d` | `7d0794f3390d0822fb0273bb48647d4f3e8b2736f16b4b1de629e822fd51963d` |
| `tests/test_harness_tiktok_managed.py` | `e8ba63d539c34119e0e99385f69e5f76f06a2ecdce41125098ad37295604f294` | `e8ba63d539c34119e0e99385f69e5f76f06a2ecdce41125098ad37295604f294` |

## 7. Reproduction

From the PR2 worktree, select direct CPython 3.12.7 and the audited locked package directory; do not execute tests using the Windows venv redirector. Use fresh OS-temp directories on each invocation.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONPATH = $lockedPackages
& $directPython -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp (Join-Path $env:TEMP ('contracts-' + [guid]::NewGuid().ToString('N'))) tests/test_ci_windows_quality_gate.py
if ($LASTEXITCODE -ne 0) { throw 'CI contracts failed' }
& $actionlint .github/workflows/harness-windows.yml
if ($LASTEXITCODE -ne 0) { throw 'Workflow lint failed' }
& $directPython -B scripts/ci/windows_quality_gate.py --dependency-root $lockedPackages --reports (Join-Path $env:TEMP ('gate-' + [guid]::NewGuid().ToString('N')))
```

For the verified bounded run the gate command was invoked through Python subprocess.run(..., timeout=300), capturing progress to an OS-temp log; contracts timeout=60 and collection timeout=60. The workflow itself retains its 20-minute outer job timeout. No repeated polling or automatic retry was part of validation. Reproduce collection with pytest --collect-only -q against SUITES, guard-first PYTHONPATH and the same interpreter.

## 8. Proposed commit boundaries and stacked publication — not executed

1. TikTok support snapshot: only reviewed B source/resources/fixtures required by Harness; retain synthetic replay and privacy redactions.
2. Harness M1 through M3-C2c source/tests plus milestone documentation: final reviewed snapshot rather than invented historical reconstruction. Dependencies crossing files can justify combining 1+2 into one coherent source commit.
3. Windows CI integration: workflow/locks/gate/guard/contracts, synthetic Overture test fixture integration, .gitignore publication rules and this report. Keep each final commit scope explicitly reviewed; do not split cross-cutting files into nonfunctional states merely for milestone names.

PR2 branch is rooted at PR1 head. Proposed PR2 base is PR1 branch while PR1 is unmerged; after separately approved PR1 merge, review retarget strategy depending on merge vs squash. Do not use stale main as base or duplicate Google prerequisite changes in PR2. Existing PR1 draft and all remotes unchanged by this preparation.

Future approval needed for exact per-file staging, commits, branch push to personal origin and Draft PR creation. Then run actual Windows-hosted checks and require `Windows offline regression (Python 3.12.7)` after a real green run. No merge queue without separately reviewed merge_group support; no auto deployment/crawl.

## 9. Remaining blockers and stop boundary

No blocker found for local offline integration in the audited profile. Public/hosted readiness still requires review of this candidate inventory and security change, actual clean dependency installation/hosted native tests, and publication approval. Retained milestone docs describe historical 17-suite/472-case results; this report supersedes only the PR2 CI count/security behavior, without rewriting their historical evidence.

No staged files, commits, push, PR, source refactor, Google production/test edits, baseline refresh, checkpoint changes in protected worktrees, C2d or CD. Do not infer source coverage, CAPTCHA-free rate, live browser safety or scale capacity from these offline tests. STOP for review.

## 10. Final Google coverage matrix and offline classification

Comparison inputs: PR1 successful 58-case JUnit, previous PR2 512 collected node IDs and new guarded collection of 528 node IDs. Node IDs are compared exactly, including parameter IDs, not merely test-function/module names. The original 42 covered cases and 16 newly included cases are all accounted for below.

### 10.1 Every previously missing case

Classification A = offline deterministic with existing fixtures/dependencies; B = needs an additional fixture/dependency; C = requires separate/unsafe environment. Result: **A=16, B=0, C=0**. No exclusions or separate test layer required for these 16.

| Exact node ID | Function verified | Dependencies / fixture boundary | Classification |
|---|---|---|---|
| `tests/test_browser_session.py::test_invalid_windows_process_handle_is_suppressed_after_shutdown` | Suppress Windows invalid-handle error (winerror=6) only after shutdown; fake process clears _child_created. | Existing SeleniumBaseSession + NetworkIdentity/core errors, stdlib; fake InvalidHandleProcess. No session.start/Driver/process spawn. | A |
| `tests/test_extractor.py::test_extracts_canonical_review` | Extract stable Google review identity, author, rating and text from a synthetic card. | Existing ReviewExtractor, selectors, core Review/ReviewParseError; pytest + local Element/Card fixtures. No DB/network. | A |
| `tests/test_extractor.py::test_missing_review_id_is_card_level_parse_failure` | Reject a card without source review ID with ReviewParseError. | Existing ReviewExtractor, selectors, core Review/ReviewParseError; pytest + local Element/Card fixtures. No DB/network. | A |
| `tests/test_paginator.py::test_initial_zero_cards_then_cards_appear_after_first_scroll` | A first scroll can reveal cards after initial zero results; unique count grows and idle count resets. | Existing ReviewPaginator/PaginationState/selectors; stdlib, local Driver/Pane/Card. wait_seconds=0 and no-op sleeper bound polling. | A |
| `tests/test_paginator.py::test_new_review_ids_reset_idle_count_after_scroll_cycle` | New source IDs reset no-growth idle count after a prior idle observation. | Existing ReviewPaginator/PaginationState/selectors; stdlib, local Driver/Pane/Card. wait_seconds=0 and no-op sleeper bound polling. | A |
| `tests/test_paginator.py::test_non_scrollable_pane_is_detected` | Unmoving pane whose scrollHeight equals clientHeight is detected as non-scrollable/stalled. | Existing ReviewPaginator/PaginationState/selectors; stdlib, local Driver/Pane/Card. wait_seconds=0 and no-op sleeper bound polling. | A |
| `tests/test_paginator.py::test_repeated_zero_growth_cycles_eventually_stall` | Three no-growth scroll cycles reach configured idle limit and stalled stop reason. | Existing ReviewPaginator/PaginationState/selectors; stdlib, local Driver/Pane/Card. wait_seconds=0 and no-op sleeper bound polling. | A |
| `tests/test_paginator.py::test_scroll_top_change_is_reported` | Scroll diagnostics report exact before/after scrollTop and movement flag. | Existing ReviewPaginator/PaginationState/selectors; stdlib, local Driver/Pane/Card. wait_seconds=0 and no-op sleeper bound polling. | A |
| `tests/test_review_pane_handoff.py::test_best_scrollable_pane_is_selected_among_candidates` | Select the higher-scoring primary scrollable review pane among candidates. | Existing ReviewSurface/selectors/errors; already locked selenium ActionChains import, pytest, fake Driver/Pane/Card. render_wait=0; no browser. | A |
| `tests/test_review_pane_handoff.py::test_no_valid_pane_is_review_surface_failure_not_pagination_stall` | Reject invalid pane geometry with ReviewSurfaceError, not a pagination outcome. | Existing ReviewSurface/selectors/errors; already locked selenium ActionChains import, pytest, fake Driver/Pane/Card. render_wait=0; no browser. | A |
| `tests/test_review_pane_handoff.py::test_resolve_review_pane_reacquires_after_sort_rerender` | Reject old invalid pane and reacquire the new valid pane after UI rerender. | Existing ReviewSurface/selectors/errors; already locked selenium ActionChains import, pytest, fake Driver/Pane/Card. render_wait=0; no browser. | A |
| `tests/test_review_pane_handoff.py::test_stale_candidate_is_skipped_when_a_valid_pane_is_available` | Ignore stale element metrics and use another valid pane. | Existing ReviewSurface/selectors/errors; already locked selenium ActionChains import, pytest, fake Driver/Pane/Card. render_wait=0; no browser. | A |
| `tests/test_review_pane_handoff.py::test_tiny_non_scrollable_candidate_is_rejected` | Reject a 49px, non-scrollable candidate as unavailable. | Existing ReviewSurface/selectors/errors; already locked selenium ActionChains import, pytest, fake Driver/Pane/Card. render_wait=0; no browser. | A |
| `tests/test_smoke_test_runner.py::test_pagination_progress_requires_actual_runtime_progress` | False movement and zero card/ID growth cannot become reported progress. | Inherited scripts/google_maps_smoke_test.py + its already-present frozen import closure; stdlib json/sqlite3/asyncio, pytest tmp_path. Only build_summary/pagination_progressed called, not main/run_worker/run_supervisor. | A |
| `tests/test_smoke_test_runner.py::test_summary_does_not_claim_runtime_stages_without_events` | Absent synthetic event evidence leaves all runtime stage claims false. | Inherited scripts/google_maps_smoke_test.py + its already-present frozen import closure; stdlib json/sqlite3/asyncio, pytest tmp_path. Only build_summary/pagination_progressed called, not main/run_worker/run_supervisor. | A |
| `tests/test_smoke_test_runner.py::test_summary_uses_emitted_runtime_stages` | Only stages explicitly emitted in synthetic progress.log are marked reached. | Inherited scripts/google_maps_smoke_test.py + its already-present frozen import closure; stdlib json/sqlite3/asyncio, pytest tmp_path. Only build_summary/pagination_progressed called, not main/run_worker/run_supervisor. | A |

For summary tests, scripts/google_maps_smoke_test.py is inherited from PR1's tree and is not copied/modified by PR2. Its import creates only constants/functions/classes; the guarded __main__ entrypoint is not executed. Tests call build_summary/pagination_progressed using synthetic tmp_path logs and absent local database/checkpoint paths. They do not call run_worker, supervisor, browser creation, subprocess crawling or main. The suite name does not imply a live smoke run. For other modules, all process/DOM/pane/card objects are local synthetic fixtures; render_wait/wait_seconds=0 make polling bounded. Required selenium/pytest imports are already in the audited lock; stdlib/json/sqlite3/asyncio need no added package.

### 10.2 Complete PR1 case set

YES/NO in the previous column shows whether each exact case appeared in the original 512-ID collection; final column uses the updated 528-ID collection. All 58 executed without skip in the final successful gate.

| Exact PR1 node ID | Previous 512 | Final 528 |
|---|---|---|
| `tests/test_architecture.py::test_adapter_retries_sort_failure_with_fresh_session` | YES | YES |
| `tests/test_architecture.py::test_crawlee_concurrency_desired_does_not_exceed_maximum` | YES | YES |
| `tests/test_architecture.py::test_health_classification_is_semantic` | YES | YES |
| `tests/test_architecture.py::test_paginator_requires_repeated_idle_iterations` | YES | YES |
| `tests/test_architecture.py::test_paginator_timeout_is_distinct_from_stall` | YES | YES |
| `tests/test_architecture.py::test_partial_timeout_preserves_extracted_review` | YES | YES |
| `tests/test_architecture.py::test_repository_dedupes_stable_source_identity` | YES | YES |
| `tests/test_architecture.py::test_retry_policy_does_not_restart_for_parse_or_stall` | YES | YES |
| `tests/test_architecture.py::test_sort_failure_gets_one_bounded_fresh_session_retry` | YES | YES |
| `tests/test_architecture.py::test_unknown_review_surface_stops_before_sort_or_collection` | YES | YES |
| `tests/test_browser_session.py::test_invalid_windows_process_handle_is_suppressed_after_shutdown` | NO | YES |
| `tests/test_extractor.py::test_extracts_canonical_review` | NO | YES |
| `tests/test_extractor.py::test_missing_review_id_is_card_level_parse_failure` | NO | YES |
| `tests/test_google_baseline.py::test_canonical_google_entrypoint_and_storage_contract` | YES | YES |
| `tests/test_google_baseline.py::test_frozen_google_source_manifest` | YES | YES |
| `tests/test_navigation_health.py::test_healthy_place_ui_overrides_limited_prompt_text` | YES | YES |
| `tests/test_navigation_health.py::test_navigator_waits_for_resolving_page_instead_of_marking_limited` | YES | YES |
| `tests/test_navigation_health.py::test_place_url_and_tabs_do_not_replace_entity_evidence[detail]` | YES | YES |
| `tests/test_navigation_health.py::test_place_url_and_tabs_do_not_replace_entity_evidence[marker]` | YES | YES |
| `tests/test_navigation_health.py::test_place_url_and_tabs_do_not_replace_entity_evidence[matching_title]` | YES | YES |
| `tests/test_navigation_health.py::test_place_url_and_tabs_do_not_replace_entity_evidence[shell]` | YES | YES |
| `tests/test_navigation_health.py::test_place_url_and_tabs_do_not_replace_entity_evidence[title]` | YES | YES |
| `tests/test_navigation_health.py::test_resolved_place_with_limited_reviews_is_not_full_access` | YES | YES |
| `tests/test_navigation_health.py::test_resolving_page_is_not_limited_without_structural_prompt` | YES | YES |
| `tests/test_navigation_health.py::test_true_limited_page_requires_strong_evidence` | YES | YES |
| `tests/test_navigation_health.py::test_unresolved_limited_search_is_place_resolution_failure` | YES | YES |
| `tests/test_paginator.py::test_initial_zero_cards_then_cards_appear_after_first_scroll` | NO | YES |
| `tests/test_paginator.py::test_new_review_ids_reset_idle_count_after_scroll_cycle` | NO | YES |
| `tests/test_paginator.py::test_non_scrollable_pane_is_detected` | NO | YES |
| `tests/test_paginator.py::test_repeated_zero_growth_cycles_eventually_stall` | NO | YES |
| `tests/test_paginator.py::test_scroll_top_change_is_reported` | NO | YES |
| `tests/test_review_pane_handoff.py::test_best_scrollable_pane_is_selected_among_candidates` | NO | YES |
| `tests/test_review_pane_handoff.py::test_no_valid_pane_is_review_surface_failure_not_pagination_stall` | NO | YES |
| `tests/test_review_pane_handoff.py::test_resolve_review_pane_reacquires_after_sort_rerender` | NO | YES |
| `tests/test_review_pane_handoff.py::test_stale_candidate_is_skipped_when_a_valid_pane_is_available` | NO | YES |
| `tests/test_review_pane_handoff.py::test_tiny_non_scrollable_candidate_is_rejected` | NO | YES |
| `tests/test_review_surface.py::test_m_lu_xec_text_container_resolves_parent_radio` | YES | YES |
| `tests/test_review_surface.py::test_selenium_element_id_deduplicates_menu_entries` | YES | YES |
| `tests/test_review_surface.py::test_sort_button_uses_container_fallback_and_rejects_negative_button` | YES | YES |
| `tests/test_review_surface.py::test_sort_button_uses_localized_xpath_fallback` | YES | YES |
| `tests/test_review_surface.py::test_sort_menu_never_opens_reports_explicit_diagnostics` | YES | YES |
| `tests/test_review_surface.py::test_sort_menu_opens_after_normal_click` | YES | YES |
| `tests/test_review_surface.py::test_sort_menu_opens_on_second_local_attempt` | YES | YES |
| `tests/test_review_surface.py::test_sort_menu_uses_action_chains_fallback` | YES | YES |
| `tests/test_review_surface.py::test_sort_menu_uses_javascript_click_fallback` | YES | YES |
| `tests/test_review_surface.py::test_sort_newest_failure_has_discovered_item_diagnostics` | YES | YES |
| `tests/test_review_surface.py::test_sort_newest_matches_aria_label_and_deduplicates_entries` | YES | YES |
| `tests/test_review_surface.py::test_sort_newest_matches_localized_text[M\u1edbi nh\u1ea5t]` | YES | YES |
| `tests/test_review_surface.py::test_sort_newest_matches_localized_text[Newest]` | YES | YES |
| `tests/test_review_surface.py::test_sort_newest_uses_second_radio_item_for_identified_standard_menu` | YES | YES |
| `tests/test_review_surface.py::test_sort_re_finds_button_after_stale_first_button` | YES | YES |
| `tests/test_review_surface.py::test_sort_reenters_reviews_after_local_attempts_fail` | YES | YES |
| `tests/test_review_surface.py::test_successful_click_without_selection_evidence_is_rejected` | YES | YES |
| `tests/test_review_surface.py::test_unknown_label_menu_close_requires_consistent_review_dates[dates0]` | YES | YES |
| `tests/test_review_surface.py::test_unknown_label_menu_close_requires_consistent_review_dates[dates1]` | YES | YES |
| `tests/test_smoke_test_runner.py::test_pagination_progress_requires_actual_runtime_progress` | NO | YES |
| `tests/test_smoke_test_runner.py::test_summary_does_not_claim_runtime_stages_without_events` | NO | YES |
| `tests/test_smoke_test_runner.py::test_summary_uses_emitted_runtime_stages` | NO | YES |

### 10.3 Commit readiness and remaining limits

**GO for the reviewed PR2 commit scope from an offline technical perspective.** Current branch still points to PR1 commit eb70bc518ebd60ea9dd0758c5cd08438472e30ba. Exactly the prepared 75 candidate paths, no duplicate Google prerequisite diff, empty index and no commit created. Explicit staging/commit approval is still required; GO is not authorization to mutate Git.

**Hosted/operational readiness remains UNVERIFIED:** no real GitHub Actions run, no hermetic dependency proof, no claim of live crawler coverage or access reliability. Guard remains cooperative Python policy, not an OS firewall. No broader network exceptions were needed for the 16 newly selected cases. Existing CPython-internal self-pipe coupling must fail closed on unsupported hosted behavior. No current offline coverage blocker remains for the PR1 58-case set.

Audit reproduction: use existing runbook command with SUITES (now 25) and guard-first PYTHONPATH for --collect-only; compare resulting IDs with all 58 PR1 JUnit IDs. Full gate has an outer 300-second subprocess bound, contracts/collection 60 seconds and actionlint 30 seconds. No retry, skip/xfail/deselect or source changes used. Final report supersedes count/status statements from historical preparation only. STOP for review; no stage/commit/push/PR/upstream operation.
