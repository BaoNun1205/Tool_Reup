# License Setup

Tài liệu này mô tả cơ chế tài khoản và license cho Auto TikTok Editor.

## Tổng quan

- App vẫn render local trên máy khách.
- Mỗi khách phải đăng nhập bằng tài khoản do admin cấp.
- Tài khoản được quản lý bởi `license_server/`.
- License có:
  - ngày hết hạn
  - số máy tối đa
  - số phiên đồng thời
  - trạng thái khóa/mở

## Hành vi hiện tại của app

- Khi mở UI, app sẽ kiểm tra online với `license server`.
- Nếu tài khoản bị khóa, hết hạn hoặc phiên không hợp lệ, app quay về màn hình đăng nhập.
- Trong lúc chạy, app heartbeat lại server để phát hiện:
  - tài khoản hết hạn
  - tài khoản bị admin khóa
  - phiên bị thu hồi
  - vượt giới hạn share account

## Biến môi trường phía client

- `AUTO_EDITOR_LICENSE_SERVER_URL`
- `AUTO_EDITOR_LICENSE_CACHE_PATH`
- `AUTO_EDITOR_LICENSE_REQUEST_TIMEOUT_SECONDS`
- `AUTO_EDITOR_LICENSE_REFRESH_LEEWAY_SECONDS`
- `AUTO_EDITOR_LICENSE_HEARTBEAT_SECONDS`

## Biến môi trường phía server

- `AUTO_EDITOR_LICENSE_DATABASE_URL`
- `AUTO_EDITOR_LICENSE_ACCESS_TOKEN_TTL_MINUTES`
- `AUTO_EDITOR_LICENSE_REFRESH_TOKEN_TTL_HOURS`
- `AUTO_EDITOR_LICENSE_SESSION_STALE_MINUTES`
- `AUTO_EDITOR_LICENSE_SIGNING_SEED_B64`
- `AUTO_EDITOR_LICENSE_PUBLIC_BASE_URL`
- `AUTO_EDITOR_LICENSE_ADMIN_SESSION_SECRET`
- `AUTO_EDITOR_LICENSE_ADMIN_SESSION_COOKIE_NAME`
- `AUTO_EDITOR_LICENSE_ADMIN_SESSION_TTL_HOURS`

## Kiến trúc free được chốt

- `Render Free Web Service`: chạy `license_server`
- `UptimeRobot Free`: ping `GET /health` mỗi 5 phút
- `Supabase Free`: làm Postgres cho license data

Luồng chuẩn:

1. Tool khách gọi `license server` trên Render.
2. `license server` đọc/ghi dữ liệu trong Supabase Postgres.
3. `UptimeRobot` ping `/health` để giữ Render ấm và tạo traffic nhẹ tới DB.

## Khởi tạo server local nhanh

```bash
cd code/license_server
pip install -e .
python -m license_server.app.bootstrap init-db
python -m license_server.app.bootstrap create-user --username admin --password your-password --admin
python -m license_server.app.bootstrap create-user --username customer01 --password strong-password
python -m license_server.app.bootstrap issue-license --username customer01 --days 30 --plan standard --max-devices 1 --max-concurrent-sessions 1
uvicorn license_server.app.api:app --host 0.0.0.0 --port 8787
```

Sau khi server chạy, mở trình duyệt tại:

```text
http://127.0.0.1:8787/admin/login
```

## Chuyển sang Supabase Postgres

1. Tạo project Supabase.
2. Lấy connection string Postgres.
3. Set:

```text
AUTO_EDITOR_LICENSE_DATABASE_URL=postgresql+psycopg://...
```

Nếu bạn dán nhầm `postgres://...` hoặc `postgresql://...`, server sẽ tự chuẩn hóa sang `postgresql+psycopg://...`.

4. Chạy lại:

```bash
python -m license_server.app.bootstrap init-db
```

5. Tạo lại admin và user test nếu database mới còn trống.

## Deploy lên Render

Trong `code/license_server` đã có sẵn [render.yaml](/D:/Tool_Reup/code/license_server/render.yaml).

Biến môi trường tối thiểu trên Render:

- `AUTO_EDITOR_LICENSE_DATABASE_URL`
- `AUTO_EDITOR_LICENSE_PUBLIC_BASE_URL`
- `AUTO_EDITOR_LICENSE_SIGNING_SEED_B64`
- `AUTO_EDITOR_LICENSE_ADMIN_SESSION_SECRET`

`AUTO_EDITOR_LICENSE_SIGNING_SEED_B64` phải là giá trị cố định trong production. Không được để server tự sinh seed mỗi lần restart.

## UptimeRobot

Tạo monitor tới:

```text
https://<your-render-service>.onrender.com/health
```

Chu kỳ khuyên dùng:

- `5 phút`

## Admin API có sẵn

- `GET /api/v1/admin/users`
- `POST /api/v1/admin/users`
- `POST /api/v1/admin/users/{user_id}/status`
- `GET /api/v1/admin/licenses`
- `POST /api/v1/admin/licenses/issue`
- `POST /api/v1/admin/licenses/{license_id}/extend`
- `POST /api/v1/admin/licenses/{license_id}/status`
- `GET /api/v1/admin/users/{user_id}/devices`
- `POST /api/v1/admin/devices/{device_id}/revoke`
- `GET /api/v1/admin/users/{user_id}/sessions`
- `POST /api/v1/admin/sessions/{session_id}/revoke`

## Lưu ý vận hành

- Client không được đọc database trực tiếp.
- Chỉ `license server` mới nói chuyện với Supabase Postgres.
- Để bán thương mại, app khách phải trỏ về URL Render thật thay vì `127.0.0.1`.
