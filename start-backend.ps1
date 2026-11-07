# Starts the NeuroSteno backend on http://127.0.0.1:8000 (serves frontend/dist if built).
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path ".venv")) {
    py -3.12 -m venv .venv
    .\.venv\Scripts\python -m pip install --upgrade pip
    .\.venv\Scripts\python -m pip install -r requirements.txt
}
.\.venv\Scripts\python -m uvicorn neurosteno.api:app --host 127.0.0.1 --port 8000
