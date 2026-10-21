# M3-C2c — durable restrictions (offline)

Ngày: 2026-10-21. **Hoàn thành phạm vi C2c OFFLINE, STOP để review.**
Không migration runtime DB hoặc xác minh live. Runtime rollout vẫn NO-GO.

## Schema contract reviewed before fixture migration

Add an optional, versioned C2c extension alongside unchanged M1 and M2 tables.
Tables: extension version, source latch, managed session bindings, immutable audit
history, operator verification. Existing exact-schema validation must recognize
the complete extension and reject partial/drifted/unknown versions. Installation
is explicit on isolated fixtures; opening an older DB never installs C2c.

TikTok single-session v1 holds the entire source in this physical database.
The originating session is retained as provenance; changing profile/session does
not escape the hold. Other sources are independent. Epoch increases on every
activation; release uses compare-and-set on the exact epoch and session.

Session registration precedes worker creation. An unresolved session itself
blocks new claims/entrypoints, including after failed restriction writes. Browser
command admission in the child rechecks durable ownership/session/latch in a
short transaction; no SQLite write lock spans browser waits. Already admitted
effects cannot be rolled back; postchecks reject success after activation.

Operator control uses the existing exclusive source-worker lock and C1 publisher
locks plus resolved process/session records. Verification is an immutable,
hashed, fresh, scoped observation bundle, consumed once under exact epoch CAS.
The only verification provider in C2c is explicitly synthetic/offline; there is
no live operator-verification/release CLI. It cannot certify real TikTok access.
Release does not alter jobs, receipts, coverage, claims or browser commands.

## Implementation và reuse map

| File | Thay đổi trong C2c |
|---|---|
| `crawler_harness/restrictions.py` | Optional registry, admissions, normalized observations, native-proof session closure, exclusive offline operator control |
| `crawler_harness/state.py` | Exact extension validation và source gate trong atomic `claim()` |
| `crawler_harness/containment.py` | Source/session gate trong existing worker eligibility; giữ nguyên Job Object launcher |
| `crawler_harness/browser_commands.py` | Session registration, pre/post gate, normalized DOM observation, immediate raw access/network observation, evidence và verified closure |
| `crawler_harness/browser_worker.py` | Same-DB Claim/session/latch admission trước action trong existing contained worker |
| `crawler_harness/tiktok_adapter.py` | Guarded source hook trên borrowed connection; từ chối direct execute/repository bypass khi C2c enabled |
| `tests/fixtures_harness_browser.py` | Optional synthetic UI/network responses, defaults cũ giữ nguyên |
| `tests/test_harness_restrictions.py` | Restart, fault, concurrency, release, schema và C1 integration fixtures |

Không thay collector, source writer, event membership/hash, Parquet schema,
publication algorithm, PRAGMA, heartbeat implementation hoặc Google source.
`JobScopedPublication` tiếp tục là đường storage-only recovery.

## Data model và compatibility

| Table | Contract |
|---|---|
| `harness_restriction_schema` | version=1, environment=`OFFLINE_FIXTURE`; đây là extension riêng, M1 vẫn version=1 |
| `harness_restrictions` | Một hàng/source/database, monotonic epoch, active, originating session/job/attempt/generation, reason, observed_at, not_before, evidence_ref |
| `harness_source_sessions` | Exact source/job/attempt/generation/owner/session; OPEN trước process creation, CLOSED chỉ từ retained C2a native proof |
| `harness_restriction_history` | Append ACTIVATE/RELEASE với actor, epoch, scope, evidence và thời gian |
| `harness_access_verifications` | Fresh scoped immutable snapshot projection/hash, operator/control binding, expiration và consumed flag |

Reason enums: CAPTCHA, LOGIN_REQUIRED, ACCESS_DENIED, RATE_LIMITED, UNKNOWN.
DB/source là affected scope trong single-session v1; session_id giữ provenance.
Đổi session/profile không thoát source hold. Nguồn khác không bị source gate này
chặn; chưa triển khai đa TikTok session hoặc restriction phân tán qua nhiều DB.

`Restrictions.install(store, offline_fixture=True)` phải gọi rõ ràng khi ledger
quiescent; mặc định từ chối runtime migration. Chỉ các isolated test DB đã được
cài extension trong milestone này. Không tự tạo extension khi mở DB/legacy CLI.
Exact validator nhận đủ cả năm bảng, kiểm tra SQL và version; partial/drifted/
unknown schema bị từ chối. Code trước C2c sẽ từ chối DB extended, không downgrade
âm thầm. Installer không sửa source/M1/M2 table hoặc historical data.

## Dispatch, admission và crash safety

1. M1 claim kiểm tra source hold/session trong cùng `BEGIN IMMEDIATE` với attempt,
   generation và claim history. Lease expiry không thay quiescence requirement.
2. C2a existing exclusive source lock được giữ trước session registration; worker
   eligibility lặp lại gate trước launch. OPEN session durable tồn tại trước child.
3. Controller ghi command intent bằng existing ownership validation. Child tự mở
   connection riêng, kiểm tra Claim/session/latch và ghi command admission dưới
   `BEGIN IMMEDIATE` ngay trước existing fake driver action.
