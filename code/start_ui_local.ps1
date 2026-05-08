$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$venvActivate = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
$tokenFile = Join-Path $projectRoot "telegram_bot_token.txt"
$srcPath = Join-Path $projectRoot "src"

if (-not (Test-Path $venvActivate)) {
    Write-Error "Khong tim thay file kich hoat venv: $venvActivate"
}

. $venvActivate
Set-Location $projectRoot

if (-not $env:AUTO_EDITOR_TELEGRAM_BOT_TOKEN -and (Test-Path $tokenFile)) {
    $env:AUTO_EDITOR_TELEGRAM_BOT_TOKEN = (Get-Content $tokenFile -Raw).Trim()
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = $srcPath
}

$env:AUTO_EDITOR_COMMERCIAL_MODE = "0"
$env:AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM = "1"
$env:AUTO_EDITOR_ALLOW_SOURCE_RUNTIME = "1"

Write-Host "Dang mo giao dien UI local/dev khong can dang nhap..."
python -m auto_tiktok_editor.cli ui
