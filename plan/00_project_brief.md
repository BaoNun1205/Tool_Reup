# Project Brief

## 1. Mục tiêu file

Tài liệu này mô tả bức tranh tổng thể của dự án `Auto TikTok Video Editor for Product Overlay` sau khi đã chốt lại yêu cầu sản phẩm: hệ thống không chỉ có media pipeline, mà còn phải có giao diện người dùng chỉnh chu để nhập dữ liệu và vận hành theo danh sách nhiều item. File này giúp team dev và Builder AI hiểu đúng sản phẩm cần build trước khi đi vào chi tiết kỹ thuật.

## 2. Nội dung chính

### Dự án này giải quyết vấn đề gì

Người bán hàng và team vận hành nội dung đang phải vừa chuẩn bị input thủ công, vừa edit video thủ công để tạo các video có chèn ảnh sản phẩm. Nếu chỉ có một pipeline chạy bằng command-line thì vẫn còn thiếu lớp vận hành phù hợp cho người dùng thực tế. Dự án này giải quyết cả hai phần: một tool render riêng và một giao diện chỉnh chu để người dùng nhập nhiều cặp `link TikTok + ảnh sản phẩm`, thêm tiếp item mới, rồi chạy xử lý theo danh sách.

### Ai là người dùng chính

Người dùng chính của MVP là:

- Seller hoặc team bán hàng muốn nhập nhiều video nguồn và ảnh sản phẩm trong một phiên làm việc.
- Media operator cần chuẩn bị một danh sách item rồi để tool xử lý tuần tự.
- Builder nội bộ cần một hệ thống vừa có pipeline render riêng, vừa có UI đủ sạch để bàn giao sử dụng nội bộ.

### Kết quả đầu ra của tool là gì

Đầu ra chính của sản phẩm ở MVP gồm:

- Một giao diện người dùng chỉnh chu để nhập và quản lý danh sách item.
- Mỗi item trong danh sách sinh ra 1 file video MP4 final.
- Mỗi item sinh thêm 1 file audio final và metadata/log riêng.
- Toàn bộ phiên chạy sinh ra thêm một `session summary` để biết item nào thành công, item nào lỗi.

### Giá trị thực tế của tool

Giá trị thực tế của sản phẩm là:

- Giảm mạnh thao tác tay khi người dùng cần xử lý nhiều cặp input liên tiếp.
- Chuẩn hóa workflow từ khâu nhập liệu tới render.
- Giảm lệ thuộc vào editor bên ngoài như CapCut.
- Tạo nền cho các bước mở rộng sau này như retry failed item, import/export danh sách hoặc preset hàng loạt.

### Mô tả ngắn cách tool hoạt động từ đầu vào tới đầu ra

Người dùng mở giao diện, nhập nhiều dòng trong danh sách. Mỗi dòng gồm 1 link video TikTok public và 1 ảnh sản phẩm, có thể bấm nút thêm để tạo dòng mới. Sau khi bấm chạy, hệ thống validate toàn bộ danh sách, tạo queue xử lý tuần tự từng item, tải video nguồn, chuẩn hóa media, tăng tốc 1.2x, detect scene, shuffle scene theo logic có kiểm soát, chèn ảnh sản phẩm, render ra MP4 final cho từng item và ghi metadata/log tương ứng. Kết thúc phiên, UI hiển thị trạng thái từng item cùng kết quả tổng hợp.

### Định nghĩa thành công ở mức sản phẩm

Sản phẩm được coi là thành công ở mức MVP khi:

- Có giao diện chỉnh chu, dễ dùng, không còn chỉ là pipeline trần.
- Người dùng có thể thêm nhiều item trong một danh sách bằng nút thêm dòng.
- Mỗi item gồm đúng 1 link TikTok public và 1 ảnh sản phẩm.
- Hệ thống xử lý tuần tự từng item trong danh sách, hiển thị rõ item nào đang chạy, item nào xong, item nào lỗi.
- Mỗi item có video final xem được ổn định, audio đủ rõ và không bị clipping rõ rệt.
- Khi một item lỗi ở runtime, phiên xử lý vẫn có thể tiếp tục với item tiếp theo.

## 3. Quyết định thiết kế chính

- Chọn `tool render riêng có UI local` thay vì chỉ có CLI hoặc phụ thuộc editor ngoài.
- Chọn `list-based session processing` cho MVP thay vì chỉ hỗ trợ một item mỗi lần.
- Chọn `xử lý tuần tự từng item` thay vì song song, để giảm độ phức tạp và rủi ro vận hành.
- Chọn `artifact riêng theo item + summary theo session` để dễ debug và dễ theo dõi trong UI.
- Chọn `UI chỉnh chu nhưng workflow đơn giản` thay vì timeline editor hoặc trình dựng phức tạp.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Khi thêm UI, hệ thống phải xử lý cả trạng thái phiên và trạng thái từng item, không còn chỉ là pipeline media thuần.
- Danh sách có thể chứa item hợp lệ lẫn item lỗi, nên phải chốt rõ lúc nào block cả phiên, lúc nào cho phép tiếp tục.
- Nếu UI không đủ rõ ràng, người dùng sẽ khó hiểu vì sao một item fail trong khi item khác vẫn chạy.
- Nếu không tách session summary và item artifact, support nội bộ sẽ khó truy vết lỗi khi xử lý theo danh sách.

## 5. Tiêu chí hoàn thành

- File này phải phản ánh đúng việc sản phẩm cần có UI chỉnh chu, không chỉ pipeline backend.
- Phải nêu rõ workflow theo danh sách nhiều item và nút thêm dòng.
- Phải định nghĩa thành công ở mức sản phẩm theo cả góc nhìn UI lẫn media output.
- Người đọc phải hiểu ngay rằng MVP đã đổi từ `single-job tool` sang `session-based tool có giao diện`.
