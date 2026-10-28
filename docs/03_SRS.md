# F&B Data Integration & Recommendation Platform

**Software Requirements Specification (SRS)**\
**Phiên bản:** 1.0 · **Ngày:** 28/10/2026 · **Trạng thái:** Draft — chờ duyệt\
**Liên quan:** `01_BRD.md`, `02_PRD.md` · **Phạm vi MVP:** Phường Khánh Hội, TP.HCM

## 1. Mục đích và phạm vi

Tài liệu này quy định **hệ thống phải làm gì và kiểm tra bằng cách nào**. BRD mô tả bài toán nghiệp vụ; PRD mô tả chức năng sản phẩm; SRS chuyển các chức năng đó thành yêu cầu có thể kiểm thử. Luồng thao tác chi tiết thuộc Use Case; bảng vật lý, thuật toán, API và công nghệ thuộc tài liệu thiết kế.

**MVP — Unified F&B Catalog:** tích hợp POI từ **Overture Maps + OpenStreetMap**, lọc theo ranh giới Khánh Hội đã xác minh, chuẩn hóa, đối chiếu chi nhánh, xuất danh mục thống nhất, liên kết nguồn, hàng chờ xác minh và báo cáo chất lượng. Chưa yêu cầu website, reviews, recommendation hoặc crawler live.

**Release 2:** cập nhật snapshot, reviews hợp lệ và phân tích nội dung. **Release 3:** tìm kiếm/gợi ý theo nhu cầu, đánh giá ranking và mở rộng địa bàn. Các yêu cầu release sau không chặn nghiệm thu MVP.

## 2. Thuật ngữ và quy ước

| Thuật ngữ | Ý nghĩa trong dự án |
|---|---|
| **POI / source record** | Một bản ghi địa điểm do nguồn cung cấp; không mặc định là quán còn hoạt động. |
| **Store / chi nhánh** | Một địa điểm kinh doanh vật lý, không phải tên thương hiệu. |
| **Canonical store** | Bản đại diện thống nhất cho một chi nhánh; có `store_id` riêng. |
| **Source link** | Quan hệ truy vết từ `store_id` đến bản ghi và phiên bản nguồn. |
| **Match / Non-match / Review** | Cùng chi nhánh / khác chi nhánh / chưa đủ bằng chứng. |
| **Snapshot / publication** | Bộ dữ liệu đầu vào ở một phiên bản / bộ đầu ra đã công bố thành công. |
| **MUST / SHOULD** | Bắt buộc để nghiệm thu / mong muốn nếu có cơ sở, chưa chặn MVP. |

**Quy ước ưu tiên:** P0 = bắt buộc MVP; P1 = Release 2; P2 = Release 3. Mã `FR-*` là yêu cầu chức năng; `NFR-*` là yêu cầu phi chức năng; `AT-*` là bài kiểm tra nghiệm thu.

## 3. Đầu vào, đầu ra và giới hạn hệ thống

**Đầu vào MVP:** snapshot Overture, snapshot OSM, phiên bản polygon Khánh Hội, bộ ánh xạ loại hình F&B, cấu hình quy tắc đối chiếu và quyết định xác minh thủ công (nếu có). Mỗi đầu vào phải có nguồn, phiên bản/thời điểm và thông tin xác định tập dữ liệu được xử lý.

**Đầu ra MVP:** `Canonical Store Catalog`, `Source Links`, `Match Review Queue`, `Coverage & Quality Report`, `Run/Publication Manifest` và hướng dẫn truy vấn. Tên bảng/file vật lý sẽ xác định ở Data Model; đây là **logical data products**, không phải schema đã khóa.

Hệ thống chỉ làm việc trong phạm vi dữ liệu và quyền sử dụng hợp lệ. Code crawler đã chuyển vào repository **chưa đồng nghĩa** với một nguồn live được phép khai thác. Không sử dụng Foursquare ở MVP.

