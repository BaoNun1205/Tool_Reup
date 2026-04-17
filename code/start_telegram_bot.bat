@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_telegram_bot.ps1"
echo.
pause

endlocal
