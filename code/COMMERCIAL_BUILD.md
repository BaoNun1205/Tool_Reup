# Commercial Build

Ban phat hanh thuong mai duoc khoa theo cac nguyen tac sau:

- Bat buoc dang nhap tai khoan do admin cap.
- License cache luu local duoc ma hoa bang Windows DPAPI.
- Offline grace period mac dinh la `48 gio`.
- Mac dinh van hanh theo chinh sach `1 tai khoan = 1 may = 1 phien`.
- Telegram local tren may khach da bi tat.
- Entry point thuong mai yeu cau chay tu file `.exe` dong goi, khong phai source code.

## Build

Chay ngay tai thu muc `code`:

```powershell
.\build_commercial.ps1
```

Hoac:

```bat
build_commercial.bat
```

Script se:

- cai `Nuitka` va build dependencies vao `.venv`
- build entry point `auto_tiktok_editor.commercial_entry`
- tao thu muc phat hanh o `build\nuitka\commercial_entry.dist`

## Runtime can co

- `AUTO_EDITOR_LICENSE_SERVER_URL`
  - vi du: `http://127.0.0.1:8787`
- Cac binary can thiet nhu `ffmpeg`, `ffprobe`, `yt-dlp`, `lazy-down`, `adb`
  - co the dat trong `.venv\Scripts`, PATH, hoac override bang env vars hien co

## Luu y van hanh

- Khong ship `telegram_bot_token.txt` cho khach.
- Co the ship file `telegram_client_settings.json` rong de khach tu dien `bot_token` va `delivery_chat_id`; app se tu nap lai moi lan mo.
- Khong ship source code cho khach.
- Khi phat hanh, nen dong goi ca thu muc `commercial_entry.dist` thanh `.zip` hoac installer rieng.
- Neu can build tu source de test truoc khi dong goi, script build tu dong bat `AUTO_EDITOR_ALLOW_SOURCE_RUNTIME=1` trong luc build.