## 4. Yêu cầu chức năng — MVP (P0)

### 4.1. Tiếp nhận và kiểm tra dữ liệu

| ID | Yêu cầu có thể kiểm thử | Liên kết PRD |
|---|---|---|
| **FR-01** | Hệ thống MUST nạp được ít nhất một snapshot Overture và một snapshot OSM; ghi tên nguồn, release/version, thời điểm lấy, run ID và số bản ghi đọc được. | PF-01 |
| **FR-02** | Hệ thống MUST dùng **polygon Khánh Hội có provenance và phiên bản** để xác định địa điểm trong phạm vi; quy tắc xử lý điểm nằm trên biên phải nhất quán và được tài liệu hóa. | PF-02 |
| **FR-03** | Hệ thống MUST áp dụng bộ ánh xạ loại hình F&B riêng cho từng nguồn; lưu mã loại hình gốc và kết quả phân loại chung, kể cả trường hợp không xác định. | PF-02, PF-03 |
| **FR-04** | Hệ thống MUST kiểm tra record thiếu định danh, thiếu/sai tọa độ, giá trị không hợp lệ và trường bắt buộc của hợp đồng dữ liệu; ghi số lượng và lý do loại/cách ly thay vì âm thầm bỏ qua. | PF-02, PF-09 |
| **FR-05** | Hệ thống MUST chuẩn hóa tên, địa chỉ, tọa độ và thuộc tính dùng để matching mà vẫn bảo toàn giá trị gốc hoặc tham chiếu tới chúng. | PF-03 |

### 4.2. Entity Resolution và xác minh

| ID | Yêu cầu có thể kiểm thử | Liên kết PRD |
|---|---|---|
| **FR-06** | Hệ thống MUST tạo danh sách cặp ứng viên đối chiếu và lưu bằng chứng như khoảng cách, tên/địa chỉ chuẩn hóa, thông tin liên hệ khi có; không được mặc định mọi POI gần nhau là trùng. | PF-04 |
| **FR-07** | Mỗi cặp được xét MUST có quyết định **match / non-match / review**, kèm phiên bản quy tắc và lý do hoặc bằng chứng; trường hợp không đủ tin cậy không tự động hợp nhất. | PF-04 |
| **FR-08** | Hệ thống MUST không gộp hai chi nhánh khác nhau chỉ vì cùng thương hiệu/tên; các trường hợp cùng tên nhưng tọa độ/địa chỉ khác phải có kiểm tra hoặc bằng chứng bổ sung. | PF-04 |
| **FR-09** | Hệ thống MUST xuất được danh sách `review` và cho phép ghi nhận quyết định thủ công cùng người/nguồn quyết định, thời điểm, lý do; quyết định này có thể được sử dụng lại khi xử lý cùng cặp. MVP có thể sử dụng file/CLI, chưa bắt buộc UI. | PF-05 |

### 4.3. Danh mục, truy vết và khai thác

| ID | Yêu cầu có thể kiểm thử | Liên kết PRD |
|---|---|---|
| **FR-10** | Mỗi canonical store được công bố MUST có `store_id` duy nhất và ổn định khi chạy lại **cùng snapshot, cấu hình và quyết định xác minh**; việc thay đổi ID ở lần cập nhật khác phải có thể giải thích. | PF-06, PF-10 |
| **FR-11** | Mỗi cửa hàng MUST liên kết tới ít nhất một source record; source link MUST ghi nguồn, source ID, phiên bản và đủ thông tin để truy về bản ghi gốc. | PF-07 |
| **FR-12** | Khi nguồn mâu thuẫn về tên, địa chỉ hoặc loại hình, hệ thống MUST lưu/tra được nguồn của giá trị được chọn và không âm thầm ghi đè bằng một giá trị thiếu căn cứ. | PF-07 |
| **FR-13** | Analyst MUST tra cứu/lọc được catalog đã công bố theo tên, loại hình, phạm vi địa lý và nguồn. MVP chấp nhận câu SQL hoặc công cụ đọc dữ liệu được hướng dẫn rõ. | PF-08 |
| **FR-14** | Hệ thống MUST tạo báo cáo: records đọc/giữ/loại theo nguồn, canonical stores, stores có nhiều nguồn, stores chỉ một nguồn, thông tin thiếu, record lỗi và cặp cần xác minh. Mẫu số và phạm vi của từng chỉ số phải rõ ràng. | PF-09 |

