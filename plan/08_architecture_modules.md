# Architecture Modules

## 1. Mục tiêu file

Tài liệu này cập nhật kiến trúc theo yêu cầu mới: hệ thống không chỉ có media pipeline mà còn phải có UI chỉnh chu và session orchestration cho danh sách nhiều item. File này là nguồn sự thật để chia module đúng ranh giới trách nhiệm.

## 2. Nội dung chính

### Danh sách module cần có

Các module chính cho MVP mới:

- `UI Shell`
- `Session List Manager`
- `Session Validator`
- `Session Orchestrator`
- `Session Summary Writer`
- `Item Input Validator`
- `Source Downloader`
- `Media Probe & Normalizer`
- `Speed Processor`
- `Scene Detector`
- `Scene Qualifier`
- `Edit Planner`
- `Overlay Planner`
- `Rough Cut Renderer`
- `Audio Finisher`
- `Final Compositor`
- `Artifact Exporter`
- `Metadata & Logging`

### Trách nhiệm của từng module

#### `UI Shell`

- Cung cấp giao diện local chỉnh chu cho người dùng.
- Hiển thị danh sách item, trạng thái session và trạng thái item.
- Gửi session input xuống backend orchestrator.

#### `Session List Manager`

- Quản lý danh sách item trong UI hoặc tầng ứng dụng.
- Hỗ trợ thêm dòng, xóa dòng, sửa dòng.
- Giữ thứ tự item theo đúng thứ tự người dùng nhập.

#### `Session Validator`

- Kiểm tra toàn bộ session trước khi chạy.
- Chặn start nếu còn dòng invalid hoặc vượt hard cap.
- Trả lỗi gắn theo item để UI highlight đúng dòng.

#### `Session Orchestrator`

- Tạo queue item từ session input.
- Xử lý tuần tự từng item.
- Tách rõ lỗi session-level và lỗi item-level.
- Phát trạng thái cho UI theo từng bước chính.

#### `Session Summary Writer`

- Ghi summary cuối phiên.
- Tổng hợp số item thành công, thất bại và đường dẫn output.

#### `Item Input Validator`

- Validate URL và ảnh ở cấp item.
- Chuẩn hóa dữ liệu item trước khi vào media pipeline.

#### `Source Downloader`

- Tải video TikTok public về local working area theo từng item.

#### `Media Probe & Normalizer`

- Probe media và chuẩn hóa source thành working media ổn định.

#### `Speed Processor`

- Áp tốc độ 1.2x cho A/V.

#### `Scene Detector`

- Detect scene trên processed master.

#### `Scene Qualifier`

- Lọc, merge, split scene thành usable units.

#### `Edit Planner`

- Sinh constrained shuffle plan theo từng item.

#### `Overlay Planner`

- Tính vị trí, scale, mode overlay từ ảnh sản phẩm.

#### `Rough Cut Renderer`

- Cắt clip A/V và ghép rough cut.

#### `Audio Finisher`

- Hoàn thiện loudness và limiter ở cấp final cut.

#### `Final Compositor`

- Ghép rough cut video, final audio và product overlay.

#### `Artifact Exporter`

- Ghi artifact theo từng item.
- Đảm bảo naming và cấu trúc output nhất quán.

#### `Metadata & Logging`

- Ghi log và warning ở cả cấp session lẫn cấp item.

### Module nào phụ thuộc module nào

Quan hệ phụ thuộc chính:

- `UI Shell` phụ thuộc `Session List Manager`, `Session Validator`, `Session Orchestrator`.
- `Session List Manager` phụ thuộc data contract của session và item.
- `Session Validator` phụ thuộc `Item Input Validator`.
- `Session Orchestrator` phụ thuộc toàn bộ item pipeline modules.
- `Session Summary Writer` phụ thuộc `Session Orchestrator` và `Metadata & Logging`.
- `Source Downloader` phụ thuộc item đã qua validation.
- `Media Probe & Normalizer` phụ thuộc `Source Downloader`.
- `Speed Processor` phụ thuộc `Media Probe & Normalizer`.
- `Scene Detector` phụ thuộc `Speed Processor`.
- `Scene Qualifier` phụ thuộc `Scene Detector`.
- `Edit Planner` phụ thuộc `Scene Qualifier`.
- `Overlay Planner` phụ thuộc item image info và canvas metadata.
- `Rough Cut Renderer` phụ thuộc `Speed Processor` và `Edit Planner`.
- `Audio Finisher` phụ thuộc `Rough Cut Renderer`.
- `Final Compositor` phụ thuộc `Rough Cut Renderer`, `Audio Finisher`, `Overlay Planner`.
- `Artifact Exporter` phụ thuộc `Final Compositor` và `Metadata & Logging`.

