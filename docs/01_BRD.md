# F&B Data Integration & Recommendation Platform

**Business Requirements Document (BRD)**\
**Phiên bản:** 1.3 · **Ngày:** 28/10/2026 · **Trạng thái:** Draft — chờ duyệt\
**Phạm vi đầu tiên:** Phường Khánh Hội, TP.HCM

## 1. Tổng quan dự án

Thông tin về quán ăn, nhà hàng và quán cà phê đang nằm ở nhiều nguồn dữ liệu địa điểm. Cùng một chi nhánh có thể được ghi bằng tên, địa chỉ, tọa độ hoặc mã định danh khác nhau; ngược lại, hai chi nhánh cùng thương hiệu có thể bị nhầm là một. Điều này gây khó khăn khi xây danh mục cửa hàng, thống kê hoặc kết hợp thông tin đánh giá.

**F&B Data Integration & Recommendation Platform** hợp nhất dữ liệu địa điểm thành một **danh mục cửa hàng thống nhất**, có thể tìm kiếm, phân tích và truy về bản ghi gốc. Sau khi danh mục đủ tin cậy và có dữ liệu reviews được phép sử dụng, hệ thống sẽ phân tích nội dung đánh giá để hỗ trợ gợi ý địa điểm phù hợp hơn với nhu cầu.

**Ưu tiên của dự án:** xây dựng dữ liệu đầu ra chính xác, kiểm chứng được và có thể cập nhật; recommendation là sản phẩm khai thác dữ liệu ở giai đoạn sau.

## 2. Bài toán cần giải quyết

| Vấn đề | Ảnh hưởng hiện tại | Kết quả mong muốn |
|---|---|---|
| Một cửa hàng xuất hiện ở nhiều nguồn | Thống kê trùng, khó liên kết dữ liệu | Nhận diện cùng chi nhánh và quản lý dưới một định danh thống nhất |
| Thông tin thiếu hoặc không đồng nhất | Khó tìm kiếm, phân loại, xác minh | Chuẩn hóa thông tin và giữ nguồn gốc để đối chiếu |
| Không rõ dữ liệu có đầy đủ, đáng tin hay không | Dễ hiểu nhầm số POI thành số quán thực tế | Đo độ phủ, chất lượng và thể hiện phần chưa chắc chắn |
| Khó chọn quán theo nhu cầu cụ thể | Chỉ dựa vào vị trí/loại hình chưa thể hiện đầy đủ trải nghiệm | Bổ sung đặc điểm từ reviews hợp lệ và đánh giá hiệu quả gợi ý ở giai đoạn sau |
| Dữ liệu thay đổi theo thời gian | Danh mục nhanh lỗi thời, khó biết thay đổi ở đâu | Có khả năng cập nhật, đối chiếu các phiên bản dữ liệu |

## 3. Mục tiêu nghiệp vụ

- **BG-01 — Hợp nhất địa điểm:** Tạo một danh mục cửa hàng F&B không bị nhân đôi chỉ vì xuất hiện ở nhiều nguồn.
- **BG-02 — Đảm bảo tính tin cậy:** Mỗi cửa hàng có thông tin nguồn gốc, tình trạng dữ liệu và kết quả đối chiếu có thể kiểm tra.
- **BG-03 — Hỗ trợ khai thác dữ liệu:** Có thể tìm kiếm, lọc và phân tích địa điểm theo khu vực, loại hình, nguồn và mức độ đầy đủ thông tin.
- **BG-04 — Có thể cập nhật:** Dữ liệu mới hoặc thay đổi được xử lý mà không làm hỏng danh mục đã có.
- **BG-05 — Tạo giá trị từ reviews:** Khi có nguồn dữ liệu phù hợp, đánh giá liệu đặc điểm rút ra từ reviews có cải thiện gợi ý quán so với chỉ dùng thông tin cơ bản hay không.

## 4. Đối tượng sử dụng

| Đối tượng | Nhu cầu chính |
|---|---|
| Người tìm địa điểm ăn uống | Xem danh sách quán không trùng lặp và, ở giai đoạn sau, nhận gợi ý theo nhu cầu |
| Người phân tích dữ liệu | Tra cứu danh mục, thống kê địa điểm, so sánh độ phủ và chất lượng giữa các nguồn |
| Người vận hành dữ liệu | Kiểm tra dữ liệu đầu vào, các trường hợp đối chiếu chưa chắc chắn, kết quả cập nhật và lỗi |

## 5. Phạm vi dự án

### 5.1. Giai đoạn đầu — danh mục F&B tại Khánh Hội