### 4.4. Công bố, chạy lại và đánh giá

| ID | Yêu cầu có thể kiểm thử | Liên kết PRD |
|---|---|---|
| **FR-15** | Mỗi lần chạy MUST có manifest ghi input versions, polygon version, classification/matching rule versions, thời gian, kết quả kiểm tra và output version. | PF-01, PF-10 |
| **FR-16** | Chạy lại cùng input + cấu hình + quyết định thủ công MUST cho kết quả logic tương đương (cùng tập store/source links), không tạo thêm ID giả hoặc nhân đôi bản ghi; khác biệt ở metadata như `run_id` là chấp nhận được. | PF-10 |
| **FR-17** | Nếu một lần xử lý thất bại trước khi công bố, hệ thống MUST không để người dùng đọc nhầm dữ liệu dở dang như bản hoàn chỉnh; kết quả của lần công bố thành công trước vẫn truy cập được. | PF-10 |
| **FR-18** | Hệ thống MUST hỗ trợ đánh giá matching bằng bộ cặp đã gán nhãn (cả match và non-match), báo cáo Precision, Recall, F1, số lỗi gộp nhầm và bỏ sót; phân biệt đánh giá *cặp* với đánh giá độ phủ toàn bộ thị trường. | PF-04, PF-09 |

## 5. Yêu cầu chức năng — các release sau

| ID | Release | Yêu cầu có thể kiểm thử | Liên kết PRD |
|---|---|---|---|
| **FR-19** | 2 | So sánh các snapshot và phân loại **new / changed / missing from source**; thiếu ở snapshot mới không được tự suy ra quán đóng cửa. | PF-11 |
| **FR-20** | 2 | Chỉ tiếp nhận reviews đã xác nhận quyền dùng; ghi source ID, thời điểm và quan hệ đến chi nhánh; review chưa ghép được phải có trạng thái riêng. | PF-12 |
| **FR-21** | 2 | Tạo đặc điểm từ nội dung reviews với số lượng bằng chứng và trạng thái thiếu/không chắc chắn; không tự diễn giải thiếu review thành chất lượng kém. | PF-13 |
| **FR-22** | 3 | Trả danh sách quán theo tiêu chí/vị trí, không hiển thị trùng chi nhánh và có lý do xếp hạng. | PF-14 |
| **FR-23** | 3 | Đánh giá baseline chỉ dùng dữ liệu POI so với ranking có review features trên cùng tập kiểm tra và báo cáo phương pháp, chỉ số, giới hạn. | PF-15 |
| **FR-24** | 2–3 | Cung cấp trạng thái cập nhật, lần công bố mới nhất, lỗi và cảnh báo chất lượng ảnh hưởng dữ liệu sử dụng. | PF-16 |

## 6. Yêu cầu phi chức năng (NFR)

