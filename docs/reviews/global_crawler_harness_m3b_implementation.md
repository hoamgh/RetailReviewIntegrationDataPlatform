# Global Crawler Harness M3-B — Managed TikTok source commits

Ngày: 2026-10-21. Baseline: [M3-A assessment](global_crawler_harness_m3a_assessment.md).
Phạm vi: **OFFLINE ONLY**. Không chạy TikTok/browser/network, không AI/Telegram.

## 1. Kết quả và boundary

Đã tích hợp thin adapter qua runner/collector/persistence TikTok hiện có. Source rows,
outbox events, collector checkpoint, final collection observation, receipt và state
seal cùng chịu authority contract trên **một source SQLite transaction**.

Không triển khai M3-C publication/recovery/session supervisor. Managed collection
không flush Parquet hoặc acknowledge outbox: kết quả đủ collection evidence vẫn
dừng ở `PERSISTING`. Chưa được coi là một job COMPLETE/PARTIAL trước reconciliation.
Legacy runner giữ hành vi cũ khi không truyền managed hooks.

## 2. Files changed và API thực tế

| File | Thay đổi M3-B |
|---|---|
| [state.py](../../crawler_harness/state.py#L185-L243) | Thêm `source_transaction(connection, token)` và nested joining chỉ cho borrowed source view; tái sử dụng owner/generation/attempt/lease validation và public receipt/transition methods. |
| [tiktok_adapter.py](../../crawler_harness/tiktok_adapter.py) | File mới: `TikTokManagedAdapter.prepare/execute/repository/guard/observe`; offline fixture port, pinned config, stable data-run, per-attempt evidence, atomic seal và conservative holds. |
| [persistence.py](../../experiments/tiktok_comment_dom/persistence.py) | Optional `commit_guard`, `commit_observer`, `publication_enabled`; preflight cross-run source-ID conflict; `final_observation` keyword; extract storage identity helper không đổi hash/event semantics. |
| [place-intelligence probe.py](../../experiments/tiktok_place_intelligence/probe.py#L452) | Dùng chung `dom_input_digest`; inject repository factory; defer publication; truyền final stop vào final persistence; managed failures propagate sau cleanup. |
| [test_harness_tiktok_managed.py](../../tests/test_harness_tiktok_managed.py) | Fake port, source connection checks, expiry/takeover contention, OS-process crash, receipt/coverage/identity/retry/hold tests. |
| Báo cáo này, current_task.md, work_status.md | Implementation evidence và handoff; không đổi các source khác. |

Collector DOM/JS, browser factory/backend, access classifier, M2 recovery implementation,
benchmark và Overture entrypoint không được sửa trong M3-B. Không tạo writer khác.
Không Git staging/commit/push/reset/clean, không xóa dữ liệu.

## 3. Cùng DB, khác connection: contract atomic

`JobStore` và `CommentPersistence` vốn mở connection riêng đến cùng TikTok DB.
Kiểm tra Claim trên connection JobStore rồi commit qua repo là check-then-write,
không đủ fencing. Implementation mới thực hiện:

1. `source_transaction` kiểm tra `PRAGMA database_list`: main source DB phải đúng
   file JobStore, dùng `sqlite3.Row`, không có transaction mở sẵn. Không attach hoặc
   tạo schema từ borrowed view.
2. Borrowed JobStore view dùng **repo.db** và clock hiện có; mở `BEGIN IMMEDIATE`.
3. Validate token trên source connection: job, owner, generation, attempt, active
   lease. Adapter yêu cầu state RUNNING, source TikTok/collection, exact
   place/video/data-run/input digest và progress không âm.
4. Preflight tất cả keys source-ID trước source DML. Sau đó existing writer ghi
   current rows, revisions/events và collector checkpoint như trước.
5. Final observer dùng chính borrowed view để `record_receipt` và `transition`.
   Các API này join transaction hiện tại, **không commit sớm** hoặc mở connection
   thứ hai dưới lock. Nested failure bị bắt rồi nuốt vẫn đánh dấu transaction
   failed; outer transaction không được commit.
6. Outer transaction recheck owner/generation/attempt và **original lease deadline**
   ngay trước commit; rollback cả source và ledger khi invalid/expired.

Không giữ SQLite write lock trong lúc browser wait. Không claim rằng deadline
check bảo đảm OS/fsync hoàn tất trước expiry; SQLite lock bảo đảm takeover không
chen vào transaction đang commit. Đây là cooperative API safety, không bảo vệ
được direct SQL hoặc legacy writer cố tình bypass guard.

## 4. Identity, attempts và input contract

- Job definition dùng API M1, `input_digest` dùng đúng helper trích từ runner cũ;
  options zero vẫn được bỏ theo công thức cũ. Strict `validate_dom_match` giữ nguyên.
  Không bật query-only association trong managed adapter.
- `workflow` là refresh epoch/version do caller chọn; cùng definition trả cùng job.
  `execute` không tạo job khác khi input/config không khớp.
- Source `run_id` luôn là `harness_jobs.data_run_id`, không phải attempt UUID.
  Retry/takeover cùng job giữ fallback keys và event identity.
- Diagnostics nằm dưới `evidence_root/<job_id>/<attempt_id>`; không ghi đè attempt cũ.
  Adapter không tự claim/takeover, không tạo/đổi browser. `resume=True` chỉ dùng port
  hiện có theo contract runner cũ; chưa có process/browser recovery.
- `execute` từ chối port thiếu `harness_offline_fixture=True`. Đây là explicit test
  contract của trusted caller, không phải detector có thể chứng minh object không
  kết nối mạng. Tất cả ports được dùng trong tests là fake.

## 5. Atomic final observation và outcome mapping

Intermediate source commits có checkpoint nhưng chưa có final receipt. Final save
lưu thêm JSON `managed_collection_observation` version 1 trong checkpoint hiện có:
job ID, attempt ID, generation, actual stop reason và source-complete flag. Trong
cùng transaction, receipt ghi exact committed event membership và source checkpoint
ref, rồi ledger chuyển trạng thái.

| Quan sát | Atomic ledger result | Coverage/publication |
|---|---|---|
| MAX_SCROLLS, COMMENT_LIMIT, bounded no-growth, missing scroll surface, known comment timeout | PERSISTING | `source_complete=False`; pending giữ nguyên. |
| Explicit end/empty + checkpoint exhausted=True + không yêu cầu replies | PERSISTING | Source complete có bằng chứng; publication/reconciliation vẫn False. |
| Explicit end nhưng thiếu exhaustion hoặc yêu cầu replies | PERSISTING | Không source COMPLETE. Reply coverage vẫn partial. |
| AUTH_GATE/CHALLENGE/denial/429 | ACCESS_RESTRICTED | Receipt giữ evidence/outcome; không flush/retry/browser restart. |
| Unknown/ambiguous-required access/layout | PAUSED_NEEDS_HUMAN | Không suy thành CAPTCHA-free hoặc collection success. |
| Unknown exception trước final seal | PAUSED_NEEDS_HUMAN nếu còn owned RUNNING | Không fabricate receipt. Nếu đã mất quyền, không ghi hold. |

Nếu checkpoint terminal/restricted đã commit nhưng final receipt chưa tồn tại do
crash, adapter không chạy UI tiếp: restriction thành ACCESS_RESTRICTED; COMPLETE/
FAILED source checkpoint thiếu final seal thành PAUSED_NEEDS_HUMAN. Không suy
COMPLETE từ checkpoint, count, UI visibility hoặc summary JSON.

Final observation đã seal làm job rời RUNNING: managed source writes tiếp theo bị
từ chối. Publication flags không được tự nâng True. M2 verifier có thể đọc exact
managed receipt/checkpoint với `allow_unpublished=True` trong fixture; điều này
không thực hiện delivery, và không biến M3-B thành scoped recovery M3-C.

## 6. Cross-run source-ID protection

Existing identity/event semantics không đổi. Với managed writes, cùng source-ID
key đã thuộc data-run khác sẽ raise `SourceRunConflict` **trước bất kỳ source DML**,
kể cả batch có record mới xuất hiện trước record conflict. Test bao gồm nội dung
UNCHANGED và changed; old current/event/checkpoint/receipt không đổi.

Adapter đặt job mới vào PAUSED_NEEDS_HUMAN bằng control-plane transaction có guard;
không sửa receipt cũ hoặc bịa UPDATE event. Legacy writes không bật guard giữ hành
vi cũ. Đây là giới hạn refresh có chủ đích: cần per-run observation/reference
contract được duyệt trước khi hỗ trợ full cross-run source-ID refresh. Với
SESSION_FALLBACK, cross-run source deduplication vẫn không được bảo đảm.

## 7. Fault injection và evidence

[Tests mới](../../tests/test_harness_tiktok_managed.py) dùng fake port, SQLite thật,
separate connections và subprocess thật. Không cần AI.

| Fault | Oracle đã kiểm tra |
|---|---|
| Stale owner/generation/attempt; expired lease trên source connection | Source rows/events/checkpoint/receipt không commit. Clock cũ sau takeover vẫn bị generation fence từ chối. |
| Lease hết trong final seal | Rollback source + final observation + receipt + state/history; job vẫn RUNNING. |
| Process exit 81 trước commit | 0 comments, 0 events, 0 source checkpoints, 0 receipts; claim/attempt có trước giữ nguyên; history chỉ created/claimed. |
| Process exit 81 sau commit | 1 comment, 1 event, 1 checkpoint, 1 receipt; state PERSISTING; exact receipt IDs và observation provenance khớp. |
| Parent takeover khi child đang giữ source transaction | Lease nhìn từ contender đã hết nhưng BEGIN IMMEDIATE bị lock; sau child rollback/exit được parent xác nhận, explicit takeover tăng generation. Không dùng lease expiry làm death detection. |
| Same job retry | Stable data-run, event ID set không tăng; checkpoint phục hồi qua port fake hiện có; final receipt thuộc attempt seal thực tế. |
| Cross-run ID conflict | Không source INSERT/UPDATE/DELETE trong SQL trace; old receipt giữ nguyên, new job hold. |
| Mismatch DB/scope/config; swallowed nested error | Reject/rollback trước source commit; config mismatch không tạo job mới/UI action. |
| Gate/unknown/unsealed terminal checkpoint | Hold đúng loại; không UI retry, fake receipt hoặc COMPLETE. |

Artifact tổng hợp local: `output/m3b-offline-evidence-20261021/verification.json`.
Fixture DB dưới short `output/t<5hex>/`; không xóa chúng trong task. Process tests
không giả lập mất điện, disk failure hoặc publish concurrency.

### Reproduction

Focused:

```powershell
Set-Location '<repository-root>'
$testRoot = 'output/t' + [guid]::NewGuid().ToString('N').Substring(0,5)
& 'python' -B -m pytest -q `
  --import-mode=importlib -p no:cacheprovider --basetemp $testRoot `
  tests/test_harness_tiktok_managed.py
```

Chọn fault cụ thể bằng `-k process_crash` hoặc `-k contends`; expiry/source fence
bằng `-k expired` / `-k expiry` / `-k stale`. Không chạy CLI TikTok live để xác nhận
M3-B. Fake runner example nằm trong test
`test_adapter_calls_existing_collector_seals_partial_and_defers_publication`.

Broader regression:

```powershell
$testRoot = 'output/t' + [guid]::NewGuid().ToString('N').Substring(0,5)
& 'python' -B -m pytest -q `
  --import-mode=importlib -p no:cacheprovider --basetemp $testRoot `
  tests/test_harness_tiktok_managed.py tests/test_harness_state.py `
  tests/test_harness_hardening.py tests/test_harness_recovery.py `
  experiments/tiktok_comment_dom/test_dom.py `
  experiments/tiktok_comment_dom/test_replies_scroll.py `
  experiments/tiktok_comment_dom/test_persistence.py `
  experiments/tiktok_comment_dom/test_compaction.py `
  experiments/tiktok_place_intelligence/test_live_dom_persistence.py `
  experiments/tiktok_access_benchmark/test_overture_study.py `
  experiments/tiktok_access_benchmark/test_reliability.py `
  tests/test_google_baseline.py
```

Fresh final results: **308 passed, 2 warnings, 37.40s**; gồm **36 managed cases**
trong module M3-B. Hai warning numexpr/bottleneck là dependency warnings có sẵn,
không cài/nâng package. Focused trước khi bổ sung ba terminal-checkpoint cases:
33 passed trong 3.56s; ba cases mới đã chạy trong final broader suite. Google
baseline và M1/M2 regression đều pass. Không dùng lại số liệu milestone trước.

`git diff --check` toàn tree vẫn báo whitespace có sẵn ở
`docs/component_registry.md:14`; không sửa file ngoài phạm vi để che lỗi. Các
source/docs M3-B kiểm tra riêng không có trailing whitespace; local links của
báo cáo đều resolve.

## 8. Compatibility, schema và rollback

- **Không schema migration:** SQLite DDL/versions/columns và Parquet schema v1 giữ
  nguyên; không thêm harness tables. Additive checkpoint JSON field được parser
  cũ bỏ qua, đã test `CommentSession.from_checkpoint` và JobStore reopen.
- Không thay journal_mode/synchronous/foreign_keys settings. Source và harness
  connection vẫn có cấu hình riêng; authority checks là SQL application contract.
- Default writer guard/observer=None, publication enabled=True; default runner
  dùng repo hiện có và flush như trước. Managed exception propagation chỉ bật khi
  inject repository_factory; CLI cũ không tự chuyển managed mode.
- Rollback: ngừng dispatch/đóng managed source owners; bỏ sử dụng adapter/hook
  injection. Không cần downgrade/drop schema, không xóa receipts/checkpoints/events.
  Pending source data phải giữ nguyên để reviewed offline delivery/reconciliation;
  không chạy global legacy flush lên shared managed DB khi chưa xác nhận quiescence
  và exact pending scope. Sealed jobs không được recollect để thay replay.
- Có thể quay về legacy trên **output/DB riêng**. Không trỏ legacy writer không
  guard vào cùng DB đang được managed adapter sử dụng.

## 9. Residual risks — M3-C còn phải làm

1. Existing flush vẫn whole-DB và unfenced cho legacy; M3-B managed mode cấm gọi nó.
   Cần scoped writer invocation, atomic guarded ack và exact artifact verification.
2. Filesystem rename/temp collisions, exclusive publisher lock, quiescence và
   process/browser lifecycle chưa được triển khai. Source fencing không fence click,
   screenshot, JSON/CSV hoặc publisher ngoài managed route.
3. Không automatic takeover/retry/heartbeat supervisor. Lease expiry không cho phép
   tự hồi sinh worker; clock và explicit quiescence contract M1 giữ nguyên.
4. Receipt thiếu do crash ở intermediate checkpoint: chưa có reconstruction/final
   PARTIAL recovery policy. Adapter fail-closed; M2 hiện giữ missing-receipt hold.
5. M2 whole-DB recovery serialization, unrelated pending/malformed payload và
   video-only benchmark reconciliation chưa sửa. Đây là dependencies M3-C, không
   được bỏ các check fail-closed để làm integration chạy được.
6. Chưa durable source/session restriction latch hoặc operator release/unpause.
   Job restricted được giữ, nhưng không có source scheduler live trong M3-B.
7. Cross-run source-ID refresh unsupported; SESSION_FALLBACK không exactly-once.
   Complete comment/reply coverage và provider capacity vẫn chưa được chứng minh.

**STOP sau M3-B để review.** Không M3-C/D, không live rollout hoặc model local.
