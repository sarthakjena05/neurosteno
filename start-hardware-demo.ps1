# NeuroSteno: one-command hardware demo launcher (g.tec Unicorn Hybrid Black, Windows).
#
#   .\start-hardware-demo.ps1                      # check + build + start, open the browser
#   .\start-hardware-demo.ps1 -Serial UN-2024.09.36
#   .\start-hardware-demo.ps1 -SkipCheck           # skip the 5 s acquisition check
#   .\start-hardware-demo.ps1 -UnicornPyDir "D:\...\Unicorn Python\Lib"
#
# What it does:
#   1. Python 3.12 venv in backend\.venv (created once) + requirements.
#   2. Finds UnicornPy (UNICORN_PY_DIR, else the Unicorn Suite default folders).
#   3. Builds the frontend once (frontend\dist) so ONE server serves UI + API on :8000.
#   4. Runs a 5 s acquisition check with the existing scripts\unicorn_hardware_check.py.
#   5. Starts the backend and opens http://127.0.0.1:8000.
# Nothing here changes patient data. Simulated mode keeps working without the headset or UnicornPy.
param(
    [string]$Serial = "UN-2024.09.36",
    [string]$UnicornPyDir = "",
    [switch]$SkipCheck,
    [switch]$Rebuild,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

Step "Python environment"
Set-Location "$root\backend"
if (-not (Test-Path ".venv")) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv .venv } else { python -m venv .venv }
    .\.venv\Scripts\python -m pip install --upgrade pip
}
.\.venv\Scripts\python -m pip install -q -r requirements.txt
$pyver = (.\.venv\Scripts\python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ($pyver -ne "3.12") { Write-Host "Warning: venv Python is $pyver; UnicornPy needs CPython 3.12." -ForegroundColor Yellow }

Step "UnicornPy"
$candidates = @($UnicornPyDir, $env:UNICORN_PY_DIR,
    "$env:USERPROFILE\Documents\gtec\Unicorn Suite\Hybrid Black\Unicorn Python\Lib",
    "$env:PUBLIC\Documents\gtec\Unicorn Suite\Hybrid Black\Unicorn Python\Lib") | Where-Object { $_ }
$found = $candidates | Where-Object { Test-Path (Join-Path $_ "UnicornPy*.pyd") } | Select-Object -First 1
if ($found) {
    $env:UNICORN_PY_DIR = $found
    Write-Host "UnicornPy: $found" -ForegroundColor Green
} else {
    Write-Host "UnicornPy not found. LIVE/ASSISTED headset modes will be unavailable; SIMULATED mode still works." -ForegroundColor Yellow
    Write-Host "Fix: pass -UnicornPyDir '<folder containing UnicornPy.pyd>'" -ForegroundColor Yellow
}

Step "Frontend build"
if ($Rebuild -or -not (Test-Path "$root\frontend\dist\index.html")) {
    Set-Location "$root\frontend"
    if (-not (Test-Path "node_modules")) { npm ci --no-audit --no-fund }
    npm run build
    Set-Location "$root\backend"
} else { Write-Host "Using existing frontend\dist (pass -Rebuild after pulling new frontend code)" }

if ($found -and -not $SkipCheck) {
    Step "5 s acquisition check on $Serial (close Unicorn Recorder / other g.tec apps first)"
    .\.venv\Scripts\python scripts\unicorn_hardware_check.py --serial $Serial --seconds 5
    if ($LASTEXITCODE -ne 0) { Write-Host "Acquisition check reported a problem (see above). Continuing; you can still use ASSISTED dry run / SIMULATED." -ForegroundColor Yellow }
}

Step "Starting NeuroSteno on http://127.0.0.1:8000 (Ctrl+C to stop)"
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port 8000 is already in use. Stop the other backend first." -ForegroundColor Red; exit 1
}
if (-not $NoBrowser) { Start-Job -ScriptBlock { Start-Sleep -Seconds 6; Start-Process "http://127.0.0.1:8000" } | Out-Null }
.\.venv\Scripts\python -m uvicorn neurosteno.api:app --host 127.0.0.1 --port 8000
