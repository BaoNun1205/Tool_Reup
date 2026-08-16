$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$version = "4.1.0"
$buildStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$buildRoot = Join-Path $projectRoot "build\profile-manager-v$version-$buildStamp"
$distDir = Join-Path $buildRoot "cli.dist"
$cacheDir = Join-Path $projectRoot "tmp\nuitka-cache"
$backupDir = Join-Path $projectRoot ("data_backup_before_rebuild_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "src\auto_tiktok_editor\cli.py"
$iconPath = Join-Path $projectRoot "assets\app_icon.ico"

if (-not (Test-Path $pythonExe)) {
    throw "Khong tim thay Python venv: $pythonExe"
}

if (-not (Test-Path $entryPoint)) {
    throw "Khong tim thay entrypoint: $entryPoint"
}

New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
$env:NUITKA_CACHE_DIR = $cacheDir

$oldDistDir = Join-Path $projectRoot "build\profile-manager-v$version\cli.dist"
$preserveItems = @(
    "tiktok_profile_manager.sqlite3",
    "telegram_bots.json",
    "telegram_bot_token.txt",
    "profiles",
    "profile_video_queue",
    "logs"
)

if (Test-Path $oldDistDir) {
    foreach ($item in $preserveItems) {
        $source = Join-Path $oldDistDir $item
        if (Test-Path $source) {
            $target = Join-Path $backupDir $item
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
        }
    }
}

$jobs = [Math]::Max(1, [Environment]::ProcessorCount - 1)
Write-Host "Dang build vao thu muc moi: $buildRoot"
Write-Host "Qua trinh nay co the mat 10-30 phut. Dung bam Ctrl+C neu khong muon huy build."

& $pythonExe -m nuitka `
    --standalone `
    --assume-yes-for-downloads `
    --jobs=$jobs `
    --enable-plugin=tk-inter `
    --nofollow-import-to=backgroundremover `
    --nofollow-import-to=torch `
    --nofollow-import-to=torch.* `
    --nofollow-import-to=torchvision `
    --nofollow-import-to=torchvision.* `
    --nofollow-import-to=torchaudio `
    --nofollow-import-to=torchaudio.* `
    --nofollow-import-to=triton `
    --nofollow-import-to=triton.* `
    --include-package=uiautomator2 `
    --include-package=adbutils `
    --include-package=requests `
    --include-package=urllib3 `
    --include-package=retry `
    --include-package=lxml `
    --include-package-data=customtkinter `
    --include-data-dir=assets=assets `
    --include-data-dir=tools=tools `
    --windows-console-mode=disable `
    --windows-icon-from-ico=$iconPath `
    --product-name="TikTok Profile Manager" `
    --product-version=$version `
    --file-version="$version.0" `
    --output-dir=$buildRoot `
    --output-filename=TikTokProfileManager.exe `
    $entryPoint

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE. Ban build moi chua hoan tat."
}

foreach ($item in $preserveItems) {
    $backupSource = Join-Path $backupDir $item
    $projectSource = Join-Path $projectRoot $item
    $source = $null
    if (Test-Path $backupSource) {
        $source = $backupSource
    }
    elseif (Test-Path $projectSource) {
        $source = $projectSource
    }
    if ($source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $distDir $item) -Recurse -Force
    }
}

@'
$ErrorActionPreference = "Stop"

$mutex = New-Object System.Threading.Mutex($false, "Global\AutoTikTokEditorTelegramBots")
$hasMutex = $mutex.WaitOne(0, $false)
if (-not $hasMutex) {
    Write-Host "Telegram bot launcher dang chay o mot cua so/process khac. Hay tat process cu truoc khi chay lai."
    exit 1
}

$projectRoot = $PSScriptRoot
$exePath = Join-Path $projectRoot "TikTokProfileManager.exe"
$botsFile = Join-Path $projectRoot "telegram_bots.json"
$tokenFile = Join-Path $projectRoot "telegram_bot_token.txt"

if (-not (Test-Path $exePath)) {
    Write-Error "Khong tim thay TikTokProfileManager.exe: $exePath"
}

if (-not (Test-Path $botsFile) -and -not (Test-Path $tokenFile) -and -not $env:AUTO_EDITOR_TELEGRAM_BOT_TOKEN) {
    Write-Host "Chua co token Telegram."
    Write-Host "Hay dat telegram_bots.json hoac telegram_bot_token.txt trong thu muc app."
    exit 1
}

$env:AUTO_EDITOR_ALLOW_LOCAL_TELEGRAM = "1"
$env:AUTO_EDITOR_ALLOW_SOURCE_RUNTIME = "1"
if (-not $env:AUTO_EDITOR_PROJECT_ROOT) {
    $env:AUTO_EDITOR_PROJECT_ROOT = $projectRoot
}
if (-not $env:AUTO_EDITOR_TELEGRAM_INPUT_MODE) {
    $env:AUTO_EDITOR_TELEGRAM_INPUT_MODE = "simple"
}
if (-not $env:AUTO_EDITOR_TELEGRAM_URL_RESOLVE_TIMEOUT_SECONDS) {
    $env:AUTO_EDITOR_TELEGRAM_URL_RESOLVE_TIMEOUT_SECONDS = "3"
}

try {
    if (Test-Path $botsFile) {
        $arguments = @("telegram-bots", "--bots-file", $botsFile)
    }
    else {
        $arguments = @("telegram-bot")
    }
    $process = Start-Process -FilePath $exePath -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $process.WaitForExit()
    exit $process.ExitCode
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex() | Out-Null
    }
    $mutex.Dispose()
}
'@ | Set-Content -Path (Join-Path $distDir "start_telegram_bot.ps1") -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $projectRoot "start_telegram_bot.bat") -Destination (Join-Path $distDir "start_telegram_bot.bat") -Force

Write-Host ""
Write-Host "Build xong:"
Write-Host (Join-Path $distDir "TikTokProfileManager.exe")
Write-Host ""
Write-Host "Du lieu da duoc giu/copy vao ban build neu co trong project hoac dist cu."