4. Activation/release/admission được SQLite serialize; không giữ write lock qua
   IPC wait/browser action. Nếu latch thay đổi sau intent nhưng trước child
   admission, action không được admission. Nếu đã admission rồi mới có latch,
   effect có thể xảy ra; postcheck không cho báo thành công/replay tự động.
5. Source guard dùng borrowed source connection, không mở transaction trên
   JobStore connection khác trong source commit. Latch thay đổi không thể chen
   giữa guard và source commit do existing SQLite writer serialization.
6. Normalized collector snapshot sử dụng `access_failure` và `network.decision`
   hiện có. Required 403 giữ denial; unknown 403 giữ UNKNOWN. Optional 403 chỉ
   được bỏ qua khi classifier có rendered evidence phù hợp. Raw STATE_JS và
   network-response results cũng được kiểm tra trước command tiếp theo.
7. Cached rendering evidence hết hạn sau 5 giây và bị invalidate sau navigation/
   click; thiếu evidence không được coi là clear. Known noninteractive wait page
   dùng normalization hiện có, không tự biến thành CAPTCHA. Incomplete access
   observation được hold UNKNOWN; đây không phải bộ classifier mới thay Safe Stop.
8. Observation + latch/history + job ACCESS_RESTRICTED hoặc PAUSED_NEEDS_HUMAN
   commit cùng transaction. Không suy COMPLETE từ UI/count/MAX_SCROLLS.

Lỗi ghi latch/evidence dừng dispatch tại controller, quiesce tree và để session
OPEN; sau restart source vẫn bị chặn. Không cố ghi một cờ “safe” khi SQLite lỗi.
Nếu authority đã mất, không được clear session hoặc ghi release. Crash trước
activation commit rollback cả latch/history; OPEN session vẫn giữ hold. Crash
sau commit giữ nguyên latch/history qua reopen và repeated audit.

## Quiescence và operator release

Session close nhận chính `ManagedWorker` C2a đang giữ native handles/source lock;
gọi `quiescence()` kiểm tra retained process handle signaled và ActiveProcesses=0.
Không nhận proof dict/verified boolean/PID/lease expiry làm chứng cứ. Khi M1 đã
kết thúc restricted job và xóa lease, chỉ exact matching generation/attempt cùng
retained native proof được append cleanup evidence; không cấp lại Claim hoặc
cho phép job/source mutation. ACTIVE job hết lease vẫn bị từ chối.

Operator protocol trong fixture:

1. Acquire existing source-worker mutex và C1 DB/lake publisher locks.
2. Kiểm tra mọi worker intent resolved, không OPEN session, lake identity đúng.
   Abandoned mutex không tự thỏa điều kiện này; unresolved history vẫn hold.
3. `verify_fixture()` chạy fixed reviewed fake observer hai lần. API không nhận
   caller-provided snapshots hoặc `verified=True`. Persist projection/hash gắn
   đúng source/session/epoch và operator/control ID, thời gian và TTL 60 giây.
4. Release kiểm tra lại ownership, epoch, freshness, hash, not_before và consumed
   flag trong cùng transaction rồi cập nhật latch/history. Snapshot trước
   Retry-After deadline không được dùng, dù chờ deadline rồi mới release.
5. Release không sửa restricted job/attempt/receipt/checkpoint, không claim,
   recollect, thay coverage hay replay command. Job mới cần claim riêng sau đó.

Operator authority là fresh exclusive local operator context, không phải expired
collection Claim. Đây là cooperative local API, không phải authorization chống
hostile Python/local administrator. Fixed provider và registry đều được đánh
nhãn OFFLINE_FIXTURE: **không có live verification provider hay runtime release
CLI**. Synthetic evidence tuyệt đối không chứng minh access thật đã phục hồi.

Bounded evidence gồm classifier reason, job/session/attempt/generation, thời gian,
command count và số observed 403/429; không lưu body, credentials, cookies hoặc
raw browser text. Verification chỉ lưu safe field projection; top-level metadata
không được phát tán vào evidence. Existing command/worker diagnostics được giữ.

## Offline acceptance và fault evidence

Covered cases: năm reasons qua reopen; source A chặn job B, nguồn khác được claim;
optional/required/unknown 403, Retry-After; missing observations; stale token/
generation/attempt/session; duplicate command admission; explicit runtime install
refusal; partial/version-drift schema; SQLite backup/restore rehearsal với một
durable pending job; before/after-commit child process faults; failed latch write
giữ OPEN source session; actual C2a worker crash; restriction giữa intent/IPC và
trong in-flight command; raw UI/network gating; freshness sau navigation.

Release cases: missing/wrong scope/stale/changed epoch/challenge/incomplete
observation, caller boolean, consumed verification, expired operator context,
tampered payload/hash, Retry-After, unresolved/native-active session, concurrent
release/claim và concurrent admission/activation trên connection riêng.

Actual C1 integration: fake collector seals one source comment, latch UNKNOWN
được kích hoạt sau seal; tree đóng an toàn rồi storage replay publish/reconcile
1 SQLite record / 1 event / 1 Parquet row, pending=0. Hai repeated recoveries
không tạo thêm files/commands và không thay latch. Restricted/unsealed jobs giữ
HELD; không fabricate receipt để ép replay hoặc COMPLETE.