- **Khu vực:** Phường Khánh Hội, TP.HCM; sử dụng ranh giới địa lý được xác minh và ghi rõ phiên bản.
- **Nguồn địa điểm:** Overture Maps và OpenStreetMap (OSM), mỗi nguồn có phiên bản và thời điểm dữ liệu riêng.
- **Đối tượng:** Các địa điểm thuộc nhóm F&B theo danh mục được thống nhất trước khi xử lý. Một cửa hàng được tính theo **chi nhánh vật lý**, không tính theo thương hiệu.
- **Xử lý nghiệp vụ:** Kiểm tra dữ liệu, chuẩn hóa, đối chiếu địa điểm, phân biệt trùng/khác/chưa xác định và tạo danh mục cửa hàng thống nhất.
- **Sử dụng kết quả:** Tra cứu, lọc và phân tích danh mục; xem nguồn của từng cửa hàng và báo cáo chất lượng dữ liệu. Chưa bắt buộc có giao diện web ở giai đoạn này.

### 5.2. Giai đoạn tiếp theo

- Tích hợp dữ liệu reviews từ nguồn **được phép thu thập, lưu trữ và sử dụng**; liên kết reviews với đúng chi nhánh.
- Phân tích những đặc điểm được phản ánh trong reviews như món ăn, dịch vụ, không gian, mức giá khi có đủ bằng chứng.
- Xây dựng và đánh giá chức năng gợi ý địa điểm; bổ sung cập nhật định kỳ và mở rộng sang khu vực khác khi kết quả pilot đáp ứng yêu cầu.

### 5.3. Ngoài phạm vi hiện tại

- Thu thập toàn bộ cửa hàng hoặc reviews của TP.HCM ngay từ đầu.
- Xây chức năng đặt bàn, đặt món, giao hàng hoặc thanh toán.
- Coi mọi POI thu thập được là cửa hàng đang hoạt động hoặc coi hai nguồn dữ liệu là danh sách đầy đủ toàn bộ quán trong khu vực.
- Phụ thuộc vào dữ liệu reviews chưa xác nhận quyền sử dụng để hoàn thành giai đoạn đầu.

## 6. Yêu cầu nghiệp vụ

| ID | Yêu cầu | Giai đoạn |
|---|---|---|
| BR-01 | Tiếp nhận dữ liệu F&B từ Overture Maps và OSM trong cùng phạm vi địa lý; lưu được nguồn và phiên bản dữ liệu | Đầu tiên |
| BR-02 | Nhận diện các bản ghi thuộc cùng **một chi nhánh** và tạo định danh cửa hàng thống nhất | Đầu tiên |
| BR-03 | Giữ liên kết từ cửa hàng thống nhất về các bản ghi và mã định danh của từng nguồn | Đầu tiên |
| BR-04 | Phân biệt bản ghi **trùng**, **khác nhau** và **cần xác minh**; không tự gộp khi chưa đủ bằng chứng | Đầu tiên |
| BR-05 | Cung cấp danh mục cửa hàng có thể tra cứu, lọc và phân tích theo khu vực, loại hình và nguồn dữ liệu | Đầu tiên |
| BR-06 | Cung cấp số liệu về số bản ghi từng nguồn, phần trùng, phần bổ sung, trường dữ liệu thiếu và các trường hợp chưa xác định | Đầu tiên |
| BR-07 | Khi xử lý lại cùng dữ liệu và cùng quy tắc, không phát sinh thêm cửa hàng trùng và giữ kết quả nhất quán | Đầu tiên |
| BR-08 | Hỗ trợ nhận diện bản ghi mới, thay đổi hoặc không còn xuất hiện trong phiên bản nguồn mới, nhưng không tự kết luận quán đã đóng cửa | Tiếp theo |
| BR-09 | Liên kết reviews hợp lệ với đúng cửa hàng và phân biệt dữ liệu gốc với thông tin suy ra từ reviews | Tiếp theo |
| BR-10 | Cho phép tìm kiếm/gợi ý quán theo nhu cầu và so sánh kết quả có dùng review features với phương án chỉ dùng thông tin cơ bản | Tiếp theo |
| BR-11 | Cho phép kiểm tra tình trạng cập nhật, chất lượng dữ liệu và các lỗi ảnh hưởng đến kết quả đã công bố | Tiếp theo |

## 7. Quy tắc nghiệp vụ

1. **Một cửa hàng là một chi nhánh thực tế.** Hai địa điểm cùng thương hiệu nhưng ở hai chi nhánh khác nhau không được gộp.
2. Trùng tên hoặc gần tọa độ **không đủ** để khẳng định hai bản ghi là cùng một cửa hàng.
3. Các bản ghi chưa chắc chắn phải được giữ để kiểm tra, không bị tự động xóa hoặc hợp nhất.
4. Mỗi cửa hàng trong danh mục phải có ít nhất một bản ghi nguồn truy vết được; thông tin suy ra phải phân biệt với thông tin nguồn.
5. Thiếu một địa điểm ở nguồn mới **không có nghĩa** địa điểm đã đóng cửa; trạng thái hoạt động cần bằng chứng riêng.
6. Số POI trong dữ liệu không được công bố như số cửa hàng thực tế hay mức độ bao phủ tuyệt đối nếu chưa có kiểm chứng độc lập.
7. Chỉ sử dụng dữ liệu trong giới hạn được phép của từng nguồn, đặc biệt với reviews và dữ liệu có điều kiện lưu trữ/tái phân phối.
8. Dữ liệu và các quyết định đối chiếu được công bố phải có khả năng giải thích, kiểm tra và tái tạo bằng đầu vào tương ứng.

