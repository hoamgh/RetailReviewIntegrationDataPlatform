# F&B Data Integration & Recommendation Platform

**Product Requirements Document (PRD)**\
**Phiên bản:** 1.0 · **Ngày:** 28/10/2026 · **Trạng thái:** Draft — chờ duyệt\
**Liên quan:** `01_BRD.md` · **Phạm vi đầu tiên:** Phường Khánh Hội, TP.HCM

## 1. Sản phẩm cần xây dựng

Hệ thống cung cấp một **danh mục địa điểm F&B thống nhất** từ nhiều nguồn dữ liệu. Mỗi chi nhánh thực tế được đại diện bằng một `store_id`, có thể xem thông tin, biết dữ liệu đến từ đâu và xác định trường hợp nào còn chưa chắc chắn.

Sản phẩm đầu tiên là **bộ dữ liệu đáng tin cậy và có thể khai thác**, chưa bắt buộc là website. Sau khi hoàn thành danh mục, hệ thống mới tích hợp reviews được phép sử dụng để phân tích trải nghiệm và thử nghiệm gợi ý địa điểm.

**Giá trị cốt lõi:** người dùng không phải tự xử lý các bản ghi địa điểm trùng lặp, thiếu thông tin hoặc mâu thuẫn giữa các nguồn trước khi khai thác.

## 2. Người dùng và nhu cầu

| Người dùng | Việc cần làm | Kết quả mong đợi |
|---|---|---|
| **Data Analyst** | Tra cứu, lọc và thống kê các địa điểm F&B | Có dữ liệu rõ nguồn gốc, không đếm trùng do khác nguồn |
| **Data Operator** | Kiểm tra nguồn nhập, cặp nghi trùng, dữ liệu lỗi và kết quả cập nhật | Biết cần xử lý gì, quyết định nào đã được áp dụng |
| **Người tìm quán** *(giai đoạn sau)* | Tìm quán theo vị trí, loại hình và nhu cầu | Nhận danh sách/gợi ý có giải thích, không hiển thị trùng chi nhánh |

## 3. Phạm vi phát hành

| Phiên bản | Bao gồm | Chưa yêu cầu |
|---|---|---|
| **MVP — Unified F&B Catalog** | POI Overture + OSM tại Khánh Hội, lọc F&B, đối chiếu chi nhánh, danh mục thống nhất, truy vết nguồn, danh sách cần xác minh và báo cáo chất lượng | Website, reviews, recommendation, chạy crawler live |
| **Release 2 — Catalog Updates & Review Integration** | So sánh phiên bản nguồn, cập nhật danh mục, tích hợp reviews đã được phép sử dụng, liên kết đến chi nhánh và phân tích đặc điểm | Phải có đủ reviews cho toàn TP.HCM |
| **Release 3 — Recommendation & Expansion** | Tìm kiếm/gợi ý theo nhu cầu, giải thích kết quả, đánh giá so với baseline, mở rộng khu vực có kiểm soát | Đặt bàn, đặt món, giao hàng, thanh toán |

Phạm vi dữ liệu MVP tính theo **ranh giới hành chính Khánh Hội đã được xác minh**, không theo một hình chữ nhật ước lượng. Mỗi nguồn phải được ghi rõ phiên bản và thời điểm lấy dữ liệu. **Foursquare không nằm trong kế hoạch hiện tại.**

## 4. Quy trình sản phẩm

1. **Chọn dữ liệu nguồn:** người vận hành chỉ định snapshot Overture và OSM cùng phạm vi khảo sát.
2. **Kiểm tra phạm vi:** hệ thống xác định địa điểm nào thuộc nhóm F&B và nằm trong khu vực được chọn; báo cáo những bản ghi bị loại hoặc thiếu thông tin.
3. **Đối chiếu chi nhánh:** hệ thống tìm các địa điểm có thể giống nhau; phân loại **trùng / khác / cần xác minh**.
4. **Xác minh ngoại lệ:** người vận hành xem bằng chứng và ghi quyết định đối với cặp chưa chắc chắn.
5. **Công bố danh mục:** hệ thống tạo danh mục thống nhất cùng thông tin liên kết nguồn và báo cáo chất lượng.
6. **Khai thác:** người phân tích có thể tra cứu, lọc và thống kê dữ liệu đã công bố.

Trong các release sau, reviews hợp lệ được liên kết với danh mục; những đặc điểm rút ra từ reviews được dùng cho phân tích và gợi ý.

## 5. Chức năng MVP

