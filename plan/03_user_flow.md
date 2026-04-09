# User Flow

## 1. Mục tiêu file

Tài liệu này mô tả lại hành trình người dùng sau khi MVP đã được đổi sang hướng có giao diện chỉnh chu và nhập liệu theo danh sách. Đây là nguồn sự thật cho UX logic, state machine và session workflow.

## 2. Nội dung chính

### User journey từ lúc mở tool tới lúc nhận kết quả

Luồng người dùng tối thiểu của MVP:

1. Người dùng mở giao diện local của tool.
2. Người dùng nhìn thấy một danh sách item, mỗi item có ô nhập link TikTok và ô chọn ảnh sản phẩm.
3. Người dùng bấm nút `thêm dòng` để tạo thêm item mới khi cần.
4. Người dùng nhập lần lượt nhiều cặp `link + ảnh` trong danh sách.
5. Hệ thống validate từng dòng và báo lỗi rõ nếu dữ liệu chưa đủ hoặc sai format.
6. Người dùng bấm nút `chạy session` hoặc `xử lý danh sách`.
7. Hệ thống validate toàn bộ session, tạo queue xử lý tuần tự.
8. UI hiển thị trạng thái từng item trong lúc chạy.
9. Khi session kết thúc, UI hiển thị item thành công, item lỗi và đường dẫn output tương ứng.

### Flow tối thiểu cho MVP

MVP cần có một giao diện local chỉnh chu với các thành phần tối thiểu sau:

- Danh sách item dạng hàng dọc hoặc table đơn giản.
- Mỗi dòng có ô nhập `link TikTok public`.
- Mỗi dòng có control chọn `ảnh sản phẩm` từ máy.
- Nút `thêm dòng`.
- Nút `xóa dòng` hoặc loại bỏ item trước khi chạy.
- Nút `chạy session`.
- Khu vực hiển thị trạng thái tổng của session.
- Khu vực hiển thị trạng thái riêng của từng item.

MVP không cần timeline preview, drag-and-drop phức tạp, live preview video hoặc chỉnh tay scene list.

### Flow lỗi khi input hỏng, link lỗi, ảnh lỗi, render lỗi

#### Lỗi trước khi bắt đầu session

- Nếu còn bất kỳ dòng nào thiếu link hoặc thiếu ảnh, hệ thống không cho start session.
- Nếu một dòng có URL sai hoặc ảnh lỗi định dạng, dòng đó phải hiện lỗi rõ ngay trong UI.
- Nếu vượt hard cap số dòng, session không được bắt đầu.

#### Lỗi runtime theo item

- Nếu một item tải video không được hoặc render fail, item đó chuyển sang `failed`.
- Session không bị hủy toàn bộ chỉ vì một item runtime fail.
- Hệ thống tiếp tục sang item kế tiếp sau khi đã ghi log và metadata cho item lỗi.

#### Lỗi session-level

- Nếu session không thể khởi tạo workspace hoặc không thể vào queue runner, session chuyển sang `failed_session`.
- Loại lỗi này khác với lỗi của từng item và phải được hiển thị riêng.

### Trạng thái hệ thống nên có

#### Trạng thái session

- `draft`
- `validating_session`
- `ready_to_run`
- `running`
- `completed_with_success`
- `completed_with_partial_failure`
- `failed_session`

#### Trạng thái item

- `draft`
- `invalid`
- `queued`
- `validating`
- `downloading`
- `processing`
- `completed`
- `failed`

### UX logic tối thiểu cho UI chỉnh chu

- UI phải cho phép người dùng thêm nhiều dòng một cách rõ ràng bằng nút `thêm dòng`.
- Mỗi dòng phải tự chứa đầy đủ thông tin của một item, tránh nhập rời rạc ở nhiều khu vực.
- Session chỉ được start khi tất cả dòng đều hợp lệ.
- Khi session đang chạy, UI phải hiển thị item hiện tại đang xử lý.
- Sau khi một item xong hoặc fail, UI phải cập nhật ngay trạng thái của dòng đó.
- Cuối session, UI phải có summary dễ hiểu: tổng số item, số thành công, số lỗi.
- UI phải hiển thị được đường dẫn hoặc nút mở thư mục output của item nếu implementation hỗ trợ.

## 3. Quyết định thiết kế chính

- Chọn `list-based UI` làm flow chính thức của MVP.
- Chọn `session validation trước khi start` để chặn lỗi dữ liệu đầu vào.
- Chọn `sequential processing với per-item status` thay vì xử lý song song.
- Chọn `partial success session` làm hành vi mặc định khi runtime fail theo item.
- Chọn `UI phục vụ nhập liệu + quan sát trạng thái`, chưa đi vào preview/editing nâng cao.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu state session và state item trộn lẫn, UI sẽ rất khó hiểu.
- Nếu không cho thấy item nào đang chạy, người dùng sẽ tưởng tool treo ở các session dài.
- Nếu session bị block bởi lỗi input nhưng UI không chỉ rõ dòng nào sai, trải nghiệm sẽ rất tệ.
- Nếu failure handling không thống nhất, có thể phát sinh tình trạng một item lỗi làm cả session dừng ngoài ý muốn.

## 5. Tiêu chí hoàn thành

- File này phải mô tả rõ user journey theo danh sách nhiều item.
- Phải định nghĩa được state của session và state của item.
- Phải nêu rõ hành vi `thêm dòng`, `xóa dòng`, `chạy session`, `partial failure`.
- Team đọc file này phải đủ thông tin để build một UI MVP chỉnh chu thay vì chỉ một form tối giản.
