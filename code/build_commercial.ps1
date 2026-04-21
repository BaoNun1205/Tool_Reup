param(
    [string]$PythonExe = "",
    [string]$OutputDir = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot

function Resolve-ProjectPath([string]$TargetPath) {
    $resolved = [System.IO.Path]::GetFullPath($TargetPath)
    $project = [System.IO.Path]::GetFullPath($projectRoot)
    if (-not $resolved.StartsWith($project, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch a path outside the project root: $resolved"
    }
    return $resolved
}

if (-not $PythonExe) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    }
    else {
        $PythonExe = "python"
    }
}

if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot "build\nuitka"
}

$outputRoot = Resolve-ProjectPath $OutputDir
$entryScript = Resolve-ProjectPath (Join-Path $projectRoot "src\auto_tiktok_editor\commercial_entry.py")
$cacheRoot = Resolve-ProjectPath (Join-Path $projectRoot "build\nuitka-cache")
$toolsSourceRoot = Join-Path $projectRoot "build\commercial_tools"

if (-not (Test-Path $PythonExe)) {
    throw "Khong tim thay Python executable: $PythonExe"
}

if (-not (Test-Path $entryScript)) {
    throw "Khong tim thay commercial entry script: $entryScript"
}

if (-not $SkipInstall) {
    Write-Host "Dang cai build dependencies cho Nuitka..."
    & $PythonExe -m pip install -e "$projectRoot[build]"
}

if (Test-Path $outputRoot) {
    Remove-Item -LiteralPath $outputRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $outputRoot | Out-Null
if (-not (Test-Path $cacheRoot)) {
    New-Item -ItemType Directory -Path $cacheRoot | Out-Null
}

function Resolve-ExistingTool([string]$ConfiguredPath, [string]$FallbackCommand) {
    if ($ConfiguredPath -and (Test-Path $ConfiguredPath)) {
        return (Resolve-Path $ConfiguredPath).Path
    }
    $command = Get-Command $FallbackCommand -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path $command.Source)) {
        return $command.Source
    }
    return $null
}

if (Test-Path $toolsSourceRoot) {
    Remove-Item -LiteralPath $toolsSourceRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $toolsSourceRoot | Out-Null

$ffmpegSource = Resolve-ExistingTool $env:AUTO_EDITOR_FFMPEG_BIN "ffmpeg.exe"
$ffprobeSource = Resolve-ExistingTool $env:AUTO_EDITOR_FFPROBE_BIN "ffprobe.exe"
$ytDlpSource = Resolve-ExistingTool $env:AUTO_EDITOR_YTDLP_BIN "yt-dlp.exe"
$nodeExe = "C:\Program Files\nodejs\node.exe"
$lazyDownModule = Join-Path $projectRoot "vendor\lazy-downloader"
$lazyDownWrapper = Join-Path $projectRoot "vendor\tools\lazy-down.cmd"
$playwrightBrowsers = Join-Path $env:LOCALAPPDATA "ms-playwright"

if (-not $ffmpegSource) {
    throw "Khong tim thay ffmpeg.exe de dong goi."
}
if (-not $ffprobeSource) {
    throw "Khong tim thay ffprobe.exe de dong goi."
}
if (-not $ytDlpSource) {
    throw "Khong tim thay yt-dlp.exe de dong goi."
}
if (-not (Test-Path $nodeExe)) {
    throw "Khong tim thay node.exe de dong goi lazy-down."
}
if (-not (Test-Path $lazyDownModule)) {
    throw "Khong tim thay package lazy-downloader de dong goi."
}
if (-not (Test-Path $lazyDownWrapper)) {
    throw "Khong tim thay lazy-down.cmd trong vendor\tools."
}
if (-not (Test-Path $playwrightBrowsers)) {
    throw "Khong tim thay ms-playwright runtime de dong goi lazy-down."
}

Copy-Item -LiteralPath $ffmpegSource -Destination (Join-Path $toolsSourceRoot "ffmpeg.exe")
Copy-Item -LiteralPath $ffprobeSource -Destination (Join-Path $toolsSourceRoot "ffprobe.exe")
Copy-Item -LiteralPath $ytDlpSource -Destination (Join-Path $toolsSourceRoot "yt-dlp.exe")
Copy-Item -LiteralPath $nodeExe -Destination (Join-Path $toolsSourceRoot "node.exe")
Copy-Item -LiteralPath $lazyDownModule -Destination (Join-Path $toolsSourceRoot "lazy-downloader") -Recurse -Force
Copy-Item -LiteralPath $playwrightBrowsers -Destination (Join-Path $toolsSourceRoot "ms-playwright") -Recurse -Force
Copy-Item -LiteralPath $lazyDownWrapper -Destination (Join-Path $toolsSourceRoot "lazy-down.cmd")

$env:AUTO_EDITOR_ALLOW_SOURCE_RUNTIME = "1"
$env:NUITKA_CACHE_DIR = $cacheRoot
try {
    Write-Host "Dang build ban thuong mai bang Nuitka..."
    & $PythonExe -m nuitka `
        --standalone `
        --assume-yes-for-downloads `
        --enable-plugin=tk-inter `
        --windows-console-mode=disable `
        --nofollow-import-to=tests `
        --output-dir="$outputRoot" `
        --output-filename="AutoTikTokEditorCommercial.exe" `
        --product-name="Auto TikTok Editor Commercial" `
        --file-version="1.0.0.0" `
        --product-version="1.0.0.0" `
        --company-name="Auto TikTok Editor" `
        $entryScript
}
finally {
    Remove-Item Env:AUTO_EDITOR_ALLOW_SOURCE_RUNTIME -ErrorAction SilentlyContinue
    Remove-Item Env:NUITKA_CACHE_DIR -ErrorAction SilentlyContinue
}

$distDir = Join-Path $outputRoot "commercial_entry.dist"
if (Test-Path (Join-Path $distDir "AutoTikTokEditorCommercial.exe")) {
    $distToolsDir = Join-Path $distDir "tools"
    if (Test-Path $distToolsDir) {
        Remove-Item -LiteralPath $distToolsDir -Recurse -Force
    }
    Copy-Item -LiteralPath $toolsSourceRoot -Destination $distToolsDir -Recurse -Force
    Write-Host ""
    Write-Host "Build xong."
    Write-Host "Thu muc phat hanh: $distDir"
}
else {
    throw "Nuitka khong tao ra thu muc dist nhu mong doi."
}