| ID | Thuộc tính | Yêu cầu và cách xác minh |
|---|---|---|
| **NFR-01** | Data correctness | Dữ liệu đã công bố phải thỏa các quy tắc toàn vẹn: `store_id` duy nhất, source link không mồ côi, mỗi store có nguồn, trạng thái match hợp lệ. Kiểm bằng automated tests. |
| **NFR-02** | Reproducibility | Input và rule versions phải đủ để chạy lại; lưu manifest và so sánh tập dữ liệu đầu ra qua hai lần chạy. |
| **NFR-03** | Reliability | Pipeline lỗi phải báo thất bại rõ, không công bố bản dở dang; phép thử cố ý gây lỗi kiểm tra khả năng đọc lại publication cũ. |
| **NFR-04** | Observability | Mỗi run có trạng thái, thời gian bắt đầu/kết thúc, số lượng theo bước và lỗi có ngữ cảnh; tìm được run gây ra một publication. |
| **NFR-05** | Data quality | Báo cáo null/invalid/duplicate theo định nghĩa rõ ràng; bài kiểm tra chất lượng quan trọng phải chặn publication thay vì chỉ ghi log. Danh sách gate chốt trong Test Plan. |
| **NFR-06** | Performance | Đo thời gian từng bước, tổng thời gian, số record xử lý và peak RAM ở pilot. Ngưỡng chấp nhận **TBD sau baseline**, không tự giả định phải dùng distributed processing. |
| **NFR-07** | Scale readiness | Quy trình chạy theo cấu hình khu vực/nguồn, không hard-code một số lượng POI. Thử với tập dữ liệu tăng kích thước và báo cáo bottleneck trước khi mở rộng. |
| **NFR-08** | Security | Token/credential không xuất hiện trong Git, log hoặc dataset xuất bản; truy cập nội dung bị hạn chế phải được kiểm soát theo quyền nguồn. |
| **NFR-09** | Data governance | Lưu tên nguồn, thời điểm, release và điều kiện sử dụng; không tự động đưa reviews/nguồn có hạn chế vào catalog công bố. |
| **NFR-10** | Maintainability | Có cấu hình, README chạy thử, kiểm thử tự động và đường dẫn đầu vào/đầu ra rõ ràng; không yêu cầu sửa trực tiếp mã nguồn mỗi lần đổi snapshot. |

**Về ngưỡng:** SRS v1.0 chưa đặt con số tùy ý cho tốc độ, RAM, tỷ lệ matching hoặc freshness. Sau Data Profiling và benchmark đầu tiên, nhóm sẽ cập nhật **ngưỡng mục tiêu + môi trường đo + tập dữ liệu chuẩn** vào SRS/Test Plan trước khi coi các NFR tương ứng là đạt.

## 7. Quy tắc dữ liệu bắt buộc

1. Định danh `store_id` đại diện cho **chi nhánh**, không cho brand; một nguồn có thể thiếu thông tin nhưng không được làm mất source ID hợp lệ.
2. Phân loại F&B phải ánh xạ được về nhãn nguồn; trường hợp không xác định phải được báo cáo, không tự ép thành một nhóm phù hợp.
3. Điểm ngoài polygon hoặc thiếu tọa độ không được âm thầm tính vào số cửa hàng **trong phạm vi**; giữ số lượng và lý do xử lý riêng.
4. Cặp `review` chưa được xác minh không được coi là cặp `match` trong catalog chính thức.
5. Quyết định thủ công phải có provenance; không để lần chạy lại vô tình ghi đè quyết định đã xác nhận.
6. Record biến mất khỏi snapshot chỉ có trạng thái **không còn thấy ở nguồn**, không tự mang nhãn `closed`.
7. Báo cáo **overlap** giữa Overture/OSM phải theo định nghĩa match được xác nhận/được chấp nhận, không suy ra mức bao phủ toàn bộ F&B ở Khánh Hội.
8. Nội dung đánh giá, hình ảnh và metadata từ nguồn hạn chế không được đưa vào output chia sẻ nếu không có quyền thích hợp.

## 8. Kiểm thử nghiệm thu MVP

