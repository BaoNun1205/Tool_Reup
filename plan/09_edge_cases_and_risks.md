# Edge Cases And Risks

## 1. Mục tiêu file

Tài liệu này cập nhật lại edge cases và rủi ro sau khi sản phẩm chuyển sang mô hình có UI chỉnh chu và xử lý theo danh sách nhiều item. Trọng tâm không còn chỉ là media lỗi, mà còn là session state, mixed-result flow và UX khi một số item fail.

## 2. Nội dung chính

### Danh sách edge case thực tế

#### Session và UI

- Danh sách có dòng trống hoặc dòng điền thiếu một nửa dữ liệu.
- Người dùng thêm quá nhiều dòng vượt hard cap của MVP.
- Hai dòng trùng hệt nhau.
- Người dùng chọn ảnh rồi sau đó ảnh bị xóa hoặc di chuyển trước khi bấm chạy.
- Trong lúc session đang chạy, người dùng muốn sửa item đang queue.

#### URL và nguồn video

- URL đúng cú pháp nhưng video private hoặc đã bị xóa.
- URL public nhưng downloader hiện tại không lấy được media.
- Một session có nhiều item, trong đó chỉ một số item gặp lỗi download.

#### Chất lượng video nguồn

- Video quá ngắn hoặc quá ít scene.
- Video có quá nhiều cut siêu ngắn.
- Video VFR mạnh gây lệch sync.
- Video landscape hoặc aspect ratio lạ khiến bố cục portrait khó đẹp.

#### Ảnh sản phẩm

- Ảnh quá nhỏ hoặc mờ.
- PNG alpha bẩn.
- JPG nền rối.
- Một session có item PNG đẹp và item JPG xấu xen kẽ nhau.

#### Audio

- Audio quá nhỏ.
- Audio đã clip từ nguồn.
- Không có audio nhưng video vẫn dùng được.

#### Render và vận hành

- Một item fail ở giữa session.
- Session bị dừng vì lỗi session-level trước khi item đầu tiên bắt đầu.
- Session chạy xong nhưng người dùng không biết item nào sinh output ở đâu.
- UI hiển thị sai trạng thái so với backend orchestrator.

### Rủi ro kỹ thuật

Các rủi ro kỹ thuật ưu tiên cao:

- Downloader nguồn public có thể gãy khi nguồn thay đổi.
- Chuẩn hóa media không đủ chặt dẫn tới lỗi sync kéo dài.
- State session và state item bị trộn, làm orchestrator khó ổn định.
- UI và backend lệch contract cho per-item status.

### Rủi ro sản phẩm

Các rủi ro sản phẩm chính:

- UI có nhưng không đủ rõ, người dùng vẫn khó dùng thực tế.
- Session có mixed result nhưng summary không dễ hiểu.
- Output đủ kỹ thuật nhưng JPG fallback chưa đủ đẹp.
- Người dùng kỳ vọng danh sách sẽ chạy song song, trong khi MVP chỉ chạy tuần tự.

### Rủi ro vận hành

Các rủi ro vận hành chính:

- Một item lỗi làm cả session dừng nếu orchestrator không xử lý đúng.
- Session dài nhưng không có progress đủ rõ, gây cảm giác treo.
- Log theo item có nhưng session summary không map lại được dòng input ban đầu.
- Output được tạo nhưng khó tìm vì thư mục không gắn rõ với item index.

### Mức độ ưu tiên xử lý từng rủi ro và hướng fallback

#### Ưu tiên cao, phải xử lý trong MVP

- Dòng invalid trong UI: block start session và highlight đúng dòng.
- Runtime fail theo item: đánh dấu `failed`, ghi log rồi tiếp tục item kế tiếp.
- Session state không rõ: bắt buộc có session summary và per-item status.
- VFR / normalize lỗi: bắt buộc chuẩn hóa về working media ổn định.
- JPG thường: bắt buộc có fallback panel.
- Log lỗi stage: bắt buộc có `process_log.txt` theo item.

#### Ưu tiên trung bình, nên xử lý trong MVP nếu không đội scope quá lớn

- Dòng trùng nhau: cho phép nhưng nên warning rõ.
- Người dùng muốn sửa queue khi session đang chạy: MVP có thể khóa editing trong lúc chạy.
- Video quá ít scene: vẫn render nhưng warning.
- Audio thiếu: vẫn render với warning nếu pipeline quyết định tạo silent final audio.

#### Ưu tiên thấp, chấp nhận defer sau MVP

- Pause/resume session.
- Retry failed item riêng biệt từ UI.
- Drag reorder queue trong lúc chạy.
- Subject-aware overlay placement.
- Batch parallel processing.

### Case nào chấp nhận defer sau MVP

Các case chấp nhận defer:

- Import danh sách từ CSV hoặc Excel.
- Duplicate detection thông minh vượt quá warning cơ bản.
- Preview thumbnail cho từng row trước khi chạy.
- Điều khiển queue nâng cao như pause, retry, reorder động.

## 3. Quyết định thiết kế chính

- Chọn `pre-run block` cho lỗi input ở cấp session.
- Chọn `continue on item failure` cho lỗi runtime sau khi session đã bắt đầu.
- Chọn `session summary + per-item logs` là lớp an toàn bắt buộc.
- Chọn `khóa editing khi session đang chạy` cho MVP để tránh state complexity.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu team không phân biệt rõ validation error và runtime error, UI sẽ rất khó hiểu.
- Nếu không chốt item failure có được bỏ qua hay không, session runner sẽ dễ phát sinh hành vi không nhất quán.
- Nếu session summary không map lại item index rõ ràng, support sẽ cực khó làm việc khi session dài.
- UI chỉnh chu nhưng state handling không chắc sẽ tạo cảm giác sản phẩm “bị giả” dù media pipeline chạy đúng.

## 5. Tiêu chí hoàn thành

- File này phải phản ánh cả edge cases của UI/session và media pipeline.
- Phải nêu rõ cách xử lý mixed-success session.
- Phải gán mức ưu tiên xử lý cho các case phát sinh do danh sách nhiều item.
- Team đọc file này phải biết rõ session nên block ở đâu và tiếp tục ở đâu.
