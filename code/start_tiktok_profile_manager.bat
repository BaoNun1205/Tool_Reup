@echo off
setlocal

start "TikTok Profile Manager" /D "%~dp0" powershell.exe -ExecutionPolicy Bypass -File "%~dp0start_tiktok_profile_manager.ps1"

endlocal & exit /b 0
