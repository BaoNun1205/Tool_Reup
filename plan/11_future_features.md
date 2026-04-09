# Future Features

## 1. Mục tiêu file

Tài liệu này cập nhật lại future features sau khi UI chỉnh chu và workflow theo danh sách đã được kéo vào MVP. Những gì còn nằm trong file này là các bước mở rộng sau MVP, không phải scope của bản build hiện tại.

## 2. Nội dung chính

### Giai đoạn ưu tiên 1: Nâng workflow theo danh sách

Nhóm này mở rộng đúng hướng session-based product nhưng chưa làm thay đổi kiến trúc quá mạnh.

Tính năng gồm:

- Retry failed item từ UI.
- Chỉ chạy lại các item đã chọn.
- Duplicate row detection tốt hơn.
- Import/export danh sách item.
- Mở thư mục output trực tiếp từ UI.

Lợi ích của nhóm này là làm workflow theo danh sách tiện hơn và gần với use case vận hành thực tế hơn.

### Giai đoạn ưu tiên 2: Nâng chất lượng visual/audio

Nhóm này cải thiện output của từng item.

Tính năng gồm:

- Nhiều style overlay hơn.
- Tinh chỉnh loudness theo profile nội dung.
- Transition scene ngắn có kiểm soát.
- Tối ưu heuristic scene quality.
- Preview thumbnail của cảnh bị drop hoặc được giữ.

Lợi ích của nhóm này là làm video final đẹp hơn mà chưa phải thêm AI nặng.

### Giai đoạn ưu tiên 3: Thông minh hóa session và edit

Nhóm này bắt đầu thêm hiểu biết nội dung và hỗ trợ điều phối queue thông minh hơn.

Tính năng gồm:

- Pause/resume session.
- Reorder queue nâng cao.
- Subject-aware overlay placement.
- Semantic scene ranking.
- Auto background removal cho ảnh sản phẩm thường.

Lợi ích của nhóm này là tăng chất lượng đầu ra và tăng tiện ích vận hành, nhưng complexity cũng tăng mạnh.

### Giai đoạn ưu tiên 4: Scale và platformization

Nhóm này dành cho khi sản phẩm đã chứng minh giá trị và cần mở rộng quy mô.

Tính năng gồm:

- Parallel processing nhiều item.
- Batch queue phân tán.
- Cloud render hoặc worker pool.
- Multi-user session management.
- API hoặc giao diện tích hợp với hệ thống khác.

Lợi ích của nhóm này là chuyển sản phẩm từ local operator tool sang platform có khả năng scale.

### Những tính năng đẹp nhưng chưa cần làm ngay

- Drag-and-drop reorder danh sách với animation.
- Thumbnail preview trực tiếp trong mỗi row.
- Template UI nâng cao và preset visual hàng loạt.
- Auto group item theo sản phẩm.
- Subtitle generation.

### Những tính năng có thể thay đổi kiến trúc nếu thêm sau này

- Parallel item processing.
- Pause/resume queue bền vững qua nhiều phiên.
- Import/export list có schema phong phú.
- AI scene ranking hoặc subject detection.
- Multi-user backend hoặc cloud execution.

## 3. Quyết định thiết kế chính

- Chọn `session workflow features` là nhánh mở rộng gần nhất sau MVP.
- Chọn `stability trước intelligence` như định hướng tiếp theo.
- Chọn `ghi rõ feature làm đổi kiến trúc` để không khóa đường phát triển về sau.
- Không kéo các queue-control hoặc AI feature vào MVP mới.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Khi UI đã vào MVP, team rất dễ tiếp tục kéo thêm nhiều workflow feature chưa cần thiết.
- Một số tính năng queue nhìn nhỏ nhưng thực chất làm thay đổi orchestration đáng kể.
- Nếu không phân biệt rõ future features với MVP, sản phẩm sẽ lại bị scope creep.
- Feature liên quan scale và AI dễ khiến kiến trúc local MVP bị over-design quá sớm.

## 5. Tiêu chí hoàn thành

- File này phải phản ánh đúng những gì đã được kéo vào MVP và những gì còn ở tương lai.
- Phải nhóm future features theo hướng session workflow, output quality và scale.
- Phải nêu được tính năng nào làm đổi kiến trúc nếu thêm sau.
- Team đọc file này phải không nhầm `UI chỉnh chu + danh sách item` là future feature nữa.
