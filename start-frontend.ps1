# Starts the Vite dev server on http://localhost:5173 (proxies /api and /ws to :8000).
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\frontend"
if (-not (Test-Path "node_modules")) { npm install }
npm run dev