| ID | Dữ liệu/tình huống thử | Điều kiện đạt | Liên kết |
|---|---|---|---|
| **AT-01** | Snapshot Overture + OSM, có version | Cả hai nguồn được đọc; có manifest và thống kê records | FR-01, FR-15 |
| **AT-02** | Điểm trong, ngoài, trên ranh giới; POI không thuộc F&B | Kết quả lọc nhất quán theo rule đã công bố, có số bản ghi loại và lý do | FR-02–04 |
| **AT-03** | Tên/địa chỉ khác định dạng nhưng cùng chi nhánh có bằng chứng | Được phân loại match đúng, giữ cả hai source links | FR-05–07, FR-11 |
| **AT-04** | Hai chi nhánh cùng thương hiệu ở hai vị trí | Không gộp chỉ vì tên giống; có lý do quyết định | FR-07–08 |
| **AT-05** | Cặp thiếu bằng chứng và một quyết định thủ công | Cặp nằm trong queue, quyết định được lưu, có thể tái sử dụng | FR-09 |
| **AT-06** | Catalog có trường nguồn mâu thuẫn | Xem được trường được chọn, giá trị nguồn và nguồn gốc | FR-10–12 |
| **AT-07** | Analyst đọc catalog và coverage report | Truy vấn được, các nhóm số liệu có định nghĩa và kiểm tra đối soát | FR-13–14 |
| **AT-08** | Chạy cùng input hai lần | Nội dung store/source links logic tương đương, không tăng bản ghi giả | FR-16, NFR-02 |
| **AT-09** | Cố ý gây lỗi trước khi công bố | Không có publication dở dang; publication cũ còn đọc được | FR-17, NFR-03 |
| **AT-10** | Bộ cặp match/non-match gán nhãn độc lập | Có confusion matrix, Precision/Recall/F1 và ví dụ lỗi | FR-18 |

Kiểm thử phải chạy được trên tập fixture nhỏ, không phụ thuộc gọi crawler live; trước nghiệm thu cần thêm một lần chạy với **snapshot thật** và lưu output/manifest/báo cáo. Ngưỡng chất lượng matching sẽ được chốt theo độ khó và kết quả gán nhãn, không tự nhận chính xác tuyệt đối.

## 9. Truy vết yêu cầu

| Business requirement | Product feature | Software requirements |
|---|---|---|
| BR-01 | PF-01–03 | FR-01–05, FR-15 |
| BR-02, BR-04 | PF-04–06 | FR-06–10, FR-18 |
| BR-03 | PF-07 | FR-11–12 |
| BR-05 | PF-08 | FR-13 |
| BR-06 | PF-09 | FR-04, FR-14, FR-18 |
| BR-07 | PF-10 | FR-10, FR-15–17 |
| BR-08 | PF-11 | FR-19 |
| BR-09 | PF-12–13 | FR-20–21 |
| BR-10 | PF-14–15 | FR-22–23 |
| BR-11 | PF-16 | FR-24 |

## 10. Các quyết định còn cần xác minh

- **Ranh giới:** tệp polygon Khánh Hội, nguồn, phiên bản và cách xử lý điểm đúng biên.
- **Danh mục F&B:** loại hình Overture/OSM nào được tính; các POI thiếu nhãn xử lý ra sao.
- **Matching:** cách chọn tập cặp gán nhãn, tỷ lệ match/non-match, ngưỡng match/review, chính sách xử lý xung đột và ổn định ID khi cập nhật nhiều snapshot.
- **Publication:** định dạng đầu ra, cách xác định current version và quy tắc rollback sẽ thống nhất ở Pipeline Design.
- **Mục tiêu định lượng:** SLA/freshness, giới hạn thời gian/RAM, quality gates và ngưỡng precision/recall được thiết lập sau baseline.
- **Crawler/reviews:** inventory code hiện có, trạng thái vận hành và quyền truy cập trước khi lên kế hoạch Release 2.

---

**Ranh giới tài liệu:** SRS định nghĩa yêu cầu kiểm thử được, không quyết định DuckDB/PostgreSQL/MinIO, thuật toán matching cụ thể, schema vật lý hay quy trình UI. Các nội dung đó lần lượt thuộc Architecture, Data Model, Pipeline Design và Use Case Specification.
