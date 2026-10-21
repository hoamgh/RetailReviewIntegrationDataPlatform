# M3-C1 — Job-scoped Parquet publication and recovery

Ngày: 2026-10-21. **Hoàn thành offline; STOP để review. Chưa cho phép managed live.**

Baseline: [M3-A](global_crawler_harness_m3a_assessment.md) và
[M3-B](global_crawler_harness_m3b_implementation.md). Giữ source fencing M3-B,
quiescence/takeover M1 và retry budget M2. Không browser, network, AI, Telegram,
Google production change, Git index/history change hoặc thay writer.

## 1. Audit findings và thay đổi

| Gap trước C1 | Hậu quả | Thay đổi tối thiểu |
|---|---|---|
| Legacy `flush()` chọn toàn bộ pending events | A có thể publish/ACK B | Thêm exact event selection và transaction guard vào chính writer hiện có; managed path chỉ truyền receipt IDs |
| Scope chỉ nằm trong payload JSON | Payload B hỏng có thể làm A không xác định scope | Routing facts trong existing harness history, cùng source commit; filter trước khi parse own payload |
| Receipt chứa IDs nhưng chưa pin toàn bộ payload | Nội dung outbox có thể đổi mà giữ event ID | Seal exact IDs và hashes của full payload trong existing checkpoint JSON, anchor hash vào history |
| Temp/rename/ACK chưa có publisher ownership | Hai publisher có thể tranh cùng file/temp | Windows Global named mutex cho cả physical DB và lake; fail-closed khi không lock được |
| Control-token check không fence source ACK | Stale publisher có thể ACK sau takeover | `BEGIN IMMEDIATE` trên chính source connection, check owner/generation/attempt/lease, recheck deadline trước commit |
| M2 global flush/global running slot | Unrelated pending/recovery có thể block A | C1 override running-attempt selection theo job; dùng guarded scoped flush; chặn M2 cũ cho active C1 jobs |
| Caller có thể vô tình gọi legacy flush trên managed DB | Đi vòng qua lock/scope path | Legacy flush kiểm tra C1 metadata bên trong write transaction và từ chối publication trên DB đó |

Các protections legacy không bị bỏ để tests pass. Historical unindexed data
không tự được gán job. Cross-run source-ID conflict của M3-B vẫn fail-closed.

## 2. Code và integration points

