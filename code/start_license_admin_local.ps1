param(
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$serverRoot = Join-Path $projectRoot "license_server"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$localDbPath = Join-Path $projectRoot "license_server.db"
$signingSeedPath = Join-Path $projectRoot ".auto_editor_license_signing_seed.b64"

if (-not (Test-Path $pythonExe)) {
    throw "Khong tim thay Python trong .venv: $pythonExe"
}

$env:AUTO_EDITOR_LICENSE_DATABASE_URL = "sqlite:///" + ($localDbPath -replace "\\", "/")
$env:AUTO_EDITOR_LICENSE_PUBLIC_BASE_URL = "http://127.0.0.1:$Port"
$env:AUTO_EDITOR_LICENSE_SIGNING_SEED_PATH = $signingSeedPath

Set-Location $serverRoot
& $pythonExe -m uvicorn license_server.app.api:app --host 127.0.0.1 --port $Port
