$ErrorActionPreference = "Stop"

$mutex = New-Object System.Threading.Mutex($false, "Global\AutoTikTokEditorTelegramBots")
$hasMutex = $mutex.WaitOne(0, $false)
if (-not $hasMutex) {
    Write-Host "Telegram bot launcher dang chay o mot cua so/process khac. Hay tat process cu truoc khi chay lai."
    exit 1
}

$projectRoot = $PSScriptRoot
$venvActivate = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
$tokenFile = Join-Path $projectRoot "telegram_bot_token.txt"
$botsFile = Join-Path $projectRoot "telegram_bots.json"
$srcPath = Join-Path $projectRoot "src"

if (-not (Test-Path $venvActivate)) {
    Write-Error "Khong tim thay file kich hoat venv: $venvActivate"
}

. $venvActivate
Set-Location $projectRoot

if (-not $env:AUTO_EDITOR_TELEGRAM_BOT_TOKEN -and (Test-Path $tokenFile)) {
    $env:AUTO_EDITOR_TELEGRAM_BOT_TOKEN = (Get-Content $tokenFile -Raw).Trim()
}

if ((-not (Test-Path $botsFile)) -and (-not $env:AUTO_EDITOR_TELEGRAM_BOT_TOKEN)) {
    Write-Host "Chua co token Telegram."
    Write-Host "Cach 1: tao file telegram_bots.json trong thu muc code de chay nhieu bot."
    Write-Host "Cach 2: tao file telegram_bot_token.txt trong thu muc code va dan token vao do de chay 1 bot."
    Write-Host "Cach 3: chay PowerShell voi bien moi truong AUTO_EDITOR_TELEGRAM_BOT_TOKEN."
    exit 1
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = $srcPath
}

$env:AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM = "1"
$env:AUTO_EDITOR_ALLOW_SOURCE_RUNTIME = "1"

try {
    if (Test-Path $botsFile) {
        Write-Host "Dang chay tat ca Telegram bot trong telegram_bots.json..."
        python -m auto_tiktok_editor.cli telegram-bots --bots-file $botsFile
    }
    else {
        Write-Host "Dang chay Telegram bot local/dev khong can dang nhap..."
        python -m auto_tiktok_editor.cli telegram-bot
    }
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex() | Out-Null
    }
    $mutex.Dispose()
}
