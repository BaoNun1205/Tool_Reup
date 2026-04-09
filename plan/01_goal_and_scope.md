# Goal And Scope

## 1. Mục tiêu file

Tài liệu này khóa lại phạm vi sản phẩm sau khi đã cập nhật yêu cầu mới: MVP bắt buộc phải có giao diện chỉnh chu và hỗ trợ nhập theo danh sách nhiều item. File này giúp team không tiếp tục triển khai theo giả định cũ kiểu `single-job + UI tối thiểu`.

## 2. Nội dung chính

### Mục tiêu chính của dự án

Mục tiêu chính là tạo một công cụ render video có giao diện local hoàn chỉnh ở mức MVP, cho phép người dùng nhập một danh sách gồm nhiều cặp `link TikTok public + ảnh sản phẩm`, sau đó xử lý tuần tự từng item để sinh ra các video MP4 mới đã tăng tốc 1.2x, xử lý audio an toàn, shuffle scene có kiểm soát và chèn ảnh sản phẩm theo bố cục ổn định.

### Phạm vi MVP

MVP bắt buộc bao gồm:

- Giao diện local chỉnh chu, không phải chỉ là CLI trần.
- Danh sách input trong UI, mỗi dòng gồm 1 link TikTok public và 1 ảnh sản phẩm.
- Nút `thêm dòng` để người dùng nhập tiếp item mới.
- Khả năng xóa hoặc sửa một dòng trước khi chạy phiên.
- Validation rõ ràng cho từng dòng và cho toàn bộ danh sách trước khi bắt đầu xử lý.
- Session runner xử lý tuần tự từng item trong danh sách.
- Trạng thái rõ cho từng item: chờ, đang chạy, thành công, thất bại.
- Media pipeline hoàn chỉnh cho mỗi item: download, normalize, speed 1.2x, scene detect, scene qualify, constrained shuffle, overlay, audio finishing, export.
- Xuất artifact riêng cho từng item và summary cho cả session.

### Ngoài phạm vi MVP

Các phần sau vẫn để sau MVP:

- Xử lý song song nhiều item cùng lúc.
- Timeline editor, preview real-time hoặc chỉnh tay scene list.
- Drag-and-drop reorder danh sách có tương tác phức tạp.
- Background removal tự động cho JPG.
- Semantic scene ranking bằng AI.
- Auto detect face/text để né overlay.
- Cloud render, queue phân tán hoặc multi-user system.

### Các non-goal quan trọng

Non-goal ở giai đoạn này là:

- Không build automation cho CapCut.
- Không chỉnh file draft của CapCut.
- Không dùng mobile automation.
- Không biến UI thành full editor với timeline.
- Không tối ưu throughput lớn hoặc xử lý song song ngay ở MVP.

### Vì sao chọn hướng tool render riêng có UI local thay vì CapCut/mobile automation

Lý do chọn hướng này:

- Kiểm soát được cả media pipeline và trải nghiệm nhập liệu.
- Có thể bàn giao cho người dùng nội bộ bằng một UI rõ ràng hơn CLI.
- Không phụ thuộc vào UI bên ngoài của app khác.
- Dễ log, dễ debug và dễ phát triển tiếp thành session-based workflow.

### Vì sao chọn `UI local dạng danh sách` thay vì UI phức tạp hơn

Lý do chọn `local web UI hoặc local app UI mỏng theo mô hình danh sách` cho MVP là:

- Dễ build hơn timeline editor hoặc desktop-native phức tạp.
- Phù hợp bài toán vận hành nhiều cặp input lặp lại.
- Cho phép team ưu tiên tính chạy chắc của media core nhưng vẫn có bề mặt sản phẩm đủ dùng thật.

### Các giả định nền tảng cho bản đầu

Các giả định nền tảng của MVP:

- Một session có thể chứa nhiều item, nhưng xử lý tuần tự từng item.
- Mỗi item chỉ có 1 video TikTok public và 1 ảnh sản phẩm.
- UI hỗ trợ thêm nhiều dòng bằng nút thêm, nhưng không hỗ trợ flow nhập liệu quá phức tạp.
- Một session trong MVP nên giới hạn ở quy mô vừa phải; hard cap đề xuất là 20 item mỗi session để tránh kéo dài runtime và làm UI khó kiểm soát.
- PNG alpha vẫn là đường ưu tiên cho chất lượng overlay.
- JPG thường vẫn đi theo fallback panel, không giải bài toán cutout tự động.

## 3. Quyết định thiết kế chính

- Chọn `UI chỉnh chu là phạm vi MVP`, không để sau.
- Chọn `danh sách nhiều item + xử lý tuần tự` là mode chính thức của sản phẩm.
- Chọn `validate cả session trước khi start` để chặn phiên chạy bị bẩn ngay từ đầu.
- Chọn `runtime fail theo item, không làm hỏng cả session` nếu lỗi xảy ra sau khi phiên đã bắt đầu.
- Chọn `UI phục vụ nhập liệu và theo dõi trạng thái`, không biến thành editor tương tác cao.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu không cập nhật scope triệt để, code backend sẽ tiếp tục lệch sang giả định single-job.
- UI vào phạm vi MVP làm tăng yêu cầu về state management và error presentation.
- Session validation và item runtime handling phải được tách rõ, nếu không UX sẽ rất mơ hồ.
- Nếu không đặt hard cap hợp lý cho số dòng, MVP dễ bị kéo sang bài toán queue lớn ngoài phạm vi.

## 5. Tiêu chí hoàn thành

- File này phải xác nhận rõ UI chỉnh chu và workflow theo danh sách đã là một phần của MVP.
- Phải tách rõ phần còn ngoài phạm vi, nhất là parallel processing và timeline editing.
- Phải nêu được giả định `session-based sequential processing` để các file sau bám theo.
- Team đọc file này phải dừng hẳn tư duy `single-job CLI là đủ`.
