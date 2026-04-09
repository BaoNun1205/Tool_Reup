# MVP Definition

## 1. Mục tiêu file

Tài liệu này định nghĩa lại MVP sau khi yêu cầu sản phẩm thay đổi: MVP không còn là một pipeline chạy cho một input duy nhất, mà là một sản phẩm có giao diện chỉnh chu và xử lý theo danh sách nhiều item. File này khóa tiêu chí done cho giai đoạn build tiếp theo.

## 2. Nội dung chính

### Định nghĩa MVP cực rõ

MVP của dự án là một công cụ render riêng có giao diện local chỉnh chu, cho phép người dùng nhập một danh sách nhiều cặp `link TikTok public + ảnh sản phẩm`, bấm thêm dòng khi cần, rồi chạy xử lý tuần tự từng item để sinh ra video MP4 final, audio final, metadata/log theo từng item cùng với summary của cả session.

### Tính năng bắt buộc của MVP

MVP bắt buộc phải có:

- UI local chỉnh chu, không phải chỉ là CLI.
- Danh sách item trong UI.
- Nút `thêm dòng` để nhập tiếp item mới.
- Nút xóa dòng trước khi chạy.
- Validation rõ cho từng dòng và cho cả session.
- Session runner xử lý tuần tự theo đúng thứ tự danh sách.
- Trạng thái rõ cho từng item và cho session.
- Media pipeline đầy đủ cho từng item: download, normalize, speed 1.2x, scene detect, scene qualify, constrained shuffle, overlay, audio finishing, export.
- Artifact riêng cho từng item.
- `session_summary.json` cho cả phiên.

### Tính năng chưa làm ở MVP

Những phần chưa làm ở MVP:

- Xử lý song song nhiều item.
- Pause/resume queue.
- Retry failed item trực tiếp từ UI.
- Timeline preview hoặc chỉnh tay scene list.
- Auto remove background cho JPG.
- Semantic scene ranking.
- Auto né mặt người, chữ hoặc caption.
- Cloud render hoặc multi-user workflow.

### Mức chất lượng chấp nhận được

Mức chất lượng chấp nhận được của MVP là:

- UI nhìn có chủ đích, rõ ràng, không còn là bề mặt nhập liệu sơ sài.
- Người dùng thêm được nhiều dòng và hiểu được từng dòng đại diện cho một item xử lý.
- Session chạy tuần tự mà không gây cảm giác treo vì thiếu status.
- Khi một item lỗi ở runtime, item khác vẫn tiếp tục chạy.
- Final video của từng item phát được ổn định và không lệch sync rõ.
- Product overlay nhìn đủ sạch cho cả PNG và JPG fallback.
- Output và log map được rõ về từng item trong danh sách.

### Tiêu chí “MVP done”

MVP được coi là done khi toàn bộ các điều kiện sau đồng thời đúng:

- Có thể tạo một session gồm nhiều item trong UI.
- Có thể thêm dòng mới trong UI mà không làm vỡ state session.
- Session chỉ bắt đầu khi toàn bộ item đều valid.
- Session xử lý tuần tự từng item end-to-end mà không cần can thiệp tay.
- Một item runtime fail không làm hỏng toàn bộ session.
- Mỗi item sinh ra đúng bộ artifact quy định.
- Session sinh ra summary tổng hợp đúng số thành công và thất bại.

### Những điểm nào cần demo được để coi là thành công

Các demo bắt buộc:

- Demo 1: tạo session có ít nhất 3 dòng trong UI.
- Demo 2: một dòng dùng PNG alpha, một dòng dùng JPG thường, cả hai cùng xử lý trong một session.
- Demo 3: một dòng cố tình fail ở runtime nhưng session vẫn tiếp tục với dòng tiếp theo.
- Demo 4: UI hiển thị rõ trạng thái từng item và summary cuối session.

## 3. Quyết định thiết kế chính

- Chọn `UI chỉnh chu + session workflow` là phần lõi của MVP mới.
- Chọn `sequential processing` để giữ độ chắc cho phiên bản đầu.
- Chọn `partial success session` là hành vi mặc định ở runtime.
- Chọn `artifact theo item + summary theo session` làm tiêu chuẩn done.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu UI không được coi là phần lõi, team sẽ lại quay về tư duy backend-only.
- Nếu không khóa rõ `session done` và `item done`, acceptance sẽ rất mơ hồ.
- Một số người có thể nhầm `list-based processing` với batch engine lớn; MVP cần tránh mở rộng sang bài toán đó.
- Nếu chỉ demo happy path, sản phẩm sẽ chưa chứng minh được behavior đúng với mixed-success session.

## 5. Tiêu chí hoàn thành

- File này phải khẳng định UI và danh sách nhiều item đã là phạm vi bắt buộc.
- Phải nêu rõ `MVP done` ở cả cấp session và cấp item.
- Phải chốt được các demo đại diện cho yêu cầu mới.
- Builder AI đọc file này phải biết ngay phiên bản cần build không còn là single-job tool nữa.
