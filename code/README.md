# Auto TikTok Editor MVP

Project này triển khai bản MVP theo `plan/` mới: một tool render riêng có giao diện local chỉnh chu, cho phép nhập một danh sách nhiều cặp `link TikTok public + ảnh sản phẩm`, xử lý tuần tự từng item, rồi sinh artifact theo item cùng summary cho cả session.

## MVP hiện có

- UI local bằng Tkinter/ttk, mở bằng command `auto-tiktok-editor`.
- Danh sách item trong UI với các thao tác:
  - nhập `link TikTok public`
  - chọn `ảnh sản phẩm` từ máy
  - `+ Thêm dòng`
  - `Xóa dòng`
- Trường `cookies.txt` tùy chọn ở cấp session để hỗ trợ tải các link TikTok bị chặn video stream khi download trực tiếp.
- Validation theo 2 lớp:
  - validation cho từng dòng
  - validation cho toàn session trước khi start
- Session orchestrator xử lý tuần tự từng item, không dừng cả session khi một item runtime fail.
- Trạng thái rõ cho session và cho từng item.
- Media pipeline đầy đủ cho từng item:
  - download source qua `yt-dlp`
  - probe media qua `ffprobe`
  - normalize về working media portrait `1080x1920`, `30 fps`, `48 kHz`
  - speed-up audio/video `1.2x`
  - scene detection bằng `ffmpeg`
  - scene qualification + constrained shuffle bằng Python
  - rough cut renderer theo clip A/V đã shuffle
  - audio finishing bằng `loudnorm` + limiter an toàn
  - product overlay cho PNG alpha và fallback panel cho JPG
- Output theo item và theo session:
  - `items/item_00x_*/final_video.mp4`
  - `items/item_00x_*/final_audio.m4a`
  - `items/item_00x_*/job_metadata.json`
  - `items/item_00x_*/process_log.txt`
  - `session_summary.json`
  - `session_log.txt`

## Cấu trúc project

- `src/auto_tiktok_editor/cli.py`: entry point. Mặc định mở UI, ngoài ra có `run-session` để chạy headless từ JSON manifest.
- `src/auto_tiktok_editor/ui/`: UI local, list input, per-item status, session summary, session-level cookies file picker.
- `src/auto_tiktok_editor/app/`: session orchestrator, item pipeline runner, workspace, artifact export, recorder.
- `src/auto_tiktok_editor/domain/`: models, validation, scene planner.
- `src/auto_tiktok_editor/media/`: download, probe, normalize, speed, scene detect, audio, render, overlay.
- `src/auto_tiktok_editor/utils/`: subprocess runner, image probe, time helpers.
- `tests/`: unit tests cho config, validation, planner và session orchestration smoke.

## Yêu cầu môi trường

Python:

- Python 3.11+ được khuyến nghị

Binary ngoài cần có trong `PATH` để chạy pipeline thật:

- `yt-dlp`
- `ffmpeg`
- `ffprobe`
- `realesrgan-ncnn-vulkan` optional, để crop 4:3 rồi làm nét ảnh sản phẩm trước khi overlay. Nếu thiếu binary này, app sẽ dùng ảnh đã crop 4:3 và ghi warning thay vì làm fail job.

Real-ESRGAN có thể cấu hình bằng biến môi trường:

- `AUTO_EDITOR_REALESRGAN_BIN`
- `AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE`
- `AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE_REQUIRED`
- `AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE_SCALE`
- `AUTO_EDITOR_PRODUCT_IMAGE_ENHANCE_MODEL`

UI dùng `tkinter`, là thư viện chuẩn của Python trên Windows thông thường.

## Cài đặt local

```bash
pip install -e .
```

## Chạy tool

Mở UI local:

```bash
auto-tiktok-editor
```

Hoặc explicit subcommand:

```bash
auto-tiktok-editor ui
```

## Khi nào nên dùng cookies.txt

Nếu app báo kiểu lỗi sau:

- `non-video artifacts only`
- `browser-cookie fallback failed`
- `Failed to decrypt with DPAPI`

thì nên export một file `cookies.txt` theo format Netscape và chọn file đó trong phần `Session Control` của UI.

Thứ tự ưu tiên download hiện tại là:

1. `cookies.txt` do người dùng chọn
2. download trực tiếp không cookie
3. fallback `--cookies-from-browser chrome`
4. fallback `--cookies-from-browser edge`

## Chạy headless từ JSON manifest

Lệnh này chủ yếu hữu ích cho smoke test backend hoặc automation nội bộ nhỏ:

```bash
auto-tiktok-editor run-session --session-file session.json
```

Ví dụ `session.json`:

```json
{
  "session_name": "demo-session",
  "output_root_dir": "D:/renders",
  "cookies_file": "D:/secrets/tiktok_cookies.txt",
  "items": [
    {
      "source_video_url": "https://www.tiktok.com/@user/video/1234567890",
      "product_image": "D:/assets/product_a.png"
    },
    {
      "source_video_url": "https://www.tiktok.com/@user/video/1234567891",
      "product_image": "D:/assets/product_b.jpg"
    }
  ]
}
```

## Output của một session

Thư mục output sẽ có dạng:

```text
output/
└─ session_YYYYMMDD_HHMMSS_xxxxxx/
   ├─ session_summary.json
   ├─ session_log.txt
   └─ items/
      ├─ item_001_row_001/
      │  ├─ final_video.mp4
      │  ├─ final_audio.m4a
      │  ├─ job_metadata.json
      │  └─ process_log.txt
      └─ item_002_row_002/
         ├─ final_video.mp4
         ├─ final_audio.m4a
         ├─ job_metadata.json
         └─ process_log.txt
```

## Chạy test

```bash
python -m unittest discover -s tests -v
```

## Deploy Telegram bot trên Render Free

Nếu chỉ dùng cá nhân qua Telegram, deploy Web Service free trên Render bằng `render.yaml` ở root repo. Service có endpoint `/health` cho UptimeRobot ping, còn bot Telegram chạy nền trong cùng process:

```bash
auto-tiktok-telegram-web
```

Video và ảnh tạm nằm trong `/tmp`; gửi xong qua Telegram thì job được xóa. Cấu hình token, allowlist chat ID và cleanup bằng biến môi trường. Xem chi tiết trong `DEPLOY_TELEGRAM_BOT.md`.

## Giới hạn hiện tại của MVP

- Session chạy tuần tự, chưa có xử lý song song.
- Chưa có pause/resume queue.
- Chưa có retry failed item trực tiếp từ UI.
- Chưa có timeline preview hoặc live preview video.
- Chưa có semantic scene ranking.
- Chưa có auto background removal cho JPG.
- Overlay placement vẫn là safe-zone cố định, chưa subject-aware.
- Một số link TikTok public vẫn cần `cookies.txt` để lộ video stream cho downloader.