| File | Vai trò C1 |
|---|---|
| [event_scope.py](../../crawler_harness/event_scope.py) — mới | Trusted routing, own-payload validation, checkpoint seal validation |
| [publication.py](../../crawler_harness/publication.py) — mới | `JobScopedPublication`, Windows exclusive ownership, guarded ACK/finalize; reuse `OfflineRecovery` |
| [tiktok_adapter.py](../../crawler_harness/tiktok_adapter.py#L96) | Commit observer ghi routing + final manifest atomically với source rows/checkpoint/receipt |
| [persistence.py](../../experiments/tiktok_comment_dom/persistence.py#L133) | Extend existing `flush(event_ids=..., publication_guard=..., fault=...)`; giữ raw schema/path/hash |
| [recovery.py](../../crawler_harness/recovery.py#L24) | Reuse exact file/content verification, C1-aware scope/seal; bảo vệ legacy M2 entrypoint |
| [state.py](../../crawler_harness/state.py#L421) | Finalize rechecks exact sealed event membership/payload/delivery trong transaction |
| [test_harness_publication.py](../../tests/test_harness_publication.py) — mới | 41 offline cases, bao gồm real Windows process crash/contention |
| [test_harness_tiktok_managed.py](../../tests/test_harness_tiktok_managed.py) | Cập nhật post-commit history expectation 4→6 do atomic routing + seal; giữ trước-commit expectation |

Không sửa DOM collector, browser backend, `run_dom_comments`, analysis compaction
hoặc Google frozen files. Những file uncommitted trước task vẫn được giữ nguyên.

## 3. Scope và sealed membership contract

Một job có unique stable `data_run_id` theo existing JobStore constraint. Scope
là **job_id + place_id + video_id + data_run_id**, không chỉ video ID.

1. Mỗi newly committed outbox event có `source.event_committed` history row:
   job/source/place/entity lấy từ owned job; code là event ID. Ghi trên cùng
   source transaction với event. Không đổi event payload hoặc identity formula.
2. Final source observer ghi `managed_publication_manifest` vào checkpoint JSON:
   version=1, exact scope, raw lake root, source_complete, stop_reason và mapping
   event ID → hash(full payload). `job.publication_sealed` history row pin digest
   manifest và source attempt/generation. Receipt giữ provenance gốc sau takeover.
3. Selection đọc IDs và trusted routing trước. Chỉ parse payload của job đang
   xử lý, sau đó xác nhận place/video/run, payload event ID và existing identity
   digest. Ambiguous/missing routing, missing indexed event, changed membership,
   seal hoặc payload mismatch đều dừng.
4. Payload B hỏng không làm A parse nó khi routing còn hợp lệ. Khi recover B,
   lỗi được giữ thành durable failure/hold; không âm thầm bỏ corrupted event.
5. Nếu DB có event chưa index, C1 giữ `UNINDEXED_OUTBOX_EVENT` thay vì đoán scope.
   Mixed historical/managed DB cần kế hoạch backfill riêng đã review. Đây là
   giới hạn isolation có chủ đích, không phải auto migration.

Metadata là trusted application contract trên SQLite, không phải chống attacker
có quyền sửa trực tiếp database/history. Không tự thêm scope columns/tables.

## 4. Publication, authority và Windows ownership

API offline: `JobScopedPublication(store, lake).recover(job_id, token,
verify_quiescent=..., evidence_ref=...)`. Không tự claim/takeover/heartbeat/retry.
Callback quiescence là operator attestation, **không phải process-death detector**.

Windows ownership dùng hai named mutexes `Global\\IntegrationRecommendationPublisherV1_*`,
key từ physical `(kind, st_dev, st_ino)` của DB và lake, acquire theo thứ tự cố định,
nonblocking. Cùng DB/different lake, different DB/cùng lake và DB hardlink alias
đều tranh quyền. Process-local guard chặn Windows same-thread mutex reentrancy.
Busy trả `PUBLISHER_BUSY`, không tiêu recovery budget. Không có TTL/lockfile fallback.
Windows API/identity/permission không khả dụng thì fail-closed.

OS giải phóng mutex của process chết; `WAIT_ABANDONED` chỉ cho phép acquire mutex,
không chứng minh worker/browser descendants đã dừng. C1 vẫn yêu cầu explicit
quiescence và current fenced claim. Lease expiry không tự cấp takeover.

Trong thời gian giữ cả mutex:

1. Reserve một durable M2 retry slot, kiểm tra seal/receipt/checkpoint/contents.
2. Với từng receipt event, mở `JobStore.source_transaction(repo.db, token)`:
   `BEGIN IMMEDIATE`, validate owner/generation/attempt/lease và PERSISTING; đọc
   lại scoped event/seal/payload. Không dùng token check ngoài transaction làm proof.
3. Existing writer giữ nguyên tên `crawl_date=.../run_id=.../event-<id>.parquet`,
   schema, single-row representation, compression và payload semantics. Write temp,
   managed fsync temp, revalidate rồi atomic rename trên cùng directory.
4. Target đã có: verify exact Arrow schema/metadata và full row; không overwrite.
   Re-read target trước ACK. ACK đúng event ID trong cùng guarded transaction.
   Revalidate authority/deadline tại các boundaries và trước commit.
5. Final transaction verify exact sealed membership, delivered flags, checkpoint,
   SQLite current contents, expected paths và Parquet schema/payload; set receipt
   verification flags, final state và recovery success/history atomically.

Filesystem rename và SQLite commit không thể là một atomic transaction. Nếu lease
hết trong IO, file hợp lệ có thể tồn tại nhưng ACK rollback; owner mới chỉ được
ACK sau verification. Resource identity được recheck, nhưng không bảo vệ trước
rogue direct filesystem mutation hay unmanaged binaries không tuân thủ contract.

## 5. Recovery và completion

| Trạng thái/boundary | Hành vi |
|---|---|
| Source committed, chưa publication | Replay exact receipt events, không recollect |
| Rename xong, ACK chưa commit | Verify file hiện có; ACK đúng ID; không tạo publication khác |
| ACK committed, chưa finalize | Verify delivered artifact, không rewrite; tiếp tục final transaction |
| COMPLETE/PARTIAL gọi lại | Read-only integrity audit dưới publisher lock; không thêm attempt/file/event |
| Missing/corrupt/schema mismatch/extra artifact | Fail-closed; không overwrite/recreate artifact; active job held với durable failure |
| Terminal artifact bị hỏng | Trả INTEGRITY_ERROR, không tự sửa terminal job hay republish |
| Receipt/seal thiếu hoặc mismatch | Không suy đoán membership/coverage, không COMPLETE |
| Source outcome chưa complete, ví dụ MAX_SCROLLS | PARTIAL sau reconciliation; pending của B không chặn finalization A |
| Genuine CAPTCHA/login/denial/429, unknown access evidence | Giữ existing restricted/human hold; không retry collection/browser |
| Storage interruption | Một step, durable bounded budget/backoff; không sleep/retry loop |

Final COMPLETE vẫn cần source completeness evidence + checkpoint exhausted + exact
durable reconciliation. UI access, extracted count và storage success không thay
thế coverage proof. SESSION_FALLBACK không bảo đảm cross-run deduplication hoặc
exactly-once source collection; unsupported source-ID refresh không thay semantics.

## 6. Tests và bằng chứng thực tế

Final regression trên Windows MSI: **349 passed, 2 warnings, 50.88s, zero skipped**.
Bao gồm 41 C1 cases và M1/M2/M3-B/TikTok legacy/Google baseline. Warnings có sẵn:
Pandas yêu cầu numexpr/bottleneck mới hơn; không cài/đổi dependency.

```powershell
Set-Location '<repository-root>'
$testRoot = 'output/t' + [guid]::NewGuid().ToString('N').Substring(0,5)
& 'python' -B -m pytest -q --import-mode=importlib -p no:cacheprovider --basetemp $testRoot `
  tests/test_harness_publication.py tests/test_harness_tiktok_managed.py `
  tests/test_harness_state.py tests/test_harness_hardening.py tests/test_harness_recovery.py `
  experiments/tiktok_comment_dom/test_dom.py experiments/tiktok_comment_dom/test_replies_scroll.py `
  experiments/tiktok_comment_dom/test_persistence.py experiments/tiktok_comment_dom/test_compaction.py `
  experiments/tiktok_place_intelligence/test_live_dom_persistence.py `
  experiments/tiktok_access_benchmark/test_overture_study.py `
  experiments/tiktok_access_benchmark/test_reliability.py tests/test_google_baseline.py
```

Để reproduce riêng: dùng cùng prefix/options, chỉ chọn
`tests/test_harness_publication.py`; thêm `-k process_crash` cho 8 crash cases,
hoặc `-k 'contend or mutex or hard_link'` cho Windows ownership tests.
Tất cả chạy fixtures/subprocess offline, không mở website.

| Test area | Bằng chứng |
|---|---|
| Shared DB A/B, same video/different place/run | A exact ACK, B events/pending/state không đổi |
| Malformed B/empty A | Routing-before-parse isolation; B vẫn bị hold khi được xử lý |
| Foreign event ID/changed seal/extra valid event | Guard hoặc finalize từ chối |
| Bad payload/schema/truncated/missing/extra file | Không overwrite, không COMPLETE, durable failure |
| Token expired trước write/sau rename/trước ACK/trước finalize | Không commit unauthorized ACK/final state |
| Old token sau explicit takeover | OwnershipError; không side effects được phép |
| Concurrent real publishers, physical aliases | PublisherBusy, không ăn budget B; release sau process exit |
| Repeated recovery/budget/backoff | Files/events không tăng; exhausted không thêm attempt |
| Legacy/global M2 bypass | C1 managed DB từ chối unguarded publication |

8 real-process fault cases: 4 boundaries × source_complete true/false. Child
`os._exit(81)`; parent xác nhận child exit rồi explicit takeover generation mới.
Mỗi fixture có 2 SQLite records và 2 events:

| Crash point | Files / ACK trước replay | Sau replay |
|---|---|---|
| before_publish | 0 / 0 | 2 files, 2 rows, pending=0 |
| after_rename | 1 / 0 | File đã có giữ nguyên, tổng 2 files, pending=0 |
| after_ack (first event) | 1 / 1 | Không publish lại delivered event; tổng 2 files |
| before_finalize | 2 / 2 | Chỉ reconcile/finalize, không thêm file |

Mỗi case kết thúc COMPLETE chỉ với explicit-end fixture; MAX_SCROLLS fixture kết
thúc PARTIAL. Receipt origin giữ nguyên. Hai repeated audits giữ nguyên files,
events và budget. Hai recovery slots: interrupted child + successful restart.

Evidence: [verification.json](../../output/m3c1-offline-evidence-20261021/verification.json),
[crash_recovery.json](../../output/m3c1-offline-evidence-20261021/crash_recovery.json),
[source_hashes.json](../../output/m3c1-offline-evidence-20261021/source_hashes.json).
Isolated raw fixtures được giữ dưới `output/t75b00/`; không sửa historical output.

Whole-tree `git diff --check` vẫn có pre-existing whitespace tại
`docs/component_registry.md:14`; không sửa file ngoài scope để che lỗi. Scoped
source/report whitespace và report local links được kiểm tra riêng trước handoff.

## 7. Compatibility, schema, backup và rollback

**Không DDL/schema migration:** SQLite/Parquet versions, columns, event hashes và
record representation giữ nguyên. Dùng existing `harness_events` và additive
checkpoint JSON. Constructor recovery có thể enable đúng existing M2 optional
3-table extension nếu chưa có; không thêm một schema/version mới. PRAGMAs không đổi.
Chỉ isolated test DB được dùng trong task này, không mở/migrate runtime crawl DB.

Historical M3-B jobs thiếu C1 seal/index không tự được upgrade. Legacy DB chưa
có C1 metadata vẫn dùng existing default writer/M2 path. Khi DB đã có C1 routing
hoặc seal, global legacy flush bị chặn. Không trộn unmanaged legacy writers với
managed DB; old binaries không được guard mới bảo vệ.

Trước áp dụng vào real DB: stop dispatch, xác nhận source workers và publishers
quiescent; tạo consistent SQLite backup bằng SQLite backup API, lưu nguyên raw
lake và receipt/checkpoint/history evidence, verify scope và hashes trên bản sao.
Không copy DB đang write như một backup transactionally consistent.

Rollback: disable C1 dispatch, giữ DB/metadata/receipts/pending/files nguyên trạng;
không drop tables, xóa history hoặc bỏ seal. Không chạy old global writer trên C1
DB để drain pending. Có thể chạy legacy riêng trên isolated output/DB khác. Backfill
historical scope hay downgrade execution trên bản sao phải được review riêng.

## 8. Residual risks và dependencies M3-C2

1. **Single-host Windows, cooperating paths only.** Global mutex cần permissions
   và stable filesystem resource IDs. Không distributed lock, chống direct SQL,
   malicious process hoặc external filesystem replacement.
2. **Process crash được test; power loss chưa được bảo đảm.** Temp fsync không
   chứng minh parent-directory/hardware/power-loss durability. Không thay existing
   SQLite writer settings, không claim atomic DB+filesystem commit.
3. **Quiescence còn là explicit attestation.** M3-C2 phải quản lý source/browser/
   publisher process identity, descendants, stop/join và safe takeover; không dùng
   lease expiration hay mutex abandonment như process-death proof.
4. **Missing final observation/receipt chưa reconstruct.** Crash giữa collection
   giữ checkpoint nhưng cần policy được duyệt; không fabricate COMPLETE/receipt.
5. **Live source/session restriction latch và operator release chưa có.** C2 cần
   độc lập browser lifecycle, bounded technical recovery và no evasion semantics.
6. **Unindexed historical/mixed DB và scale chưa giải quyết.** Fail-closed hiện tại;
   index/history scan và exact lake scan chưa benchmark lớn. Không thêm grouped
   writer/small-file optimization; analysis compaction vẫn riêng biệt.
7. **C1 chưa đưa managed adapter vào live runner.** Browser supervision/rollout,
   live identity/coverage và operator recovery flow còn phải review/test riêng.

GO cho review và bước offline tiếp theo sau approval. **NO-GO cho managed live
hoặc tự động chuyển M3-C2 trong task này.**
