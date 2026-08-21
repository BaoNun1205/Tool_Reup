$ErrorActionPreference = "Continue"

$projectRoot = $PSScriptRoot
$venvActivate = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
$srcPath = Join-Path $projectRoot "src"
$localTemp = Join-Path $projectRoot "tmp"

if (-not (Test-Path $venvActivate)) {
    Write-Host "[ERROR] Khong tim thay moi truong ao .venv tai: $venvActivate" -ForegroundColor Red
    Read-Host "Nhan Enter de thoat..."
    exit 1
}

. $venvActivate
Set-Location $projectRoot

New-Item -ItemType Directory -Force -Path $localTemp | Out-Null
$env:TEMP = $localTemp
$env:TMP = $localTemp

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$srcPath;$env:PYTHONPATH"
}
else {
    $env:PYTHONPATH = $srcPath
}

Write-Host "Kiem tra thu vien giao dien PySide6..." -ForegroundColor Yellow
$checkCmd = python -c "import PySide6, qfluentwidgets; print('OK')" 2>$null
if ($LASTEXITCODE -ne 0 -or $checkCmd -ne "OK") {
    Write-Host "Dang cai dat PySide6 va PySide6-Fluent-Widgets (co the mat 15-30 giay)..." -ForegroundColor Cyan
    python -m pip install "PySide6>=6.6,<7" "PySide6-Fluent-Widgets>=1.6,<2"
}

Write-Host "Dang khoi chay TikTok Profile Manager (PySide6 Fluent Design)..." -ForegroundColor Green
try {
    python -m auto_tiktok_editor.cli profile-manager
}
catch {
    Write-Host "[ERROR] Loi xay ra khi chay chuong trinh: $_" -ForegroundColor Red
    Read-Host "Nhan Enter de thoat..."
}

