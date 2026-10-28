# 08 — Test Plan & Acceptance

**Version:** 1.0 · **Status:** Draft · **Related:** `03_SRS.md` (FR-01–24, NFR-01–10, AT-01–10), `04_USE_CASES.md`

## 1. Chiến lược

Bốn mức kiểm thử: **unit** (normalization, spatial/classification), **integration** (file read/write and data contracts), **end-to-end offline** (fixtures → published catalog) và **evaluation** (ER metrics on held-out labels). Live crawler/network gọi thật **không thuộc CI MVP**.

## 2. Bộ fixture bắt buộc

| Fixture | Mục đích | Yêu cầu liên quan |
|---|---|---|
| F01: cùng quán, tên viết khác nhau | correct match và source links | FR-05–07, 11; AT-03 |
| F02: 2 chi nhánh cùng brand | prevent false merge | FR-08; AT-04 |
| F03: hai quán sát nhau, tên gần giống | distinguish distinct entities | FR-06–08 |
| F04: missing name/phone/address | preserve nullable facts, quarantine đúng | FR-04–05 |
| F05: incorrect/missing coordinates | filter/quarantine và reason | FR-02–04 |
| F06: point inside/on/outside polygon | stable boundary policy | FR-02; AT-02 |
| F07: conflicting source attributes | selected field provenance | FR-11–12; AT-06 |
| F08: manual match/non-match/review | persist decision, precedence | FR-09; AT-05 |
| F09: rerun + shuffled input order | deterministic logical outputs | FR-10,16; AT-08 |
| F10: crash before/after publish | pointer safety, recovery | FR-17; AT-09 |
| F11: schema removed/renamed | fail with contract error | FR-01,04; NFR compatibility |
| F12: weak/ambiguous pair and labels | precision/recall/F1 + error analysis | FR-18; AT-10 |

## 3. Automated tests & gates

- Python `pytest`: normalization unicode/diacritics, coordinate math/haversine, category mapping, ordered pairs, cluster rules, config parsing, manifests, publication paths.
- Data contract checks: uniqueness, not-null, allowed enums, PK/FK-like relationships via anti-joins, value bounds, canonical count consistency, row-level provenance.
- SQL/dbt tests nếu dbt được dùng; không tự nhận `dbt test` PASS khi chưa chạy.
- E2E smoke test dùng **small deterministic dataset** và offline; một test trên actual Khánh Hội snapshots riêng để xác thực input integration.
- Regression khi mapping/schema/rules thay đổi: so sánh counts, match decisions, QA sample và baseline quality/performance.

## 4. Entity Resolution evaluation

1. Xây bộ cặp gán nhãn `same_branch`/`different_branch`, tách train/tuning và **held-out evaluation** theo clusters/locations nếu có thể, tránh leakage cùng chi nhánh.
2. Báo cáo confusion matrix TP/FP/FN/TN và Precision/Recall/F1 trên candidate-pair evaluation set.
3. Báo cáo riêng **candidate recall** (trên tập gold có cặp match có thể đánh giá) vì missing pair bị blocking có thể không xuất hiện trong classifier evaluation.
4. Ghi precision cho auto-match, review rate, false-merge/false-split sample. Không suy ra market coverage từ pair precision.
5. Thresholds và sample size phải chốt sau exploratory labeling, theo version; không tự đưa mục tiêu số ảo vào SRS.

## 5. Acceptance matrix MVP

| AT | Evidence tối thiểu |
|---|---|
| AT-01 | Manifests hai source + row counts |
| AT-02 | Fixture polygon/category + reject report |
| AT-03 | Single canonical store with two source links |
| AT-04 | Distinct store IDs for same-brand branches |
| AT-05 | Review queue + persisted manual decision replay |
| AT-06 | Field provenance sample + conflict record |
| AT-07 | 3 query examples + coverage metrics reconciliation |
| AT-08 | Two-run logical diff = no difference |
| AT-09 | Crash injection + old current remains valid |
| AT-10 | Labeled evaluation set + metric report + failure examples |

## 6. Test run artifacts

Mỗi test run lưu command, commit SHA, environment, input dataset manifest, rules, results, elapsed time và outputs dưới `output/test-runs/{run_id}` (đường dẫn có thể thay đổi theo config). Không commit raw source data/review content nếu không được phép.

## 7. CI policy

Trước merge: lint/format/type checks nếu cấu hình, Python unit/integration offline, contracts, fixture E2E. Network/live Google/TikTok/paid cloud phải được **tách khỏi CI**, không tự chạy. Build trên Windows và ít nhất một Linux-compatible môi trường chỉ khi đã có công cụ; không tự tuyên bố cross-platform trước test.

## 8. Definition of Done

Một feature chỉ được đánh dấu complete nếu có implementation, test, run evidence và docs update. Còn blocked phải mô tả blockers. Không sửa tests để che lỗi. Tài liệu khác thay đổi yêu cầu phải cập nhật traceability trong SRS.