### Module nào nên build trước

Thứ tự build module tối ưu cho MVP:

1. `Item Input Validator`
2. `Source Downloader`
3. `Media Probe & Normalizer`
4. `Speed Processor`
5. `Scene Detector`
6. `Scene Qualifier`
7. `Edit Planner`
8. `Rough Cut Renderer`
9. `Overlay Planner`
10. `Audio Finisher`
11. `Final Compositor`
12. `Artifact Exporter`
13. `Session Validator`
14. `Session Orchestrator`
15. `Session Summary Writer`
16. `UI Shell`
17. `Metadata & Logging` hoàn thiện theo flow cuối

Lý do là phải chứng minh item media core chạy được trước, rồi mới bọc bằng session orchestration và UI.

### Dữ liệu trao đổi giữa các module

Các contract dữ liệu nên có:

- `SessionSpec`
- `SessionItemSpec`
- `ValidatedSession`
- `ValidatedItem`
- `SourceAsset`
- `WorkingMedia`
- `ProcessedMaster`
- `RawSceneList`
- `UsableSceneList`
- `EditPlan`
- `OverlaySpec`
- `RoughCutAsset`
- `FinalAudioAsset`
- `ItemResult`
- `SessionSummary`

### Cấu trúc logic của hệ thống

Kiến trúc logic của MVP nên là `UI-driven session orchestrator + deterministic item pipeline`. UI không được nhúng logic media nặng. Media pipeline không được biết quá nhiều về UI. Session orchestration là lớp trung gian điều phối item pipeline và phát trạng thái cho UI.

### Cách chia nhỏ để sau này code dễ

Nguyên tắc chia nhỏ:

- Tách `session layer` và `item media layer` thành hai cụm rõ ràng.
- UI chỉ gọi orchestrator, không gọi thẳng media modules.
- Mỗi item phải có result object riêng để session tổng hợp dễ.
- Logic thuần như validation và scene planning phải dễ test độc lập.

### Ưu tiên kiến trúc đơn giản nhưng mở rộng được

MVP không cần distributed queue hay web-scale backend. Một session orchestrator chạy cục bộ và xử lý tuần tự là đủ. Tuy nhiên contract giữa UI, session và item phải đủ rõ để sau này có thể thêm retry failed item, import list hoặc parallel workers mà không viết lại toàn bộ hệ thống.

## 3. Quyết định thiết kế chính

- Chọn `UI Shell + Session Orchestrator + Item Pipeline` làm xương sống kiến trúc.
- Chọn `media modules không phụ thuộc UI`.
- Chọn `session-level contracts` thay vì chỉ có per-job contract.
- Chọn `sequential queue runner` cho MVP để giữ kiến trúc đơn giản nhưng đúng sản phẩm.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu UI biết quá nhiều về state nội bộ của media pipeline, code sẽ khó bảo trì.
- Nếu session summary và item artifact không tách rõ, UI rất khó hiển thị kết quả đúng.
- Nếu không khóa contract session ngay từ đầu, phần frontend và backend sẽ dễ lệch nhau.
- Nếu build UI quá sớm khi orchestrator chưa rõ state model, dễ phải sửa lại nhiều lần.

## 5. Tiêu chí hoàn thành

- File này phải đưa thêm UI và session orchestration vào danh sách module bắt buộc.
- Phải mô tả rõ phụ thuộc giữa UI, session layer và item media layer.
- Phải chỉ rõ contract dữ liệu mới ở cấp session.
- Team đọc file này phải có thể chia việc rõ giữa frontend/UI, session orchestration và media processing.
