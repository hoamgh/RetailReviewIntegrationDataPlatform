# 13 — Monitoring & Observability

**Version:** 1.0 · **Status:** Proposed · **Applies to:** M1 onward; operational automation in later releases\
**Related:** `03_SRS.md` NFR-03/04/05/06, `07_PIPELINE_DESIGN.md`, `12_ORCHESTRATION_OPERATIONS.md`

## 1. Mục tiêu

Operator phải trả lời được: **run nào chạy, đầu vào nào, đang ở bước nào, kết quả có dùng được không, lỗi ở đâu và lần công bố gần nhất là phiên bản nào?** Monitoring không chỉ đo CPU; **data observability** theo dõi freshness, volume, schema, chất lượng và lineage. Tất cả thông số phải đi từ measurement thật, không đưa KPI/SLO tùy ý.

## 2. Những lớp quan sát

| Lớp | Theo dõi | Ý nghĩa |
|---|---|---|
| Pipeline run | `run_id`, step, status, start/end, elapsed_ms, attempts | Biết job nào lỗi và chỗ lỗi |
| Data volume | rows read/filtered/quarantined/normalized/published by source | Phát hiện rơi dữ liệu, sai category/polygon |
| Data quality | null/invalid/duplicate rate, RI failures, contract status | Chặn công bố dữ liệu không hợp lệ |
| Entity Resolution | candidate pairs, auto match, non-match, review queue, false merges (labeled only) | Theo dõi matching và rủi ro |
| Freshness | source snapshot date, ingested_at, published_at, current publication age | Phân biệt độ mới nguồn với độ mới hệ thống |
| Resource/performance | stage duration, throughput, peak RSS, CPU, bytes read/written | Benchmark & capacity planning |
| Publication | `publication_id`, current pointer, checksums, last successful version | Phát hiện partial/failed publish |

## 3. Logging contract

Log dạng JSON Lines, mọi event có `timestamp_utc`, `level`, `event`, `run_id`, `stage`, `source` (nếu có), `status`, `duration_ms` (khi có), `code_revision`, `config_version`. Không log credential, URL có token hoặc nội dung reviews chưa được phép. Dùng `correlation_id` nối các bước; `error_type` và `safe_message` để phân loại lỗi. Không logging 1 dòng cho mỗi POI theo mặc định, tránh log quá lớn.

```json
{"timestamp_utc":"2026-10-28T04:00:00Z","level":"INFO","event":"stage_completed","run_id":"example-run","stage":"normalize","source":"osm","status":"SUCCESS","rows_input":500,"rows_output":480,"rows_quarantined":20,"duration_ms":1234}
```

*Đây là ví dụ schema, không phải kết quả chạy thật.*

## 4. Metric definitions và reconciliation

- `records_seen_total{source,stage}`: số bản ghi duyệt ở stage; source snapshot ID dùng trong report/label có cardinality kiểm soát.
- `records_rejected_total{source,reason}`: bản ghi bị loại theo reason; cần tách `out_of_polygon`, `non_fnb`, `invalid`, `unknown_category`.
- `er_candidate_pairs_total`, `er_auto_matches_total`, `er_review_pairs_total`; không cộng các metrics khác grain.
- `publication_success_total`, `publication_failure_total`; `last_successful_publication_timestamp`.
- `stage_duration_seconds{stage}`, `peak_rss_bytes`, `processed_records_per_second` trên cùng denominator.
- `freshness_lag_hours = published_at - source_snapshot_timestamp` và `time_since_last_publish`: **khác nhau**, phải đặt tên khác.

**Lưu ý:** số lượng `source records`, `candidate pairs`, `canonical stores` khác grain; không gộp tùy tiện thành một "total". Báo cáo reconciliation phải chỉ rõ filtering order và counts.

## 5. Alert design (khi chạy theo lịch)

| Trigger | Severity đề xuất | Action |
|---|---|---|
| Quality gate FAIL hoặc source schema bất tương thích | Critical | Không publish, xuất report + manifest |
| Current pointer tới publication không hợp lệ | Critical | Dừng consumers có rủi ro; rollback theo runbook |
| Run thất bại nhiều lần liên tiếp | Warning/Critical theo lịch | Xem logs, phân loại retryable/permanent |
| Volume đột ngột đổi, tăng invalid rate | Warning | So sánh với source snapshot và category mapping |
| Data publication vượt freshness budget | Warning | Kiểm tra source lag vs pipeline lag |
| Tăng review queue hoặc false merge trên labeled checks | Warning | Giữ nguyên prior publication nếu gate fails |

Budget và ngưỡng alert là **TBD sau baseline**; không giả định 99.9% availability cho pipeline local chạy thủ công.

## 6. Triển khai theo giai đoạn

- **M1–M3:** structured logs + per-run JSON metrics + quality report + manifest; xem qua CLI/SQL.
- **M4–M5:** run history, change metrics, alert qua email/kênh có người nhận khi scheduled.
- **M7:** Prometheus/Grafana hoặc OpenTelemetry **chỉ nếu có nhu cầu**, không ép chạy server trong MVP.

## 7. Evidence và nghiệm thu

Mỗi run thực lưu file log, metric JSON, counts, danh sách gates, source/version info; có bài kiểm tra missing source → failed run; test bad schema → logged & no publication; test injected crash → prior publication remains active. Không tuyên bố dashboard "đã có" khi chưa chạy được.