## 8. Kết quả cần bàn giao

| Kết quả | Người sử dụng nhận được |
|---|---|
| **Danh mục cửa hàng thống nhất** | Mỗi chi nhánh có định danh ổn định, thông tin cơ bản và trạng thái dữ liệu |
| **Liên kết nguồn** | Biết cửa hàng đến từ Overture, OSM hay cả hai; xem được bản ghi nguồn liên quan |
| **Danh sách cần xác minh** | Biết những cặp địa điểm chưa đủ bằng chứng để tự quyết định |
| **Báo cáo chất lượng và độ phủ** | Số lượng nguồn, độ trùng, phần bổ sung, dữ liệu thiếu hoặc không hợp lệ |
| **Kết quả tra cứu/phân tích** | Có thể sử dụng danh mục để trả lời các câu hỏi cơ bản về địa điểm F&B trong phạm vi pilot |
| **Báo cáo đánh giá gợi ý** *(giai đoạn sau)* | So sánh kết quả có/không sử dụng thông tin rút ra từ reviews |

## 9. Tiêu chí thành công của giai đoạn đầu

Một pilot được xem là hoàn thành khi:

1. **Có dữ liệu thật từ cả Overture và OSM** trong ranh giới đã xác minh; ghi rõ thời điểm và phạm vi của từng nguồn.
2. **Có danh mục thống nhất:** mỗi cửa hàng đầu ra truy được về nguồn và giữ riêng các trường hợp chưa đủ bằng chứng để gộp.
3. **Có kiểm chứng chất lượng đối chiếu:** chuẩn bị tập cặp được gán nhãn, báo cáo Precision/Recall/F1 trên tập đánh giá và giải thích các lỗi gộp nhầm/tách nhầm. Không dùng các chỉ số này để khẳng định độ phủ toàn bộ địa điểm thực tế.
4. **Có báo cáo chất lượng và đóng góp của từng nguồn:** nêu rõ phần trùng, phần chỉ có ở một nguồn, dữ liệu thiếu và ngoại lệ.
5. **Có khả năng chạy lại:** cùng đầu vào và quy tắc cho kết quả logic nhất quán, không nhân đôi cửa hàng. Mọi thay đổi giữa các lần chạy phải có thể giải thích.
6. **Có thể khai thác đầu ra:** người sử dụng truy vấn hoặc lọc được danh mục và xem được báo cáo pilot.

Các mục tiêu định lượng cụ thể về độ chính xác, thời gian xử lý và độ mới sẽ được đặt sau khi có dữ liệu khảo sát và baseline, thay vì ghi các con số chưa được đo.

## 10. Giả định, phụ thuộc và rủi ro

| Vấn đề | Cách xử lý hoặc giới hạn |
|---|---|
| Overture/OSM thiếu hoặc sai địa điểm | Đánh giá dữ liệu thực tế, giữ trạng thái chưa chắc chắn; không hứa bao phủ toàn bộ thị trường |
| Gộp nhầm hai chi nhánh | Ưu tiên quyết định thận trọng, có tập kiểm chứng và danh sách cần xem xét |
| Khác biệt về phân loại F&B và ranh giới địa lý | Xác nhận quy tắc phân loại và ranh giới trước khi so sánh hai nguồn |
| Dữ liệu reviews có giới hạn quyền sử dụng | Chỉ tích hợp sau khi xác minh điều kiện của nguồn; nếu không, tiếp tục với danh mục POI |
| Crawler đã có nhưng chưa xác minh khả năng sử dụng lại | Kiểm tra code, dữ liệu và giới hạn truy cập riêng; không coi việc có code là đã sẵn sàng chạy live |
| Mở rộng phạm vi khiến chi phí tăng | Hoàn thành pilot, đo kết quả và nhu cầu thực tế trước khi mở rộng |

---

**Ranh giới tài liệu:** BRD xác định bài toán, mục tiêu, yêu cầu nghiệp vụ và điều kiện thành công. Chi tiết chức năng nằm ở PRD; yêu cầu kiểm thử, hiệu năng và độ tin cậy nằm ở SRS; các luồng tương tác nằm ở Use Case Specification; thiết kế dữ liệu và công nghệ nằm ở System Design.
