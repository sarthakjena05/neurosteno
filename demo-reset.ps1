# NeuroSteno demo isolation: archive SIMULATED (and replay) patient history so repeated demos and
# live A/B comparisons start from the seed profiles again.
#
# * Never deletes anything: files are MOVED / COPIED to backend\data\runtime_archive\<timestamp>\
# * Never touches real-origin data: history_*_real.json stays in place; metrics.jsonl keeps every
#   line whose origin is not "simulated" (a full copy of the original file is archived first).
# * Calibration files are left alone.
# * Refuses to run while the backend is running (it keeps history in memory and would write it back).
#
# Usage (backend STOPPED):   .\demo-reset.ps1            (archive)
#                            .\demo-reset.ps1 -WhatIf    (show what would happen, change nothing)
param([switch]$WhatIf)
$ErrorActionPreference = "Stop"

$rt = Join-Path $PSScriptRoot "backend\data\runtime"
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "The backend is running on port 8000. Stop it first (Ctrl+C in its terminal), then run this again." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $rt)) { Write-Host "No runtime folder yet: nothing to isolate."; exit 0 }

$files = @(Get-ChildItem $rt -File | Where-Object { $_.Name -like "history_*_simulated.json" -or $_.Name -like "history_*_replay.json" })
$metrics = Join-Path $rt "metrics.jsonl"
$simLines = 0; $keep = @()
if (Test-Path $metrics) {
    foreach ($line in Get-Content $metrics -Encoding UTF8) {
        if (-not $line.Trim()) { continue }
        $origin = $null
        try { $origin = ($line | ConvertFrom-Json).origin } catch { }
        if ($origin -eq "simulated") { $simLines++ } else { $keep += $line }   # unknown / real / replay-of-real lines are kept
    }
}

if ($files.Count -eq 0 -and $simLines -eq 0) { Write-Host "Already clean: no simulated history or simulated metrics found."; exit 0 }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$arch = Join-Path $PSScriptRoot "backend\data\runtime_archive\$stamp"
Write-Host "Archive folder: $arch"
foreach ($f in $files) { Write-Host "  move  $($f.Name)" }
if ($simLines -gt 0) { Write-Host "  copy  metrics.jsonl (full), then keep $($keep.Count) non-simulated line(s) in place, archive $simLines simulated line(s)" }
if ($WhatIf) { Write-Host "-WhatIf: nothing changed."; exit 0 }

New-Item -ItemType Directory -Force -Path $arch | Out-Null
foreach ($f in $files) { Move-Item -LiteralPath $f.FullName -Destination $arch }
if ($simLines -gt 0) {
    Copy-Item -LiteralPath $metrics -Destination (Join-Path $arch "metrics.jsonl")
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $text = if ($keep.Count) { ($keep -join "`n") + "`n" } else { "" }
    [System.IO.File]::WriteAllText($metrics, $text, $utf8)
}
Write-Host "Done. Simulated history archived; real-origin data untouched. Start the backend again with .\start-backend.ps1" -ForegroundColor Green
