# Editing Pipeline

## 1. Mục tiêu file

Tài liệu này cập nhật pipeline xử lý media theo mô hình mới: sản phẩm vận hành theo session gồm nhiều item trong UI, nhưng mỗi item vẫn đi qua cùng một media pipeline chuẩn. File này giúp team phân biệt rõ `session pipeline` và `item pipeline`.

## 2. Nội dung chính

### Pipeline chính cho MVP

Pipeline MVP có 2 lớp:

#### Lớp session

1. Session intake từ UI danh sách
2. Session validation
3. Queue creation
4. Sequential item runner
5. Session summary export

#### Lớp item

Mỗi item trong queue sẽ đi qua thứ tự sau:

1. Intake và validation item
2. Download source video
3. Probe media và chuẩn hóa sang working media nội bộ
4. Tăng tốc video/audio lên 1.2x trên working media
5. Detect scene trên bản media đã tăng tốc
6. Lọc scene và chuẩn hóa thành danh sách clip usable
7. Tạo shuffle plan có kiểm soát
8. Cắt clip audio/video theo edit plan
9. Ghép rough cut theo thứ tự scene mới
10. Hoàn thiện audio ở cấp final cut
11. Áp product overlay lên final cut
12. Export artifact của item

### Mô tả rõ từng stage ở lớp session

#### Stage S1: Session intake từ UI danh sách

- Input: danh sách các dòng do người dùng nhập trong UI
- Output: `SessionSpec` ở trạng thái draft
- Bắt buộc cho MVP: có
- Mục tiêu: gom tất cả item đầu vào vào một session logic

#### Stage S2: Session validation

- Input: danh sách item draft
- Output: session hợp lệ để chạy hoặc danh sách lỗi cần sửa trong UI
- Bắt buộc cho MVP: có
- Mục tiêu: chặn phiên chạy nếu còn dòng invalid

#### Stage S3: Queue creation

- Input: session hợp lệ
- Output: queue item theo thứ tự hiển thị trong UI
- Bắt buộc cho MVP: có
- Mục tiêu: khóa thứ tự xử lý cho phiên chạy

#### Stage S4: Sequential item runner

- Input: queue item
- Output: item result lần lượt cho từng phần tử trong danh sách
- Bắt buộc cho MVP: có
- Mục tiêu: điều phối item pipeline tuần tự và giữ state rõ ràng

#### Stage S5: Session summary export

- Input: kết quả cuối của toàn bộ item
- Output: `session_summary.json` và session state cuối
- Bắt buộc cho MVP: có
- Mục tiêu: tổng hợp kết quả phiên xử lý cho UI và debug

### Mô tả item pipeline

Item pipeline của mỗi dòng giữ nguyên định hướng media như trước, vì phần cốt lõi xử lý video không thay đổi. Mỗi item phải độc lập về artifact, log và trạng thái.

#### Item Stage 1: Intake và validation item

- Input: URL TikTok public, ảnh sản phẩm, optional seed hoặc output basename
- Output: item job hợp lệ hoặc lỗi item-level
- Bắt buộc cho MVP: có

#### Item Stage 2: Download source video

- Input: item đã hợp lệ
- Output: source asset local
- Bắt buộc cho MVP: có

#### Item Stage 3: Probe media và chuẩn hóa sang working media nội bộ

- Input: file nguồn vừa tải
- Output: working media
- Bắt buộc cho MVP: có

#### Item Stage 4: Tăng tốc video/audio lên 1.2x

- Input: working media
- Output: processed master
- Bắt buộc cho MVP: có

#### Item Stage 5: Detect scene trên media đã tăng tốc

- Input: processed master video
- Output: raw scene list
- Bắt buộc cho MVP: có

#### Item Stage 6: Lọc scene và chuẩn hóa thành danh sách clip usable

- Input: raw scene list
- Output: usable scene list
- Bắt buộc cho MVP: có

#### Item Stage 7: Tạo shuffle plan có kiểm soát

