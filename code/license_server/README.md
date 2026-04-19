# License Server

Backend quản lý tài khoản, license, device binding và phiên đăng nhập cho Auto TikTok Editor.

## Mục tiêu

- App desktop vẫn chạy local trên máy khách.
- Mọi tài khoản phải được admin cấp và có ngày hết hạn.
- Server kiểm soát:
  - `expires_at`
  - số máy được phép dùng
  - số phiên đồng thời
  - khóa/mở tài khoản
  - heartbeat để phát hiện share account

## Chạy local cho dev

1. Cài dependency:

```bash
pip install -e .
```

2. Khởi tạo database:

```bash
python -m license_server.app.bootstrap init-db
```

3. Tạo admin:

```bash
python -m license_server.app.bootstrap create-user --username admin --password your-password --admin
```

4. Cấp user và license:

```bash
python -m license_server.app.bootstrap create-user --username customer01 --password strong-password
python -m license_server.app.bootstrap issue-license --username customer01 --days 30 --plan standard --max-devices 1 --max-concurrent-sessions 1
```

5. Chạy server:

```bash
uvicorn license_server.app.api:app --host 0.0.0.0 --port 8787 --reload
```

## Kiến trúc free được chốt

- `Render Free Web Service`: chạy `license_server`
- `UptimeRobot Free`: ping `GET /health` mỗi 5 phút
- `Supabase Free`: lưu Postgres cho `users / licenses / devices / login_sessions / audit_logs`

`/health` sẽ chạm database bằng `SELECT 1`, nên vừa kiểm tra service còn sống vừa tạo activity tới DB phía sau.

## Biến môi trường quan trọng

- `AUTO_EDITOR_LICENSE_DATABASE_URL`
- `AUTO_EDITOR_LICENSE_ACCESS_TOKEN_TTL_MINUTES`
- `AUTO_EDITOR_LICENSE_REFRESH_TOKEN_TTL_HOURS`
- `AUTO_EDITOR_LICENSE_SESSION_STALE_MINUTES`
- `AUTO_EDITOR_LICENSE_SIGNING_SEED_B64`
- `AUTO_EDITOR_LICENSE_PUBLIC_BASE_URL`
- `AUTO_EDITOR_LICENSE_ADMIN_SESSION_SECRET`

## Lưu ý khi dùng Supabase Postgres

- Dùng connection string dạng `postgresql+psycopg://...`
- Nếu bạn dán chuỗi `postgres://...` hoặc `postgresql://...`, server giờ sẽ tự chuẩn hóa sang driver `psycopg`
- Không cho app khách đọc database trực tiếp; app chỉ gọi API server

## Lưu ý production

- Trên host free như Render, bắt buộc set `AUTO_EDITOR_LICENSE_SIGNING_SEED_B64` cố định
- Không dựa vào file seed local cho production
- Nếu không set `AUTO_EDITOR_LICENSE_SIGNING_SEED_B64`, token có thể đổi khóa sau restart
