@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%node.exe" (
    set "_NODE_EXE=%SCRIPT_DIR%node.exe"
) else (
    set "_NODE_EXE=node"
)
if exist "%SCRIPT_DIR%ms-playwright" (
    set "PLAYWRIGHT_BROWSERS_PATH=%SCRIPT_DIR%ms-playwright"
)
set "_LAZY_DOWN_ENTRY=%SCRIPT_DIR%lazy-downloader\dist\cli.js"
if not exist "%_LAZY_DOWN_ENTRY%" (
    set "_LAZY_DOWN_ENTRY=%SCRIPT_DIR%..\lazy-downloader\dist\cli.js"
)
if not exist "%_LAZY_DOWN_ENTRY%" (
    echo lazy-down wrapper could not find dist\cli.js next to this script. 1>&2
    exit /b 1
)
"%_NODE_EXE%" "%_LAZY_DOWN_ENTRY%" %*
