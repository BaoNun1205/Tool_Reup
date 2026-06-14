# TikTok Profile Manager

This project now keeps only the TikTok Profile Manager surface.

Kept features:

- Manage TikTok accounts in SQLite.
- Create and reuse separate persistent Chrome profile folders per account.
- Open TikTok Studio with Playwright.
- Track account status, queued videos, captions, hashtags, product IDs, publish mode, schedule time, and logs.
- Auto post or prepare selected videos through the profile browser automation.
- Map profile names to Telegram bot/chat configs in `telegram_bots.json`.
- Send a selected profile video to the matching Telegram chat.
- Start, pause, resume, and stop the Telegram bot from Profile Manager settings.
- Process Telegram jobs through the retained editor pipeline and queue completed videos into the matching profile.

Run on Windows:

```powershell
cd code
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
```

Start the app:

```powershell
.\start_tiktok_profile_manager.bat
```

The Telegram worker is normally controlled from the Profile Manager. Its launcher remains available at:

```powershell
.\start_telegram_bot.bat
```

Or:

```powershell
python -m auto_tiktok_editor.cli profile-manager
```

Tests:

```powershell
python -m unittest discover -s tests -v
```