## Reproduction

Kết quả thực thi mới, không dùng lại số C2b:

| Check | Kết quả |
|---|---|
| Focused C2c, source cuối | **50 passed**, 2 existing Pandas warnings, **9.08s** |
| Full regression, source cuối | **472 passed**, 0 failed, 0 skipped, 2 existing warnings, **91.11s** |
| Frozen Google manifest | **47/47** normalized hashes unchanged; baseline tests trong full suite pass |
| Scoped Python AST/whitespace | PASS / 0 issues |
| Whole-tree `git diff --check` | Vẫn fail tại pre-existing `docs/component_registry.md:14`; giữ unrelated work |

Final fixture root: `output/tc792f`. Evidence:
`output/m3c2c-offline-evidence-20261021/focused-current.xml`,
`regression-complete.xml`, `verification.json`, `source_hashes.json`.
Các intermediate XML/fixture outputs giữ nguyên; không coi kết quả intermediate
458/467/469 là final current-source validation.

Lần focused đầu 4 failed / 26 passed: một contention fixture mở JobStore dưới
writer lock rồi đợi readiness sai thứ tự; ba case chạm cùng bug cleanup C2c dùng
lease NULL sau khi M1 kết thúc job. Fixture được đồng bộ trước tranh chấp; code
cleanup dùng exact native owner/proof cho terminal session, không đổi M1 claim
hoặc cho expired ACTIVE token ghi. Toàn bộ case này đã chạy lại trong suite cuối.
Rà soát sau đó bổ sung regressions cho interstitial normalization và stale
optional-403 UI context. Không che các failure ban đầu bằng số pass cũ.

```powershell
Set-Location '<repository-root>'
$testRoot = 'output/t' + [guid]::NewGuid().ToString('N').Substring(0,5)
& 'python' -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp $testRoot tests/test_harness_restrictions.py
```

Thêm `-k 'process_crash or contention or serialized or child_rechecks'` để xem
fault/concurrency cases; `-k 'operator or release or verification'` cho release.
Full suite gồm M1/M2/M3-B/C1/C2a/C2b, TikTok DOM/replies/persistence/compaction/
integration/benchmark/network và `tests/test_google_baseline.py`.

```powershell
$testRoot = 'output/t' + [guid]::NewGuid().ToString('N').Substring(0,5)
if (Test-Path -LiteralPath $testRoot) { throw 'Fresh fixture output required' }
& 'python' -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp $testRoot `
  tests/test_harness_restrictions.py tests/test_harness_browser_commands.py `
  tests/test_harness_containment.py tests/test_harness_publication.py `
  tests/test_harness_tiktok_managed.py tests/test_harness_state.py `
  tests/test_harness_hardening.py tests/test_harness_recovery.py `
  experiments/tiktok_comment_dom/test_dom.py experiments/tiktok_comment_dom/test_replies_scroll.py `
  experiments/tiktok_comment_dom/test_persistence.py experiments/tiktok_comment_dom/test_compaction.py `
  experiments/tiktok_place_intelligence/test_live_dom_persistence.py `
  experiments/tiktok_access_benchmark/test_overture_study.py `
  experiments/tiktok_access_benchmark/test_reliability.py `
  experiments/tiktok_access_benchmark/test_network.py tests/test_google_baseline.py
```

## Remaining limitations / C2d readiness

- GO cho review C2c offline; NO-GO managed live. C2d cần được approve riêng.
- Live verification provider, operator authentication/verification UX và runtime
  migration chưa triển khai. Registry environment/schema extension phải được
  review lại trước khi dùng trên runtime DB; không dùng fake provider làm bypass.
- Crashed supervisor/expired owner còn OPEN session/unresolved intents sẽ human
  hold. Không có auto operator attestation hoặc automatic process-death inference.
- Unknown/incomplete access evidence giữ hold bảo thủ, có thể dừng khi UI chưa
  sẵn sàng. Offline fixtures không chứng minh live selector/readiness coverage.
- Restricted job có intermediate committed rows nhưng chưa seal receipt giữ
  nguyên data/pending events; không suy membership/coverage để tự finalize.
- Single host/physical DB, cooperative C2a/C1 paths; không chống direct SQL,
  rogue publisher, local admin, reflective Python hay multi-host legacy writers.
- Không rollback action đã admission, không exactly-once source collection,
  không power-loss guarantee. SESSION_FALLBACK/cross-run source-ID limits giữ nguyên.
- C2d còn cần end-to-end operator recovery/provenance và reviewed actual UC
  provider/profile/Element integration. Không LLM/Telegram hay live rollout ở đây.

Backup/rollback: use SQLite backup API on a quiescent fixture before installation;
reopen/validate the backup and rehearse restore into a separate fresh file. Do not
drop extension tables on an active database. Old code intentionally rejects an
extended database; rollback uses the pre-extension backup with matching lake and
evidence, never deletion of a latch. Existing runtime DBs are untouched.
