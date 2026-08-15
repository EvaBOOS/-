# Stable local API for VideoGen (Windows).
# One uvicorn process, no --reload (avoids orphan workers on Cyrillic paths).

$ErrorActionPreference = "Stop"
$BackendRoot = Split-Path -Parent $PSScriptRoot
Set-Location $BackendRoot

$VenvPython = Join-Path $BackendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Missing .venv. Create it: python -m venv .venv && .\.venv\Scripts\pip install -r requirements.txt"
}

$portBusy = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($portBusy) {
    Write-Host "Port 8000 is already in use (PID(s): $($portBusy.OwningProcess -join ', ')). Stop that process first." -ForegroundColor Yellow
    exit 1
}

Write-Host "Starting VideoGen API on http://127.0.0.1:8000 (no reload)..." -ForegroundColor Cyan
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
