# Input Output Spec

## 1. Mục tiêu file

Tài liệu này đặc tả lại input/output theo mô hình session-based. Nguồn sự thật mới không còn là `một job = một cặp input` nữa, mà là `một session = một danh sách nhiều item`, trong đó mỗi item vẫn giữ media pipeline riêng.

## 2. Nội dung chính

### Đặc tả input chi tiết

Input của MVP gồm 2 lớp:

#### Session-level input

- `items`: bắt buộc. Danh sách các item cần xử lý.
- `output_root_dir`: tùy chọn theo implementation. Thư mục gốc chứa kết quả của session.

#### Item-level input

Mỗi item trong `items` gồm:

- `source_video_url`: bắt buộc. URL video TikTok public.
- `product_image`: bắt buộc. File ảnh sản phẩm.
- `output_basename`: tùy chọn.
- `shuffle_seed`: tùy chọn.

### Input bắt buộc và input tùy chọn

Input bắt buộc ở cấp session:

- `items`

Input bắt buộc ở cấp item:

- `source_video_url`
- `product_image`

Input tùy chọn:

- `output_root_dir`
- `output_basename`
- `shuffle_seed`

### Validation rules cho session

- Danh sách `items` phải có ít nhất 1 dòng.
- Không chấp nhận dòng rỗng hoặc dòng chỉ điền một nửa thông tin.
- MVP nên chặn nếu số dòng vượt hard cap `20 item / session`.
- Session chỉ được phép bắt đầu khi tất cả dòng trong danh sách đều hợp lệ về mặt input.

### Validation rules cho từng item

#### `source_video_url`

- Phải là URL hợp lệ.
- Domain phải là nguồn TikTok public được hỗ trợ.
- Không chấp nhận URL private, URL yêu cầu đăng nhập hoặc URL playlist/channel.
- Sau khi vào runtime download stage, video phải tải được và đọc được metadata cơ bản.

#### `product_image`

- Chấp nhận `PNG`, `JPG`, `JPEG`.
- Kích thước tối thiểu chấp nhận được phải đủ để render không vỡ quá mức.
- Kích thước khuyến nghị vẫn là cạnh dài từ `800 px` trở lên.
- Nếu là PNG có alpha thì dùng flow overlay ưu tiên.
- Nếu là JPG hoặc PNG không alpha thì chuyển sang fallback layout.

#### `output_basename`

- Nếu có, chỉ nên chứa ký tự an toàn cho tên file.
- Nếu không có, hệ thống tự sinh tên theo `item_index` hoặc `item_id`.

#### `shuffle_seed`

- Nếu có, phải là số nguyên hợp lệ.
- Nếu không có, hệ thống tự sinh seed theo item.

### Format đầu ra tối thiểu

Output tối thiểu của MVP gồm 2 lớp:

#### Session-level output

- `session_summary.json`: tóm tắt toàn bộ session.
- `session_log.txt`: log tổng hợp các state chuyển qua ở cấp session nếu implementation muốn tách riêng.

#### Item-level output

Mỗi item phải có thư mục riêng, ví dụ `item_001`, `item_002`, trong đó tối thiểu có:

- `final_video.mp4`
- `final_audio.m4a`
- `job_metadata.json`
- `process_log.txt`

### Format đầu ra media tối thiểu cho mỗi item

- Canvas mục tiêu: `1080 x 1920`
- Frame rate mục tiêu: `30 fps`
- Audio mục tiêu: `AAC`, `48 kHz`
- Video final phải có thời lượng nhất quán với audio final của cùng item

### Metadata/log nên sinh ra

#### `job_metadata.json` của từng item

- `job_id`
- `session_id`
- `item_index`
- `source_url`
- `source_duration`
- `working_duration_after_speed`
- `scene_detected_count`
- `scene_kept_count`
- `scene_dropped_count`
- `shuffle_seed_used`
- `overlay_mode_used`
- `image_type_detected`
- `audio_warnings`
- `render_warnings`
- `status`
- `final_output_paths`

#### `session_summary.json`

- `session_id`
- `item_count_total`
- `item_count_completed`
- `item_count_failed`
- `started_at`
- `finished_at`
- danh sách item và trạng thái cuối cùng của từng item

### Các case input xấu cần xử lý

- Danh sách có dòng hợp lệ lẫn dòng thiếu ảnh hoặc thiếu link.
- Người dùng thêm nhiều dòng nhưng một số ảnh đã bị di chuyển khỏi máy trước lúc chạy.
- Có hai dòng trùng hệt nhau.
- Số lượng dòng quá lớn so với hard cap của MVP.
- Một item dùng link đúng cú pháp nhưng video đã bị gỡ.
- Một item dùng ảnh quá nhỏ hoặc lỗi định dạng.

## 3. Quyết định thiết kế chính

- Chốt `session list` là contract input chính của UI.
- Chốt `mỗi item có thư mục output riêng` để dễ xem kết quả và debug.
- Chốt `session summary` là artifact bắt buộc, không chỉ có artifact theo item.
- Chốt `pre-run validation cho toàn bộ session` để tránh phiên chạy bị bẩn ngay từ đầu.
- Chốt `per-item runtime result` để session vẫn tiếp tục nếu một item fail giữa chừng.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Khi input chuyển sang dạng list, validation phải rõ ở cả cấp dòng và cấp session.
- Nếu output không được group theo item rõ ràng, UI khó map kết quả với từng dòng đầu vào.
- Nếu cho phép session start khi còn dòng invalid, runtime UX sẽ rối và khó support.
- Session summary thiếu thông tin sẽ khiến người dùng không biết cần xem item nào khi phiên dài.

## 5. Tiêu chí hoàn thành

- File này phải định nghĩa rõ session input, item input và output tương ứng.
- Phải chốt được artifact theo item và summary theo session.
- Phải nêu được validation rule cho cả cấp dòng và cấp danh sách.
- Team đọc file này phải có thể dùng ngay để thiết kế contract giữa UI, session orchestrator và media pipeline.
