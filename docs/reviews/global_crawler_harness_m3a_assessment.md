# Global Crawler Harness M3-A — TikTok integration and recovery safety

Ngày: 2026-10-21. Trạng thái: **DESIGN/AUDIT — chờ review**.

## 1. Kết luận và phạm vi

**NO-GO cho harness điều phối live ở thời điểm audit. GO cho triển khai M3-B offline sau khi duyệt các thay đổi contract bên dưới.** M1/M2 là nền tảng control plane và offline recovery; chưa fence source writer, publisher hoặc browser. Bọc CLI hiện tại bằng `claim()` không đủ.

Không chạy live/browser, không thêm AI/Telegram, không đổi code hay schema trong M3-A. Google frozen giữ nguyên. Đã đọc Git status trước audit; giữ các chỉnh sửa/untracked hiện có, kể cả thay đổi TikTok readiness/compaction trước task này. Báo cáo không coi các thay đổi đó là tính năng harness.

Ưu tiên một worker, một browser/session, một job collection cho mỗi place/video/data-run; không mở rộng distributed execution. Dùng SQLite hiện hữu và writer hiện hữu; không thêm database service, crawler hoặc writer cạnh tranh.

## 2. Current architecture và reuse map

| Thành phần thực tế | API/điểm nối | Vai trò và giới hạn |
|---|---|---|
| [JobStore](../../crawler_harness/state.py#L134-L200) | `JobStore(database, clock=...)`; `_transaction(token)` | Connection riêng, BEGIN IMMEDIATE; check owner/generation/attempt/lease và deadline trước commit. Chỉ fence mutation trên connection này. |
| [Job identity/claim](../../crawler_harness/state.py#L231-L318) | `create_job(...)`, `claim(...)`, `takeover(...)`, `heartbeat(...)` | Stable job hash, stable `data_run_id`, unique attempt/run IDs, operator-attested takeover. Không tự claim lại RUNNING khi lease hết. |
| [Receipt/finalization](../../crawler_harness/state.py#L328-L435) | `record_receipt(...)`, `transition(...)` | Scope event đúng place/video/data-run, seal receipt khi PERSISTING; finalize reread membership và pending. Publication/coverage ban đầu là trusted attestations. |
| [OfflineRecovery](../../crawler_harness/recovery.py#L186-L263) | `recover(job_id, token=None, verify_quiescent=..., evidence_ref=...)` | Một bước bounded, không browser/recollect/takeover; exact artifact checks; giữ restriction. |
| [Collection integration](../../experiments/tiktok_place_intelligence/probe.py#L452-L581) | `run_dom_comments(match_data, *, output, database, parquet_root, run_id, port=..., resume=..., ...)` | Điểm adapter nên gọi; đã dùng collector, Storage và CommentPersistence. Chưa có guard/scope/receipt hook. |
| [DOM collector](../../experiments/tiktok_comment_dom/probe.py#L99-L371) | `collect_video(..., collector=..., on_collect=..., checkpoint=..., resume_in_place=...)` | Thu thập, onboarding, scroll, replies và bounded waits hiện có. Không viết collector khác. |
| [DOM/browser port](../../experiments/tiktok_comment_dom/probe.py#L372-L556) | `SeleniumDOMPort(driver=..., network=...)`; navigation handler; snapshot/wait/scroll/expand | Inject browser đang sở hữu; tránh implicit browser creation trong harness. Không phải AI action loop. |
| [Source persistence](../../experiments/tiktok_comment_dom/persistence.py#L29-L129) | `persist(...)`, `load_checkpoint(...)`, `flush()`, `summary()` | Comment/current + event + collector checkpoint cùng SQLite transaction. Flush hiện whole-DB, một event/file. |
| [Storage callback](../../experiments/tiktok_access_benchmark/crawler.py#L83-L122) | `Storage.persist_collector(...)` | Delegate vào repo; JSONL/CSV/summary là diagnostics, không thay DB receipt. |
| [Benchmark orchestration](../../experiments/tiktok_access_benchmark/reliability.py#L460-L588) | `run_live(..., existing_port=..., persistence_root=...)` | Orchestrate nhiều video, append run suffix theo index; không dùng trực tiếp làm per-job adapter. |
| [Overture entrypoint](../../experiments/tiktok_access_benchmark/overture_study.py#L315-L392) | `automatic_crawl(...)` | Discovery/search-card navigation rồi collection; candidate mapping có thể UNVERIFIED. Giữ làm legacy route. |
| [Analysis compaction](../../experiments/tiktok_comment_dom/compaction.py) | `compact(...)`, `finalize_analysis(...)` | Sealed snapshot đọc whole source khi writer đã dừng. Không phải raw outbox publication receipt. |

Luồng managed đề xuất:

```text
Reviewed place/video match → stable job → claim/attempt
  → borrowed existing port → run_dom_comments → existing collector
  → guarded existing persist + collector checkpoint
  → durable outcome/receipt seal → guarded/scoped existing flush
  → exact raw reconciliation → COMPLETE hoặc PARTIAL
```

Discovery và collection là job khác nhau. M3-B/C trước hết chỉ nhận match đã chuẩn bị; không kéo cả Overture study vào adapter. NO_MATCH là kết quả discovery hợp lệ, không phải collection success. Matching chưa rõ phải giữ provenance/quarantine; không dùng `allow_query_candidates=True` mặc định để hợp thức hóa branch mapping.

## 3. Safety gaps với references

Severity dưới đây đánh giá rủi ro **nếu tích hợp live/shared DB**, không quy kết các giới hạn đã công bố thành bug M1.

| ID / severity | Trigger, code reference | Invariant/hậu quả | Thay đổi tối thiểu cần thiết |
|---|---|---|---|
| G1 CRITICAL blocker | Worker A mất generation/lease nhưng gọi [persist:58–101](../../experiments/tiktok_comment_dom/persistence.py#L58-L101). Writer không nhận Claim. | Current row, outbox, checkpoint vẫn commit sau mất quyền; control-plane rejection không bảo vệ dữ liệu. Đã tái hiện bằng fixture. | Guard trên **chính connection source**, trong cùng BEGIN IMMEDIATE và kiểm tra trước commit; không check trước ở JobStore rồi ghi qua repo connection. |
| G2 CRITICAL blocker | [flush:108–120](../../experiments/tiktok_comment_dom/persistence.py#L104-L120) select toàn bộ pending, rename rồi ack không owner check. | Token A có thể publish/ack B; concurrent publishers dùng cùng `.tmp`, rename có thể thay artifact nếu cùng vượt `exists()`. | Scoped exact event set + guard ack + exclusive DB/lake publisher lock; mọi publisher managed phải tham gia. Existing content phải được verify trước ack, không overwrite mismatch. |
| G3 HIGH, conservative M2 limit | [recover:219–230](../../crawler_harness/recovery.py#L219-L230) thấy unrelated pending thì từ chối; [_reserve:139–148](../../crawler_harness/recovery.py#L139-L148) serialize mọi recovery trong DB. | M2 không publish B nhưng A bị hold/budget failure bởi B. Có thể head-of-line blocking sau recovery B chết. Không phải per-job independent recovery. | M3-C dùng scoped flush đã duyệt; isolation test A/B; giữ M2 conservative route làm fallback, không xóa check để gọi global flush. |
| G4 HIGH blocker | [run_dom_comments.save:478–497](../../experiments/tiktok_place_intelligence/probe.py#L478-L497), [startup flush:509](../../experiments/tiktok_place_intelligence/probe.py#L500-L523). | Ghi trước, JSON checkpoint, flush, receipt là các bước rời; commit rồi chết trước receipt khiến M2 không biết coverage. Startup cũng có thể giao event của job khác. | Inject guarded repository/commit observer vào runner, atomic final outcome seal + final source checkpoint + receipt observation. Startup flush cũng phải scoped. Không chỉ sửa outer call. |
| G5 HIGH recovery limit | [resume:468–470](../../experiments/tiktok_place_intelligence/probe.py#L466-L470), [resume_in_place:519–521](../../experiments/tiktok_place_intelligence/probe.py#L519-L521). | `resume=True` yêu cầu port và URL cũ; mất process/browser không đồng nghĩa có thể tiếp tục UI từ checkpoint. | Tách delivery-only recovery khỏi live resume; khác browser thì mặc định hold partial, không tự mở lại/recollect. |
| G6 HIGH blocker | [reconcile:98–123](../../experiments/tiktok_access_benchmark/reliability.py#L98-L123) chỉ lọc video ID; [summary:124–127](../../experiments/tiktok_comment_dom/persistence.py#L124-L127) count whole DB; benchmark tự tạo run suffix. | Receipt/counters có thể trộn place hoặc refresh run cùng video. | Adapter dùng exact `(place_id, video_id, data_run_id)` và receipt IDs; attempt UUID không được dùng làm data-run. Không lấy benchmark aggregate làm completion evidence. |
| G7 HIGH, cross-run semantic limit | [persist:66–98](../../experiments/tiktok_comment_dom/persistence.py#L66-L98): observed source-ID key có scope `video`; UNCHANGED đổi current `run_id` nhưng không tạo event. [verify:97–105](../../crawler_harness/recovery.py#L97-L105) so current với event/latest theo job. | Run B có current row nhưng không có event B; hoặc B sửa current khiến replay A fail. SESSION_FALLBACK không gặp cùng key giữa run nhưng lại không bảo đảm source dedup. Đã tái hiện UNCHANGED. | M3-B/C chặn cross-data-run source-ID mutation trước commit và báo explicit hold. Refresh support cần versioned per-run observation/reference contract được duyệt; không bịa UPDATE event, không bỏ verification. |
| G8 HIGH, isolation limit | [scoped_events:26–35](../../crawler_harness/recovery.py#L24-L41), [state._reconciled:393–396](../../crawler_harness/state.py#L389-L401) parse payload của mọi job trước lọc. | Malformed JSON của B có thể làm recovery/finalize A lỗi. | Index scope từ metadata ràng buộc event hoặc mapping có kiểm chứng, lọc trước payload parsing; nếu thiếu trusted metadata thì hold cả DB vì không biết event hỏng thuộc ai. Không âm thầm bỏ event hỏng. |
| G9 MEDIUM/high at scale | [verify:94](../../crawler_harness/recovery.py#L94-L105) scan lake; [compaction](../../experiments/tiktok_comment_dom/compaction.py) đòi toàn source reconciled. | Extra file cùng run làm fail đúng; lake lớn và compaction toàn DB gây phụ thuộc job khác. | Giữ raw receipt là event/file; compaction chỉ maintenance sau quiescence, không gate job A bởi grouped output toàn DB. Không union raw + analysis. |
| G10 HIGH lifecycle gap | [study finally:385–392](../../experiments/tiktok_access_benchmark/overture_study.py#L385-L392), collector nhận driver trực tiếp. | Hold job không đồng nghĩa giữ browser; stale code có thể còn click hoặc ghi diagnostics cùng output path. | Supervisor sở hữu lifecycle riêng, per-attempt evidence folder, guarded port trước/sau thao tác, exclusive session lock. Không dùng `automatic_crawl` outer finally làm human-hold controller. |

### Receipt/coverage gaps không được che bởi count

- `persist` lưu collector pagination; `run_dom_comments` return `stop_reason` có thể chi tiết hơn pagination đã lưu. Adapter phải seal actual final stop riêng, không suy từ summary/count hoặc `session.pagination` duy nhất.
- `record_receipt` kiểm tra IDs được cung cấp thuộc scope; `_reconciled` mới kiểm tra **exact** toàn scope khi finalize. Receipt rỗng không chứng minh không có comment nếu checkpoint có records hay source-ID UNCHANGED.
- M2 verify exact raw payload/schema nhưng không có filesystem fencing. Nó loại `observed_at/run_id/provenance` khi so latest current với payload; không được diễn giải là full current-row byte equality.
- Không giả định `source_complete=True` do caller là bằng chứng tự đủ. M3 adapter phải áp restrictive coverage rules trước khi gọi API M1. MAX_SCROLLS/COMMENT_LIMIT/no-growth không được COMPLETE dù caller gắn evidence tùy ý.

## 4. Proposed thin adapter interface — chưa tồn tại

Tên dưới đây là **đề xuất**, không phải import/API đang hoạt động:

```python
validate_input(match_data, limits) -> ValidatedInput
# calls validate_dom_match; pins normalized source input and its exact digest

execute_collection(context, *, existing_port) -> AdapterResult
# calls run_dom_comments; injects proposed repository guard/observer hooks

recover_publication(context, *, quiescence_evidence) -> RecoveryResult
# scoped existing outbox writer + M2 verifier; never navigation/recollection

inspect_result(context) -> JobFacts
# exact receipt/checkpoint/files, separate collection and delivery outcomes
```

`context`: job ID, current Claim, stable data-run ID, immutable input digest, reviewed match reference, per-attempt output root, DB/lake identities và budget. `AdapterResult`: observed stop, source coverage, reply coverage, committed checkpoint ref, sealed event IDs, publication/reconciliation facts, access evidence refs. Không cho phép LLM cấp các cờ này.

Reuse `run_dom_comments`, không copy `collect_video/save/flush` sang adapter. Hooks dự kiến là guard/receipt observer/repository injection có mặc định legacy-compatible. Chính persistence hiện hữu vẫn xây row, hash, revisions, event IDs và đường file. Không tạo writer mới.

## 5. Job, attempt, checkpoint và state mapping

### Identity

- Một collection job = canonical place + TikTok video + pinned config/mapping + explicit refresh epoch/workflow. `create_job` hash definition; cùng definition trả lại cùng job, không tự chạy lại COMPLETE/PARTIAL.
- `data_run_id = harness_jobs.data_run_id` giữ nguyên qua retry/takeover. `harness_attempts.run_id` và `attempt_id` chỉ là execution correlation. Event `run_id`, fallback identity scope và source checkpoint dùng **data_run_id**.
- `input_digest` phải khớp chính xác phép tính [probe:466](../../experiments/tiktok_place_intelligence/probe.py#L466): normalized video/place/limits, bỏ option zero theo code hiện có. Không hash raw `match_data` rồi mong M2 checkpoint comparison pass. Pin snapshot kể cả fields metadata có trong digest; không refresh candidate stats trước resume. M3-B cần helper dùng chung phép tính hiện có, không hai công thức khác nhau.
- Folder evidence theo job/attempt, không phải identity dữ liệu. Checkpoint ref là bounded identifier trỏ bản ghi authoritative `(data_run_id, place_id, video_id)`; JSON/CSV không authoritative.
- `next_page` ở DOM flow là snapshot progress count, không phải cursor TikTok API; không dùng nó như điểm seek browser sau restart.

### Outcomes (không thêm state M1 trong M3-A)

| Bằng chứng/outcome | State M1 đề xuất | Yêu cầu |
|---|---|---|
| Explicit validated top-level end/empty, replies không được yêu cầu, receipt durable/reconciled | RUNNING → PERSISTING → COMPLETE | Tất cả điều kiện độc lập pass; UI heading/count/bottom không đủ. |
| COMMENT_LIMIT, MAX_SCROLLS, no-growth, missing safe scroll, bounded reply coverage | RUNNING → PERSISTING → PARTIAL | Chỉ finalize khi receipt + exact persistence pass. `source_complete=False`. |
| UI timeout kỹ thuật | PARTIAL nếu có receipt partial hợp lệ; FAILED/PAUSED nếu bằng chứng thiếu | Không gắn thành access denial; retry có budget và chỉ khi authority/session hợp lệ. |
| BLOCKED bởi dependency | Giữ PENDING, report `DEPENDENCY_BLOCKED` | `BLOCKED` không phải state schema hiện có. NO_MATCH discovery không tự cho collection chạy. |
| CAPTCHA/login/required denial/429 | ACCESS_RESTRICTED | Giữ evidence và pending committed data; không retry collection/session. |
| UNKNOWN layout/ambiguous 403/thiếu receipt/coverage | PAUSED_NEEDS_HUMAN khi cần quyết định; unknown coverage có thể PARTIAL nếu collection evidence đủ | UNKNOWN là thuộc tính evidence/outcome, không thêm state và không tự chuyển COMPLETE. |
| Persistence pending/mismatch | Giữ PERSISTING hoặc PAUSED_NEEDS_HUMAN tùy integrity evidence | UI success không thay persistence success; không finalize PARTIAL khi pending. |

M1 transition terminal hiện không có đường unpause/requeue. M3-A **không thêm** đường đó. Operator live verification không tự cấp Claim mới hoặc đổi receipt source coverage. Release/resume từ restricted cần contract riêng được duyệt; giữ source hold nếu chưa có.

## 6. Fencing strategy và contract cần duyệt trước M3-B/C

### 6.1 Source transaction

1. Adapter xác nhận input/path/token trước thao tác; đây chỉ là early rejection.
2. Existing `persist` trong managed mode mở BEGIN IMMEDIATE trên **repo.db**; kiểm tra job/source/place/video/data-run, active state, owner/generation/attempt và deadline trên cùng connection.
3. Upsert current, insert events, checkpoint qua code writer hiện hữu. Final seal ghi coverage observation và exact receipt membership trong cùng transaction; intermediate checkpoints chưa phải final receipt.
4. Revalidate original authority/deadline trước commit, rollback toàn bộ nếu mất quyền. Không giữ source write transaction xuyên qua browser wait.
5. Public JobStore calls hiện mở transaction trên connection riêng: không gọi chúng trong source write lock, tránh nested BEGIN/deadlock. Cần connection-aware internal helper tái sử dụng validation/event logic, không chép hai bộ SQL/fencing.

**Đây là extension contract, không phải tính năng đã có.** M1 public behavior và generation semantics giữ nguyên; source writer thêm managed guard/observer hooks. Chưa sửa schema. Nếu cần thêm per-run observation/session registry hoặc policy table, schema validator M1 hiện yêu cầu exact known `harness_*` tables và DDL: phải có explicit version/migration/rollback review, không thêm table lén rồi bỏ kiểm tra drift.

### 6.2 Filesystem publication và acknowledgement

- Serialize publisher theo canonical **DB + lake** identity bằng OS lock giữ handle, không TTL file/PID-only lock. Local Windows implementation cần kiểm thử release-on-process-death và path aliases. Scope ownership phải bao gồm source writers, publishers và compaction được quản lý.
- Existing `flush` cần tham số scoped event set và guard; selected pending IDs phải chính xác thuộc scope/sealed receipt. Unrelated job không được publish/ack. Truy vấn/ack có ràng buộc ID, scope và token trong transaction.
- Authority check ngay trước publication, verify existing target bằng exact payload/schema trước ack; no overwrite nếu mismatch. Ack dùng source connection guard trong transaction. Unique attempt temp hoặc serialization phải bảo đảm không hai publisher cùng sửa `.tmp`.
- Check lease rồi rename không thể tạo atomic transaction SQLite+filesystem. Nếu lease hết trong I/O, có thể có file hợp lệ nhưng chưa ack; receipt/replay phải xử lý trường hợp này. **Không tuyên bố filesystem fencing chỉ nhờ token.**
- Takeover không được chạy khi publisher cũ còn giữ lock/side effect in flight. Stable event identity và immutable payload bảo vệ replay; quiescence + exclusive lock ngăn old publisher tiếp tục sau takeover. Không force-delete lock file để bypass handle đang giữ.
- Legacy/direct SQL/external filesystem writers không tuân thủ lock/guard không thể được bảo vệ bởi cooperative adapter. Managed DB/lake phải có một owner route; nếu không xác nhận được mọi writer cũ dừng thì NO-GO. Không trỏ harness vào DB Google hay tự nâng guarantee cho legacy CLI.

### 6.3 Browser/session authority và quiescence

- Job authority, OS process ownership và authenticated profile là ba thứ riêng. Guarded wrapper gọi các method hiện có; kiểm tra trước/sau action và khi polling, nhưng một click đã dispatch không thể rollback/fence từ SQLite.
- Lease renewal cần connection/thread ownership đúng; không share `sqlite3.Connection` qua heartbeat thread. TTL chỉ là deadline, không chứng minh process chết. Mất authority thì dừng side effects, không cố lưu checkpoint bằng stale token.
- Supervisor giữ process handle + process creation identity + session UUID, quản lý worker/publisher và browser children của chính nó. PID/process-name absence riêng lẻ không đủ do PID reuse/child sống sót. Windows Job Object/process tree là phương án cần thử, không coi đã triển khai.
- Trước takeover: ngừng dispatch, xác nhận worker cũ và mọi publisher đã exit, browser owner cũ đóng/revoked và không còn command in flight, lock lấy được; lưu evidence ref. Nếu xác nhận không đủ, PAUSED_NEEDS_HUMAN. Không kill Chrome ngoài session được sở hữu.
- `verify_quiescent=True` là attestation của operator/verifier về các sự kiện trên, **không phải** automatic process-death detection. Lease expiry đơn lẻ không đủ; generation tăng vẫn trong transaction M1.

## 7. Per-job recovery protocol

1. Đọc job/receipt/checkpoint; terminal COMPLETE/PARTIAL không recollect. Repeated recovery chỉ read-only integrity audit như M2.
2. Restricted/ambiguous-access job giữ hold. M2 hiện trả HELD và không drain pending của restricted job; không bypass điều này bằng global flush. Operator-authorized storage-only drain nếu cần là contract mở, không tự cho phép collection.
3. Với active job đã chết, establish quiescence và explicit takeover trước. Receipt source observation phải giữ attempt/generation gốc; attempt mới chỉ revalidate delivery facts.
4. Có final receipt: scoped pending replay; verify exact membership, payload, schema, paths và source checkpoint; ack sau publication; finalize theo coverage. Sau publish/trước ack: verify file, không tạo file khác. Sau ack/trước finalize: verify rồi finalize, không UI.
5. Không có final receipt: khôi phục **intermediate** observation từ checkpoint chỉ khi format/version/source scope/input và source DB evidence khớp. Không suy đoán COMPLETE. M3-B nên atomic final seal để tránh mất outcome; interim crash có thể tạo receipt PARTIAL với explicit `PROCESS_INTERRUPTED` sau quiescent validation, nhưng cần API/design approval, M2 hiện không làm việc này.
6. Browser chết giữa video: committed partial data không mất; reply expansion `INTENT/CLICKED` không được replay tự động. Không commit bộ nhớ stale. M3-C có thể hoàn tất PARTIAL storage-only; browser recreation/recollection là job policy khác, không ngầm bật qua `resume=True`.
7. Job B pending/corrupt scoped không làm A publish B. Pending B bình thường không chặn A sau scoped implementation. Nếu event hỏng chưa có trusted scope metadata, hold DB và yêu cầu repair để tránh bỏ sót dữ liệu.
8. Existing current-state cross-run source-ID conflicts (G7) fail trước mutation trong managed mode. Không sửa global current/latest rule cho vừa test. Versioned observation references cần milestone riêng nếu muốn hỗ trợ full refresh; SESSION_FALLBACK chỉ dedup same data-run, không exactly-once source.

## 8. Access restriction policy

Reuse hiện trạng [network.py](../../experiments/tiktok_access_benchmark/network.py), [UIPort.state](../../experiments/tiktok_access_benchmark/crawler.py#L126-L149), [access_failure](../../experiments/tiktok_comment_dom/extractor.py), không tạo classifier thay thế.

| Evidence | Quyết định adapter |
|---|---|
| Active CAPTCHA/login modal/required auth denial | Lưu evidence giới hạn, ACCESS_RESTRICTED, dừng affected source/session dispatch; operator xác minh. Không giải, không restart để né gate. |
| HTTP 429 và Retry-After | Dừng, lưu endpoint category/time/header đã sanitize; không retry sớm. Hết Retry-After không tự chứng minh access restored. |
| Document/comments/search/video-detail 403 | Required restriction; không downgrade vì một screenshot cũ có comments. |
| Related-items/optional resource 403 | Telemetry riêng, không tự kết luận comments inaccessible. |
| Unknown 403 significance | PAUSED_NEEDS_HUMAN, giữ UNKNOWN; không skip/bypass. |
| Comment composer `Log in to comment` nhưng comments đọc được | Không đồng nhất với login modal. Dùng classifier/UI evidence hiện hữu; operator kiểm tra nếu mâu thuẫn. |

Job-scoped hold chưa đủ để stop các job kế tiếp dùng cùng session. M3-C cần durable source/session restriction latch hoặc tương đương explicit supervisor hold được kiểm tra khi dispatch. Cách lưu/release latch là open contract decision; không lén thêm state BLOCKED hoặc mở lại terminal job. M3-D không rollout nếu latch còn chỉ nằm trong RAM.

## 9. M3-B / C / D implementation gates

| Milestone | Phạm vi, dependency | Acceptance | Rollback |
|---|---|---|---|
| **M3-B: managed adapter + source commit boundary, offline** | Duyệt connection-aware guard/receipt hooks; pinned source digest; scoped repo injection; fake port; atomic intermediate commit và final seal. Chưa live. | Lost lease/token rejects source comment/outbox/checkpoint/receipt mutation trên cùng connection. Crash trước commit rollback tất cả; sau commit có durable exact observation. Same job retry giữ data-run và fallback keys. Unsupported cross-run source-ID refresh fail-closed. Legacy regression pass. | Managed route off; không dispatch mới. Đóng writers, replay qua reviewed safe path. Giữ additive metadata/evidence; không drop schema hoặc xóa outbox. Legacy chỉ chạy output riêng đã xác nhận không owner. |
| **M3-C: scoped publisher/recovery + process/session safety, offline** | M3-B pass; scoped existing flush; per-job verifier metadata; process/lock contract; source/session hold; explicit quiescent takeover. | A recovery không publish/ack B và không bị B pending bình thường chặn. Concurrent publisher/process faults pass. Restricted source không redispatch/restart. Budget/history durable; receipt origin preserved. Browser-loss fixture chỉ storage recovery hoặc explicit partial hold. | Disable managed publisher/dispatch; quarantine pending scope, preserve files/receipt. Không quay lại global flush trên shared managed DB. M2 fallback chỉ khi đáp ứng toàn bộ quiescence/unrelated-pending preconditions. |
| **M3-D: operator CLI/reporting + full offline rehearsal** | B/C pass, migration/release latch quyết định xong; runbook one-session/sequential; report job/attempt/source/data-run riêng. | Exact source-current/event/Parquet reconciliation trong test scope, checkpoint restart, failure reasons/coverage truthful; raw vs analysis tách; Google hashes không đổi. Mọi test matrix gate đạt, command live chỉ được chuẩn bị. | Route off; giữ legacy runner riêng và hồ sơ audit. Không auto live/crawl lại. Mọi rollback dữ liệu dùng backup/verified replay, không delete historical files. |

M3-D **không bao gồm** AI, Telegram, automatic code repair, grouped raw writer hoặc full Overture discovery rewrite. Live smoke là approval riêng sau offline gates. M3-A không cấp approval đó.

## 10. Test matrix và acceptance criteria

| Test | Fault injection / oracle | Gate |
|---|---|---|
| Atomic source fence | Hai connection/process tranh BEGIN IMMEDIATE; stale A tiếp tục sau takeover B. Kiểm tra source rows/events/checkpoint/receipt/history đều không có stale mutation. | B |
| Lease expires inside persist | Clock vượt original deadline trước commit, bao gồm final receipt insert. Tất cả rollback; heartbeat không hồi sinh token cũ. | B |
| A trước SQLite commit | Kill subprocess sau SQL write/before commit; reopen không có transaction dở dang. | B |
| B sau SQLite commit/trước publication | Kill worker; process mới không browser/model; verify checkpoint/receipt, scoped replay, pending A=0, payload exact, finalize theo coverage. | B/C |
| C sau rename/trước ack | Kill publisher; restart verify existing file, ack đúng ID. Không overwrite hoặc thêm file trùng. | C |
| D sau ack/trước finalize | Restart chỉ audit receipt/publications rồi finalize; source collector không gọi lại. | C |
| E browser crash giữa video | Mock driver disconnect/real fixture worker death, committed records giữ; uncertain reply click không replay; không fabricate full coverage. | C |
| Concurrent publisher | Dừng A ngay trước rename, cho B tranh lock; B không publish cho tới verified quiescence/ownership. Temp collisions/path alias tested. | C |
| Ownership lost during filesystem I/O | File có thể tồn tại unacked; old worker không ack/finalize; takeover không được publish đồng thời. | C |
| A/B isolation | Same DB, A và B pending; A chỉ xử lý A. B same video khác place/run vẫn tách. B recovery STARTED chết không được silently lấy slot. | C |
| Malformed payload B | Với metadata scope trusted: quarantine B, A độc lập; nếu không xác định scope: explicit DB integrity hold, không bỏ event. | C |
| Repeated resume/replay | Restart rồi recovery hai lần nữa; event set/payload/file set không đổi; không thêm collection hoặc attempt recovery cho terminal audit. | C/D |
| Missing receipt/checkpoint | Không COMPLETE; fail-closed hoặc approved PARTIAL recovery từ intermediate evidence, có provenance. | B/C |
| Bad/missing artifact | Existing corrupt/extra/duplicate file hoặc delivered-but-missing: không overwrite, không COMPLETE; raw và analysis không union. | C |
| Coverage | MAX_SCROLLS/COMMENT_LIMIT/no-growth/UI count/bottom: PARTIAL; replies requested: PARTIAL_VISIBLE. Unknown access không được CAPTCHA-free. | B/D |
| Cross-run identity | Same source-ID unchanged và changed, two jobs; không làm old receipt invalid qua silent current mutation. M3 initial supported mode phải hold trước commit; fallback cross-run không claim dedup. | B/C |
| Gates | CAPTCHA/login/document403/429 stop; optional403 không false-denial; unknown403 hold. No next source job/restart; Retry-After persisted; operator release không tự complete. | C/D |
| Session mismatch | Wrong video/window/profile/owner dừng; old owner không click/close browser mới; no PID-name-only kill. | C |
| Compatibility | Existing TikTok collector/persistence/compaction + M1/M2 + Google baseline; migrations reopen/drift rollback và Windows long paths. | D |

Tests dùng fake ports và isolated fixture processes; không TikTok requests. Process crash khác power loss. SQLite PRAGMAs và filesystem fsync phải đo trên connection/path test thực, không suy từ config. Browser/process liveness assertions phải có test riêng; mocked `verify_quiescent=lambda:True` không chứng minh production safety.

## 11. Audit evidence và reproduction

### Fixture probes thực hiện trong M3-A

Isolated artifacts: `output/m3a-473398/audit_results.json`, `source_hashes.json`; không dùng DB/live output của người dùng.

| Probe | Kết quả thực tế |
|---|---|
| Old Claim sau mocked takeover generation 2 | `JobStore.save_checkpoint(old)` bị reject; gọi writer không token vẫn commit 1 synthetic record. Mock attestation chỉ fault injection, không production death detector. |
| Existing global flush với pending event khác scope | Pending 1 → flush 1 → pending 0; API không có tham số job/token. |
| Source-ID UNCHANGED ở run B | INSERT run A=1; UNCHANGED run B=1; current.run_id=run-b nhưng event run-b=0. |
| PRAGMAs source connection | journal_mode=delete, synchronous=2/FULL, foreign_keys=0. |
| PRAGMAs JobStore connection | journal_mode=delete, synchronous=2/FULL, foreign_keys=1. |
| Source audit hashes | 8 file inspected giữ nguyên sau probes. |

Các probe xác nhận giới hạn integration, không thực thi recovery policy mới. Filesystem writer hiện rename không explicit raw-file fsync; compactor fsync là đường khác. Không có end-to-end power-loss guarantee hay exactly-once source guarantee.

Fresh regression M3-A: **141 passed, 2 warnings, 26.25s**. Hai warning là
numexpr/bottleneck version có sẵn; không đổi dependencies. Không thêm test hay
production code trong task audit; probes là isolated synthetic transactions.
Tám source hashes vẫn khớp sau cả probes và regression. Báo cáo có 0 broken
local links và 0 trailing whitespace khi kiểm tra.

### Reproduce regression hiện có

```powershell
Set-Location '<repository-root>'
$testRoot = 'output/t' + [guid]::NewGuid().ToString('N').Substring(0,5)
& 'python' -B -m pytest -q `
  --import-mode=importlib -p no:cacheprovider --basetemp $testRoot `
  tests/test_harness_state.py tests/test_harness_hardening.py `
  tests/test_harness_recovery.py `
  experiments/tiktok_comment_dom/test_persistence.py `
  experiments/tiktok_place_intelligence/test_live_dom_persistence.py `
  tests/test_google_baseline.py
```

Đọc [process recovery tests](../../tests/test_harness_recovery.py#L129-L188), [stale process test](../../tests/test_harness_hardening.py#L387-L406), [unrelated-pending rejection](../../tests/test_harness_recovery.py#L314-L322). Chúng không phải bằng chứng source/publisher fencing live. Kết quả fresh của audit ghi trong current task/work status; không tái dùng con số test lịch sử để chấp nhận M3.

## 12. Risks, blockers và quyết định cần review

1. Duyệt source-connection guard + atomic outcome/receipt hook trước sửa M1/M2 hoặc runner. Không sửa contract trong M3-A.
2. Duyệt scoped flush và trusted scope metadata/migration để đạt isolation cả pending bình thường lẫn malformed event. M2 toàn DB conservative behavior phải được giữ rõ.
3. Duyệt initial refresh limitation G7; nếu cần full refreshed stable-ID observations ngay, bổ sung versioned observation/reference design trước implementation, không patch event semantics tùy tiện.
4. Chốt OS lock/process ownership và operator quiescence evidence; không hỗ trợ multi-host DB/lake trong M3 v1.
5. Chốt durable source/session restriction hold và explicit operator release; hiện M1 không có terminal reopen API. Khi chưa chốt, chỉ hold, không resume restricted jobs.
6. Raw publication receipt là authoritative. Analysis compaction là tùy chọn sau writer stop, thất bại không thay crawler outcome hoặc cho phép acknowledge thêm event.
7. Existing live collector còn partial coverage, fallback identity và layout variability; integration không biến chúng thành full coverage. M3 chỉ bảo đảm lifecycle/persistence trong phạm vi đã kiểm thử.
8. Trước additive migration phải có backup consistent và rehearsal restore. Code v1 kiểm schema exact, nên không được rollback executable v1 trực tiếp lên DB có extension v2 chưa hỗ trợ; chọn binary đọc được schema mới hoặc restore bản backup đã đối soát vào đường riêng. Không drop bảng để ép validator pass.

**STOP sau M3-A.** Không triển khai M3-B/C/D, không đổi M1/M2 contracts, không live, không Gemini/Ollama/Telegram, không stage/commit/push/reset/clean. Review báo cáo và các contract gates trước bước tiếp theo.
