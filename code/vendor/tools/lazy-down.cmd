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
"%_NODE_EXE%" "%SCRIPT_DIR%..\lazy-downloader\dist\cli.js" %*
