$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$venvActivate = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
$srcPath = Join-Path $projectRoot "src"
$localTemp = Join-Path $projectRoot "tmp"

if (-not (Test-Path $venvActivate)) {
    Write-Error "Khong tim thay file kich hoat venv: $venvActivate"
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

Write-Host "Dang mo TikTok Profile Manager..."
python -m auto_tiktok_editor.cli profile-manager
