# Build Order

## 1. Mục tiêu file

Tài liệu này cập nhật thứ tự build theo nguồn sự thật mới: sản phẩm phải có UI chỉnh chu và workflow theo danh sách. Build order vì vậy phải vừa chứng minh media core chạy chắc, vừa đưa session orchestration và UI vào đúng thời điểm.

## 2. Nội dung chính

### Thứ tự build tối ưu

Thứ tự build đề xuất cho dự án:

1. Chốt data contract cho `SessionSpec`, `SessionItemSpec`, `ItemResult`, `SessionSummary`.
2. Build item validation.
3. Build downloader và source asset handling.
4. Build media probe và working media normalization.
5. Build speed processing 1.2x cho A/V.
6. Build rough render tối thiểu cho một item chưa shuffle và chưa overlay.
7. Build scene detection.
8. Build scene qualify logic gồm merge, split, drop.
9. Build constrained shuffle và edit plan.
10. Build rough cut renderer theo scene order mới.
11. Build overlay planner.
12. Build audio finisher ở cấp final cut.
13. Build final compositor và artifact exporter theo item.
14. Build session validator.
15. Build session orchestrator xử lý tuần tự theo danh sách.
16. Build session summary writer.
17. Build UI danh sách chỉnh chu.
18. Hardening edge cases và kiểm thử đại diện.

### Milestone logic

#### Milestone 1: Item media core chạy được

Đầu ra cần đạt:

- Chạy được pipeline core cho một item hợp lệ.
- Có working media, processed master và final output tối thiểu.

Mục tiêu là chứng minh media engine đúng trước khi bọc session và UI.

#### Milestone 2: Một item hoàn chỉnh đúng media behavior

Đầu ra cần đạt:

- Scene detect, scene qualify, shuffle, overlay và audio finishing đều hoạt động cho một item.
- Item artifact export đúng cấu trúc.

Milestone này khóa logic xử lý của từng dòng trong danh sách.

#### Milestone 3: Session runner chạy được theo danh sách

Đầu ra cần đạt:

- Nhận một session nhiều item.
- Validate session trước khi chạy.
- Xử lý tuần tự từng item.
- Ghi session summary.

Milestone này đưa sản phẩm từ `single-item engine` thành `session-based tool`.

#### Milestone 4: UI chỉnh chu vận hành được session

Đầu ra cần đạt:

- UI có danh sách item.
- Có nút thêm dòng, xóa dòng, chạy session.
- Có trạng thái rõ cho từng item và session.

Milestone này đưa sản phẩm tới đúng hình hài MVP mà người dùng mong đợi.

#### Milestone 5: Hardening và demo readiness

Đầu ra cần đạt:

- Chạy ổn trên các case demo đại diện.
- UI xử lý đúng cả success lẫn partial failure.
- Log và summary đủ cho support.

### Module nào phải test sớm

Các phần phải test sớm nhất:

- Downloader với nhiều kiểu URL public.
- Media normalization với source VFR và source không chuẩn.
- Speed processing để giữ A/V sync.
- Scene detection trên video có mật độ cut khác nhau.
- Session validator với các case nhiều dòng invalid.
- Session orchestrator với case một item fail nhưng item sau vẫn chạy.

### Dependency giữa các phần

Các dependency quan trọng:

- Không nên build UI hoàn chỉnh trước khi session orchestrator có state model rõ.
- Không nên build session runner trước khi item pipeline đã có contract output ổn định.
- Audio finisher vẫn phải phụ thuộc rough cut của từng item.
- Session summary phải phụ thuộc item results, không tự suy diễn từ log rời rạc.

### Chiến lược giảm rủi ro khi build

Chiến lược giảm rủi ro:

- Chứng minh item media core chạy được trước.
- Sau đó mới thêm session orchestration.
- Chỉ bọc bằng UI sau khi session state machine đã rõ.
- Test sớm mixed-success session, không chỉ happy path.
- Khóa contract session-level trước khi polish UI.

### Đường đi ngắn nhất để có bản chạy được đầu tiên

Đường ngắn nhất để có một bản chạy được đầu tiên vẫn là:

- Validation item
- Download source
- Normalize working media
- Speed process 1.2x
- Render ra một final tối thiểu cho một item

Tuy nhiên checkpoint này chỉ là nền kỹ thuật. Nó chưa phải MVP mới. Để đạt MVP, bắt buộc phải đi tiếp qua session runner và UI danh sách.

## 3. Quyết định thiết kế chính

- Chọn `build theo rủi ro kỹ thuật`, nhưng chấp nhận rằng UI giờ là phần bắt buộc của sản phẩm.
- Chọn `item pipeline ổn trước, session orchestration sau, UI sau nữa`.
- Chọn `mixed-success session` là case phải test sớm.
- Chọn `UI polish sau khi state model chắc`, không làm ngược lại.

## 4. Điểm khó / rủi ro / chỗ dễ fail khi build

- Nếu build UI quá sớm, team dễ phải viết lại khi session state đổi.
- Nếu chỉ bám vào core pipeline mà quên session runner, sản phẩm sẽ lại lệch source of truth.
- Nếu không test partial failure sớm, bản đầu sẽ chỉ đúng trên happy path.
- Nếu session contracts không ổn định, frontend và backend sẽ dễ lệch nhau.

## 5. Tiêu chí hoàn thành

- File này phải thể hiện rõ build order mới có UI và session workflow.
- Phải có milestone riêng cho session runner và UI.
- Phải nêu rõ phần nào cần test sớm do thay đổi scope.
- Builder AI đọc file này phải có thể triển khai đúng theo thứ tự mà không tiếp tục bám tư duy cũ kiểu one-job CLI.
