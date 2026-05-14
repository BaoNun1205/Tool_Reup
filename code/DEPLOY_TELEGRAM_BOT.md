# Deploy Telegram bot len Render Free

Flow hien tai chi chay Telegram bot va pipeline edit video. Ban free cua Render can co HTTP endpoint de UptimeRobot ping, nen service chay dang Web Service va bot Telegram long polling chay trong background thread.

## Render

File `render.yaml` o root repo tao mot Web Service Docker free va tro Docker context vao `./code`:

```yaml
type: web
plan: free
runtime: docker
dockerfilePath: ./code/Dockerfile
dockerContext: ./code
healthCheckPath: /health
```

Docker image cai san:

- Python package cua project
- `ffmpeg` / `ffprobe`
- `yt-dlp`
- `lazy-down`
- Playwright browser runtime cho `lazy-down`

Bien can dien tren Render:

```text
AUTO_EDITOR_TELEGRAM_BOT_TOKEN=123456:telegram-token
AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

`AUTO_EDITOR_TELEGRAM_ALLOWED_CHAT_IDS` nen la chat ID cua ban de khoa bot chi cho ban dung. Gui `/myid` cho bot khi chay local de lay ID.

Sau khi deploy, lay URL Render va tao monitor tren UptimeRobot ping moi 5 phut:

```text
https://ten-service.onrender.com/health
```

Mac dinh Render service trong repo dung:

```text
AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM=1
AUTO_EDITOR_OUTPUT_ROOT=/tmp/auto-tiktok-output
AUTO_EDITOR_TELEGRAM_CLEANUP_AFTER_JOB=1
AUTO_EDITOR_MAX_PARALLEL_SESSION_ITEMS=1
AUTO_EDITOR_TELEGRAM_AUTO_CLEANUP=1
AUTO_EDITOR_TELEGRAM_CLEANUP_MAX_AGE_SECONDS=3600
```

Output va file tam nam trong `/tmp`, khong dung persistent disk. Sau khi bot gui xong video qua Telegram, service se xoa thu muc session render va anh input cua job do. Neu job fail, auto cleanup van quet file cu de tranh day disk.

## Local nhanh

Windows:

```powershell
.\start_telegram_bot.bat
```

Hoac chay thang trong venv:

```powershell
python -m auto_tiktok_editor.cli telegram-bot
```

Local van doc token tu `code/telegram_bot_token.txt` neu khong co bien moi truong `AUTO_EDITOR_TELEGRAM_BOT_TOKEN`.

## Lenh Telegram

- Gui 1 link TikTok public va 1 anh san pham. Thu tu nao cung duoc.
- Bot se xu ly tung job va gui lai `video_final.mp4`.
- `/myid`: xem chat ID de cau hinh allowlist.
- `/reset`: xoa draft va hang doi cua chat hien tai.
- `/cleanup`: xoa media input/output khi khong co job dang chay.

## Ghi chu van hanh

Render Free Web Service van co the restart hoac bi gioi han CPU/RAM. UptimeRobot giup han che idle spin down, nhung khong dam bao chat luong nhu paid worker/VPS. Video render bang ffmpeg la tac vu nang, nen giu moi lan 1 job va gui video ngan neu dung free.
