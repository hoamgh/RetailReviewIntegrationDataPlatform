# Overture ↔ Foody: Data Profiling và Data Source Evaluation

**Phạm vi:** F&B POI tại phường Khánh Hội; đánh giá offline ngày 2026-11-01. Đây là hai bước liên quan nhưng trả lời hai câu hỏi khác nhau. Không nguồn nào được mặc định là ground truth.

| | Data Profiling | Data Source Evaluation |
| --- | --- | --- |
| Câu hỏi | **Dữ liệu từng nguồn hiện có những gì, chất lượng ra sao?** | **Nguồn nào phù hợp với mục tiêu sản phẩm, và Foody bổ sung được gì cho Overture?** |
| Đơn vị phân tích | Một snapshot, cột, bản ghi, source ID | Hai nguồn được đưa về cùng phạm vi, các cặp ứng viên, chi phí và quyền sử dụng |
| Chỉ số | Schema, null rate, geometry, taxonomy, duplicate, thời gian ghi nhận | Coverage có kiểm chứng, overlap được xác nhận, POI mới còn hoạt động, enrichment, độ tin cậy, cost, license |
| Đầu ra | Báo cáo chất lượng và các vấn đề phải xử lý | Quyết định chọn nguồn kèm bằng chứng, giả định, bất định và việc cần xác minh |

## 1. Evidence và giới hạn của phép so sánh

Nguồn kết quả có thể tái lập: [`experiments/source_evaluation/results/20261101-khanh-hoi-v3/`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/). Đầu vào gồm Overture release `2026-10-21.0`, tập strict F&B Quận 4 cũ, và **1.000 URL Foody được chọn theo thứ tự discovery** đã lấy detail; cả hai được lọc lại theo cùng polygon Khánh Hội. Foody hiện là mẫu chưa chứng minh đầy đủ địa lý. Số 682 Overture Khánh Hội trong benchmark trước thuộc phạm vi/snapshot khác, không dùng làm mẫu số ở đây.

| Chỉ số thực đo trong polygon | Foody | Overture |
| --- | ---: | ---: |
| Source POI sau clip | 557 | 725 |
| Có tên | 557/557 | 725/725 |
| Có địa chỉ | 557/557 | 693/725 |
| Có category | 557/557 | 725/725 |
| Có giá | 504/557 | 0/725 |
| Có cuisine | 466/557 | 0/725 |
| Có tín hiệu review | 416/557 | 0/725 |
| Có số điện thoại | 0/557 | 611/725 |
| Có operating status | 0/557 | 0/725 |
| Trùng source ID trong tập đã clip | 0 | 0 |

Các tỷ lệ trên là **field availability**, không chứng minh giá trị chính xác hay quán còn mở. Timestamp Foody là thời điểm thu thập; metadata cập nhật Overture không xác minh được hoạt động kinh doanh. Xem [`05_detailed_data_profile.md`](../../experiments/source_evaluation/results/20261101-khanh-hoi-profile-detail-v1/05_detailed_data_profile.md) và [`quality_metrics.csv`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/quality_metrics.csv) để kiểm tra định nghĩa/denominator.

## 2. Từ profiling sang đánh giá nguồn

1. **Chuẩn hóa phạm vi:** giữ provenance, version, source ID và raw fields; dùng cùng polygon và định nghĩa F&B; ghi lý do loại bản ghi. Không lấy tổng Quận 4 của nguồn này chia cho tổng Khánh Hội của nguồn kia.
2. **ER trực tiếp Foody ↔ Overture:** chặn ứng viên bằng vị trí, tên, địa chỉ, danh mục và thông tin chi nhánh. Chấm điểm là **bằng chứng ứng viên**, không phải nhãn đúng/sai. Google search ID chỉ hỗ trợ kiểm tra, không tự động hợp nhất; đã quan sát collision 3 Foody × 16 Overture tạo 48 cặp.
3. **Xác minh độc lập:** người đánh giá đối chiếu đúng cửa hàng/chi nhánh, tình trạng hoạt động và nguồn bằng chứng có ngày. Giữ `REVIEW_REQUIRED` khi thiếu căn cứ; không suy ra Foody-only = POI mới hoặc Overture-only = Foody không có.
4. **Đo giá trị tăng thêm:** chỉ tính confirmed overlap, active Foody-only, enrichment hữu ích và precision/recall khi có nhãn phù hợp; kiểm tra quyền thu thập/lưu trữ/sử dụng thương mại và chi phí vận hành trước quyết định production.

Ở lần chạy này có **2.854 cặp ứng viên** từ nhiều đường blocking (639 `REVIEW_REQUIRED`, 2.215 `NON_MATCH` tạm thời), **169 dòng** trong [`manual_review.csv`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/manual_review.csv) **chưa được gán nhãn**. Vì vậy confirmed overlap, verified active uplift, độ chính xác matching và giá trị kinh tế vẫn **UNKNOWN / NOT YET VERIFIED**. Xem [`04_source_selection_decision.md`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/04_source_selection_decision.md) và [`run_manifest.json`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/run_manifest.json) để kiểm tra quyết định và bằng chứng chạy.

## 3. Quyết định hiện tại và thứ tự việc cần làm

**INCONCLUSIVE.** Overture hiện là baseline kỹ thuật có thể tái lập theo release; Foody có tín hiệu bổ sung về giá, cuisine và review nhưng chưa xác minh được số cửa hàng mới thực sự hữu ích. Đây là lựa chọn triển khai tạm thời, không phải kết luận Overture có coverage hay accuracy cao hơn. Không tự nhập Foody vào canonical catalog từ các cặp ứng viên.

Ưu tiên tiếp theo: (1) gán nhãn độc lập tập 169 dòng với branch identity, trạng thái và thời điểm bằng chứng; (2) xác minh quyền lưu trữ/sử dụng Foody và nghĩa vụ attribution/license từng record Overture; (3) sau khi có nhãn, tính uplift/enrichment và chi phí trên một mẫu địa lý đủ đại diện. Bản quyết định có thể kiểm toán ở [`04_source_selection_decision.md`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/04_source_selection_decision.md); manifest đầu vào/output ở [`run_manifest.json`](../../experiments/source_evaluation/results/20261101-khanh-hoi-v3/run_manifest.json).