- Input: usable scene list
- Output: ordered scene plan
- Bắt buộc cho MVP: có

#### Item Stage 8: Cắt clip audio/video theo edit plan

- Input: processed master và ordered scene plan
- Output: danh sách clip A/V
- Bắt buộc cho MVP: có

#### Item Stage 9: Ghép rough cut theo thứ tự scene mới

- Input: clip A/V list
- Output: rough final cut
- Bắt buộc cho MVP: có

#### Item Stage 10: Hoàn thiện audio ở cấp final cut

- Input: rough final cut audio
- Output: final audio
- Bắt buộc cho MVP: có

#### Item Stage 11: Áp product overlay lên final cut

- Input: rough final cut video, final audio, product image, overlay spec
- Output: composed final video
- Bắt buộc cho MVP: có

#### Item Stage 12: Export artifact của item

- Input: composed final video, final audio, item metadata
- Output: `final_video.mp4`, `final_audio.m4a`, `job_metadata.json`, `process_log.txt`
- Bắt buộc cho MVP: có

### Stage nào nên tách riêng thành module

Ngoài các media module cũ, MVP mới bắt buộc phải có thêm:

- Session validator
- Session queue runner
- Session summary writer
- UI state adapter hoặc UI controller

Media modules vẫn giữ tách biệt như trước:

- Validation item
- Downloader
- Media normalizer
- Speed processor
- Scene detector
- Scene planner
- Audio finisher
- Overlay composer
- Artifact exporter

### Nơi nào dễ gây lệch thời lượng, lỗi đồng bộ audio/video, lỗi render

Các điểm media rủi ro vẫn giữ nguyên:

- Bước chuẩn hóa nếu không đưa video về working media ổn định.
- Bước tăng tốc nếu video và audio không cùng timing base.
- Bước cắt scene nếu scene list dùng thời gian khác với processed master.
- Bước nối clip nếu có transition không kiểm soát.
- Bước final audio nếu normalize sai stage.

Ngoài ra, với mô hình session mới còn có rủi ro mới:

- Queue runner có thể dừng giữa chừng nếu không tách rõ lỗi item và lỗi session.
- UI có thể hiện trạng thái sai nếu orchestrator không phát event nhất quán.

### Stage nào là bắt buộc cho MVP

Cả lớp session và lớp item đều là bắt buộc cho MVP mới. UI danh sách và session runner không còn là phần tùy chọn nữa, mà là điều kiện để sản phẩm đúng với yêu cầu đã chốt lại.

## 3. Quyết định thiết kế chính

- Chọn `session pipeline + item pipeline` thay vì nhồi mọi thứ vào một flow duy nhất.
- Chọn `xử lý tuần tự theo queue` cho MVP.
- Giữ media pipeline theo item gần như nguyên trạng để hạn chế rủi ro kỹ thuật.
- Tách `session failure` và `item failure` để UI trình bày đúng.
- Áp overlay và audio finishing ở cấp item sau khi rough cut đã chốt.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu session runner gọi media pipeline mà không đóng gói rõ contract item result, UI rất khó hiển thị đúng trạng thái.
- Nếu session summary sinh quá muộn hoặc thiếu dữ liệu, người dùng khó biết phiên chạy đã hoàn tất như thế nào.
- Nếu build UI trước khi session runner ổn định, team dễ phải sửa lại state flow nhiều lần.
- Nếu giữ tư duy cũ kiểu `một process = một job`, code orchestration sẽ nhanh chóng lệch source of truth.

## 5. Tiêu chí hoàn thành

- File này phải mô tả được hai lớp pipeline: session và item.
- Phải chỉ rõ item pipeline nào giữ nguyên và phần nào mới xuất hiện do yêu cầu UI/list.
- Phải chốt session runner xử lý tuần tự cho MVP.
- Team đọc file này phải có thể triển khai đúng ranh giới giữa UI/session orchestration và media processing.