| ID | Chức năng | Hành vi mong đợi | Ưu tiên |
|---|---|---|---|
| **PF-01** | Tiếp nhận nguồn POI | Nhận Overture và OSM, ghi tên nguồn, phiên bản, thời điểm và trạng thái xử lý | P0 |
| **PF-02** | Lọc phạm vi F&B | Lấy địa điểm thuộc ranh giới và loại hình đã thống nhất; báo cáo số bản ghi giữ/loại theo lý do | P0 |
| **PF-03** | Chuẩn hóa thông tin địa điểm | Cung cấp tên, vị trí, địa chỉ, loại hình và các trường hỗ trợ đối chiếu dưới cách biểu diễn thống nhất; không làm mất giá trị gốc | P0 |
| **PF-04** | Đối chiếu địa điểm | Tạo kết quả trùng, khác hoặc cần xác minh; phân biệt các chi nhánh cùng thương hiệu | P0 |
| **PF-05** | Xem xét trường hợp chưa chắc chắn | Có danh sách cặp cần kiểm tra, bằng chứng đối chiếu và chỗ ghi quyết định thủ công | P0 |
| **PF-06** | Danh mục cửa hàng thống nhất | Mỗi chi nhánh có `store_id` ổn định và thông tin dùng được để phân tích | P0 |
| **PF-07** | Truy vết nguồn và mâu thuẫn | Từ một cửa hàng xem được mọi bản ghi nguồn; không che giấu thông tin khác nhau giữa nguồn | P0 |
| **PF-08** | Tra cứu và lọc | Tìm/lọc cửa hàng theo tên, loại hình, vị trí/khu vực và nguồn; MVP chấp nhận SQL hoặc bảng dữ liệu có tài liệu hướng dẫn | P0 |
| **PF-09** | Báo cáo dữ liệu | Có thống kê số bản ghi theo nguồn, số cửa hàng thống nhất, phần trùng/chỉ có một nguồn, dữ liệu thiếu, lỗi và cặp chưa xác minh | P0 |
| **PF-10** | Công bố và chạy lại | Mỗi phiên bản đầu ra có thông tin xác định đầu vào; xử lý lại cùng dữ liệu không làm tăng cửa hàng giả hoặc đổi định danh vô cớ | P0 |

**Nguyên tắc:** kết quả `cần xác minh` là một trạng thái hợp lệ, không phải lỗi phải tự động gộp cho hết. Nếu không có đủ bằng chứng, giữ nguyên hai bản ghi và đưa vào danh sách xem xét.

## 6. Chức năng các release sau

| ID | Chức năng | Hành vi mong đợi | Release |
|---|---|---|---|
| **PF-11** | So sánh snapshot | Nhận diện địa điểm mới, thay đổi hoặc không còn thấy ở snapshot mới; không tự kết luận quán đóng cửa | 2 |
| **PF-12** | Tích hợp reviews hợp lệ | Chỉ tiếp nhận dữ liệu đã xác nhận quyền sử dụng; liên kết review với đúng chi nhánh, giữ thông tin nguồn | 2 |
| **PF-13** | Phân tích nội dung reviews | Tổng hợp các đặc điểm như đồ ăn, dịch vụ, không gian, giá; hiển thị mức bằng chứng, thiếu dữ liệu hoặc không chắc chắn | 2 |
| **PF-14** | Tìm kiếm/gợi ý theo nhu cầu | Trả địa điểm phù hợp theo tiêu chí của người dùng, không trùng chi nhánh, có lý do xếp hạng | 3 |
| **PF-15** | Đánh giá chất lượng gợi ý | So sánh cách xếp hạng chỉ dựa trên thông tin cơ bản với cách bổ sung review features trên cùng tập đánh giá | 3 |
| **PF-16** | Theo dõi chất lượng cập nhật | Cho người vận hành biết bản dữ liệu nào hiện hành, lần cập nhật gần nhất, bất thường và lỗi ảnh hưởng đầu ra | 2–3 |

Các chức năng chưa đến release tương ứng **không phải điều kiện nghiệm thu MVP**.

## 7. Yêu cầu đối với dữ liệu đầu ra

| Data product | Người dùng sử dụng để làm gì? | Nội dung tối thiểu |
|---|---|---|
| **Canonical Store Catalog** | Tra cứu và thống kê chi nhánh | `store_id`, tên hiển thị, vị trí, loại hình, tình trạng xác minh và phiên bản công bố |
| **Source Links** | Đối chiếu và truy nguồn | `store_id`, tên nguồn, source record ID, bản ghi gốc hoặc vị trí tham chiếu |
| **Match Review Queue** | Xem các trường hợp nghi trùng | Hai record cần đối chiếu, bằng chứng, trạng thái, quyết định và lý do (nếu đã xử lý) |
| **Coverage & Quality Report** | Hiểu chất lượng dữ liệu | Số lượng theo nguồn, overlap, trường thiếu, bản ghi lỗi, chưa xác minh và phạm vi áp dụng |
| **Catalog Query Examples** | Sử dụng đầu ra ngay cả khi chưa có UI | Những truy vấn hoặc ví dụ đọc dữ liệu phổ biến, kèm ý nghĩa từng trường |

