# AI Port Hub - one-shot dev/prod start (Windows PowerShell)
# Usage:  ./scripts/start.ps1            (build frontend + run backend serving it)
#         ./scripts/start.ps1 -Dev       (run backend + vite dev server separately)
param([switch]$Dev)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# --- Backend venv + deps ---
Push-Location "$root/backend"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".venv/Scripts/python.exe" -m pip install -q --upgrade pip
& ".venv/Scripts/python.exe" -m pip install -q -r requirements.txt
Pop-Location

if ($Dev) {
    Write-Host "Starting backend (:8000) and vite dev (:5173)..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit","-Command","cd '$root/backend'; ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000"
    Push-Location "$root/frontend"
    if (-not (Test-Path "node_modules")) { npm install }
    npm run dev
    Pop-Location
} else {
    Write-Host "Building frontend..." -ForegroundColor Cyan
    Push-Location "$root/frontend"
    if (-not (Test-Path "node_modules")) { npm install }
    npm run build
    Pop-Location
    Write-Host "Starting backend on http://localhost:8000 ..." -ForegroundColor Green
    Push-Location "$root/backend"
    & ".venv/Scripts/python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
    Pop-Location
}
