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
    Write-Host ""
    Write-Host "Build xong."
    Write-Host "Thu muc phat hanh: $distDir"
}
else {
    throw "Nuitka khong tao ra thu muc dist nhu mong doi."
}
