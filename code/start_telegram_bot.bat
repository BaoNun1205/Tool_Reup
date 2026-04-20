@echo off
setlocal

powershell -ExecutionPolicy Bypass -File "%~dp0start_telegram_bot.ps1"
set "exit_code=%ERRORLEVEL%"

if not "%exit_code%"=="0" (
    echo.
    echo Telegram local launcher da dung voi ma loi %exit_code%.
    pause
)

endlocal & exit /b %exit_code%
