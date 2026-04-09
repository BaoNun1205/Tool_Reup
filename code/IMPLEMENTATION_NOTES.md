# Implementation Notes

## Mapping từ plan sang code

- `plan/10_mvp_definition.md` và `plan/01_goal_and_scope.md`: dùng để khóa lại scope mới, nên project đã được đổi từ single-job CLI sang `session-based desktop UI` với nhiều dòng input.
- `plan/02_input_output_spec.md`: map sang `SessionSpec`, `SessionItemSpec`, `SessionResult`, `session_summary.json`, output theo từng item bên trong thư mục session.
- `plan/03_user_flow.md`: map sang UI local trong `ui/app.py` với các hành vi `thêm dòng`, `xóa dòng`, `chạy session`, per-item status và session summary.
- `plan/04_editing_pipeline.md`: media pipeline item-level vẫn giữ theo thứ tự `validate -> download -> normalize -> speed -> detect scenes -> qualify -> plan -> rough cut -> audio -> overlay -> export` bên trong `ItemPipelineRunner`.
- `plan/05_scene_shuffle_logic.md`: map sang `SceneQualifier` và `EditPlanner` trong `domain/planner.py`.
- `plan/06_product_overlay_logic.md`: map sang `OverlayPlanner` và filter graph trong `media/render.py`.
- `plan/07_audio_processing_logic.md`: map sang `AudioFinisher` với `volumedetect`, `loudnorm`, limiter và fallback silent audio.
- `plan/08_architecture_modules.md`: map sang package split `ui`, `app`, `domain`, `media`, `utils`; thêm `SessionOrchestrator` ở tầng orchestration và `ItemPipelineRunner` ở tầng item execution.
- `plan/09_edge_cases_and_risks.md`: map sang pre-run session validation, per-item runtime failure isolation, summary/log rõ ràng cho mixed-success session.
- `plan/12_build_order.md`: bám theo hướng media core giữ nguyên, rồi thêm session orchestration và UI ở lớp trên.

## Các giả định phải tự chốt khi code

- UI desktop dùng `Tkinter/ttk` vì đây là lựa chọn đơn giản và ổn định nhất cho MVP local, không kéo thêm web stack hoặc desktop framework nặng.
- `run-session` headless từ JSON manifest được giữ như một utility nhỏ để smoke test/backend debug; đây không phải luồng chính của sản phẩm.
- Ảnh sản phẩm dưới 400px cạnh dài bị hard fail; từ 400px đến dưới 800px chỉ warning. Plan chỉ nêu 800px là mức khuyến nghị nên mình chốt ngưỡng này để vừa practical vừa không loại quá nhiều input.
- Session output được nhóm theo `session_id/items/item_00x_row_id` thay vì chỉ theo `job_id`, để UI và summary map dễ hơn với từng dòng input.
- Nếu validation fail ở bất kỳ dòng nào, session không start; thay vào đó UI đánh dấu các dòng invalid và chờ người dùng sửa.
- Nếu một item fail trong runtime, session vẫn tiếp tục item sau; final session status sẽ là `completed_with_partial_failure`.
- Session hard cap giữ ở `20 item / session` theo plan.
- UI không có cancel/pause/resume ở MVP để tránh làm phức tạp state machine và cleanup logic.
- `cookies.txt` được hỗ trợ ở cấp session như một override download strategy cho các link TikTok khó tải; file này phải tồn tại trước khi start session.

## Điểm deliberately deferred sau MVP

- Xử lý song song nhiều item.
- Pause/resume hoặc retry item trực tiếp từ UI.
- Preview timeline hoặc live preview video.
- Auto background removal cho JPG.
- Subject-aware overlay placement.
- Semantic scene ranking hoặc scene quality hiểu nội dung.
- Batch queue, worker pool hoặc cloud render.
- Tự export cookies từ browser khi DPAPI/browser-cookie extraction fail.

## Điểm cần cải thiện sau MVP

- UI có thể được polish thêm bằng filter/sort item, bulk import từ file, retry failed item và preview artifact nhanh.
- `SessionOrchestrator` hiện đủ rõ cho MVP nhưng có thể tách event bus/observer riêng nếu sau này cần nhiều client hoặc remote control.
- Session summary hiện tập trung vào trạng thái và đường dẫn output; về sau có thể thêm duration xử lý, thống kê warning theo nhóm và metrics cho từng stage.
- Scene detection nên được benchmark với nhiều video TikTok thật để tinh chỉnh threshold.
- Overlay visual có thể được nâng cấp bằng một bước image compositing riêng nếu muốn panel đẹp hơn cho JPG fallback.
- Audio finishing có thể nâng thành 2-pass loudnorm nếu cần loudness ổn định hơn ở quy mô lớn.
- Downloader TikTok hiện đã có fallback `cookies.txt`, nhưng nếu muốn tỷ lệ tải thành công cao hơn nữa thì nên thêm hướng dẫn export cookie rõ hơn hoặc importer hỗ trợ nhiều browser.