Tên bảng/schema vật lý, PK/FK và cách lưu file **không chốt trong PRD**; chúng thuộc Data Model và System Design.

## 8. Trải nghiệm sử dụng và nguyên tắc hiển thị

- **Không đếm trùng:** nếu Overture và OSM cùng chỉ một chi nhánh đã xác minh, danh mục hiển thị một cửa hàng; phần source links vẫn giữ cả hai nguồn.
- **Không gộp nhầm chi nhánh:** tên thương hiệu giống nhau không có nghĩa là cùng địa điểm.
- **Rõ mức tin cậy:** thiếu dữ liệu, không xác định và không áp dụng phải khác nhau; không tự suy ra trạng thái đang mở cửa.
- **Có thể giải thích:** người vận hành tìm được nguồn gốc và lý do của quyết định ghép/tách.
- **Dùng được không cần website:** bản MVP có thể bàn giao dataset, báo cáo, truy vấn mẫu và danh sách ngoại lệ. UI sẽ được quyết định riêng nếu thực sự cần.
- **Review-based insights:** ở release sau, tính năng từ reviews phải nêu được bằng chứng và không biến việc không có reviews thành đánh giá tiêu cực.

## 9. Tiêu chí nghiệm thu sản phẩm MVP

| ID | Tình huống kiểm tra | Kết quả cần đạt |
|---|---|---|
| **AC-01** | Nạp snapshot từ cả hai nguồn trong Khánh Hội | Có dữ liệu đầu vào, phiên bản và số lượng theo từng nguồn |
| **AC-02** | Một chi nhánh có record ở cả Overture và OSM | Khi được xác nhận trùng, danh mục có một `store_id` và hai source links |
| **AC-03** | Hai chi nhánh cùng thương hiệu | Không bị gộp thành một cửa hàng chỉ vì tên giống |
| **AC-04** | Hai record chưa đủ bằng chứng | Vẫn được giữ, xuất hiện trong danh sách cần xác minh |
| **AC-05** | Analyst tìm/lọc và xem nguồn của một cửa hàng | Trả được kết quả từ danh mục đã công bố và truy được source IDs |
| **AC-06** | Đọc báo cáo sau một lần xử lý | Có số lượng, phần trùng, phần bổ sung, thiếu dữ liệu và ngoại lệ; nêu rõ phạm vi thống kê |
| **AC-07** | Chạy lại cùng snapshot và quy tắc | Danh mục logic và IDs nhất quán, không sinh thêm bản ghi trùng do chạy lại |
| **AC-08** | Kiểm chứng trên tập đối chiếu có nhãn | Có báo cáo lỗi ghép nhầm/tách nhầm và các chỉ số matching phù hợp, không tự nhận đã bao phủ toàn bộ quán thực tế |

Các ngưỡng định lượng (nếu cần) sẽ chốt sau khi khảo sát dữ liệu và đo baseline. Các trường hợp chi tiết, ngoại lệ và test tương ứng sẽ được viết trong **Use Case Specification** và **SRS/Test Plan**.

## 10. Phụ thuộc và giới hạn

- **Nguồn dữ liệu:** Overture và OSM phải truy cập được, có bản quyền/điều kiện sử dụng đã được xem xét và ghi nhận.
- **Ranh giới & loại hình:** cần thống nhất polygon Khánh Hội và bộ quy tắc xác định F&B trước khi công bố số liệu so sánh.
- **Chất lượng nguồn:** thiếu trường, tọa độ sai, địa điểm hết hoạt động hoặc record trùng là tình huống dự kiến, không phải ngoại lệ hiếm gặp.
- **Crawler hiện có:** code đã được chuyển vào repository nhưng phải kiểm tra thực trạng, điều kiện vận hành và quyền sử dụng dữ liệu trước khi kết nối; MVP không phụ thuộc crawler live.
- **Quyền reviews:** có crawler không đồng nghĩa với quyền lưu trữ/tái sử dụng nội dung. Nếu chưa có nguồn hợp lệ, Release 2 phần review chưa triển khai live.
- **Không cam kết độ phủ tuyệt đối:** dữ liệu công bố chỉ phản ánh các nguồn và phiên bản được xử lý, không khẳng định là danh sách đầy đủ quán tại Khánh Hội.

---

**Ranh giới tài liệu:** PRD xác định *sản phẩm cung cấp chức năng gì và người dùng nhận được gì*. SRS định nghĩa yêu cầu kiểm chứng và phi chức năng; Use Case mô tả các luồng tương tác; Data Model/Architecture/Pipeline Design mô tả cách triển khai. Tài liệu này **chưa chốt công nghệ, database, API hay giao diện**.
