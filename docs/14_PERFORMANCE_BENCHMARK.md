# 14 — Performance, Benchmark & Capacity

**Version:** 1.0 · **Status:** Proposed · **Related:** `03_SRS.md` NFR-06/07, `08_TEST_PLAN.md`

## 1. Câu hỏi cần trả lời

Trên laptop hiện tại, pipeline xử lý Khánh Hội **mất bao lâu, tốn RAM/disk bao nhiêu, bước nào chậm và thay đổi tối ưu thực sự có ích không?** Không giới thiệu Spark/MinIO/Trino hoặc cluster khi chưa có bottleneck đo được. Tách **offline batch POI** khỏi **online crawler reviews** — throughput/giới hạn hoàn toàn khác.

## 2. Benchmark matrix

| Workload | Quy mô / lần chạy | Cần đo |
|---|---|---|
| Overture extraction | snapshot thật, file sizes ghi manifest | scan bytes, elapsed, rows/sec, peak RSS |
| OSM extraction/normalization | cùng polygon/category policy | elapsed, accepted/rejected, RAM |
| Spatial blocking | pilot, nhân bản dataset có gắn nhãn synthetic riêng | input rows, candidates, reduction ratio, candidate recall |
| Pair scoring/clustering | fixed candidate set | pairs/sec, elapsed, peak RAM, false merge tests |
| Gold query | truy vấn điển hình trên published catalog | cold/warm latency, rows scanned, explain plan |
| Publication | N files, fixed output size, fault injection | duration, bytes, crash recovery correctness |
| Review crawler (future) | approved source + bounded workloads | valid reviews/min, p50/p95 store time, failure/retry, RSS, network |

## 3. Protocol đo chuẩn

1. Ghi **hardware** (CPU cores, RAM, SSD), OS, Python/DuckDB versions, commit SHA, environment vars ảnh hưởng performance; cùng input snapshot và `rules_version`.
2. Lấy **baseline trước**; nếu được chạy ≥3 lần và ghi median, min/max hoặc p50/p95 phù hợp loại phép đo; không báo p95 từ vài sample mà không chú thích hạn chế.
3. Phân biệt **cold vs warm** cache; đo wall-clock, CPU, peak resident RAM, disk I/O và output correctness, không chỉ time.
4. Mỗi tối ưu chỉ thay đổi một nhóm yếu tố; chạy regression quality/ER tests trước khi kết luận nhanh hơn.
5. Lưu raw measurement CSV/JSON và report cùng thư mục `output/benchmarks/{benchmark_id}`; **không giả vờ tốc độ sample là tốc độ toàn thành phố**.

## 4. Các tối ưu đáng thử theo thứ tự

| Bottleneck đã đo | Hướng thử | Risk cần kiểm soát |
|---|---|---|
| Đọc nhiều dữ liệu không liên quan | projection/filter pushdown, chọn đúng source partitions | Không bỏ sót polygon-boundary candidates |
| Matching so sánh toàn cặp `O(N²)` | spatial grid, radius-based neighbors, category blocking | Candidate recall không giảm đáng kể |
| Repeated text normalization | normalize once/cache Parquet Silver | Rule change phải invalidate cache |
| Large intermediate frames | streaming/batch sizing; spill/storage capacity | Deterministic results and no truncation |
| Tiny Parquet files | compaction theo batch và query pattern | Atomic publication + không overwrite active |
| Query Gold chậm | `EXPLAIN ANALYZE`, ordering, predicate/projection pruning | Same output and source provenance |
| Crawler chậm | profile network/UI waits, bounded concurrency | Không vượt access/rate restrictions |

## 5. Capacity estimation

`disk_required ≈ raw_snapshot_bytes + silver_bytes + gold_publications_bytes + staging_peak + backups`; đo hệ số thực bằng `du`/file stats. Đưa ước lượng cho 1 ward → nhiều wards từ **bytes/POI thật**, nhưng ghi rõ skew và khác biệt schema. Bổ sung disk free guard trước xử lý lớn. Không khẳng định "77k stores = big data"; scale được định nghĩa bằng workload và resource constraints.

## 6. Decision gate for technology change

Đổi sang distributed engine/object storage khi có: (a) yêu cầu thực tế (nhiều worker/máy/compute tách storage), (b) benchmark local không đáp ứng, (c) bảng so sánh chi phí vận hành, (d) plan rollback. Không dùng MinIO chỉ để nhìn giống S3; trong local một máy, dùng Parquet filesystem là hợp lý.

## 7. Acceptance / Report template

`benchmark_id`, purpose, environment, input count/bytes, method, baseline, candidate optimization, quality checks, timing/resource results, limitations, recommended decision. Chỉ gắn **VERIFIED** khi có lệnh chạy và file kết quả có thể tái lập.
