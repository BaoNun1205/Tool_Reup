@echo off
setlocal

start "Auto TikTok Telegram Bot" /D "%~dp0" powershell.exe -ExecutionPolicy Bypass -File "%~dp0start_telegram_bot.ps1"
timeout /t 2 /nobreak >nul
start "TikTok Profile Manager" /D "%~dp0" powershell.exe -ExecutionPolicy Bypass -File "%~dp0start_tiktok_profile_manager.ps1"

endlocal & exit /b 0
