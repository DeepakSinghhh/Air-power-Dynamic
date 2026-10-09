# One-command demo on Windows (PowerShell): build the UI (if needed) and serve API + UI at http://127.0.0.1:8000
#   powershell -ExecutionPolicy Bypass -File run.ps1          (set $env:REBUILD=1 to rebuild the UI)
# Needs Python 3.11+ and Node.js 20+ on PATH. Works offline after the first install.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "frontend/dist") -or $env:REBUILD -eq "1") {
  Push-Location frontend
  npm install --no-fund --no-audit
  npm run build
  Pop-Location
}
python -c "import ortools, fastapi" 2>$null
if ($LASTEXITCODE -ne 0) { python -m pip install -e "engine[api]" }

$hostAddr = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$port = if ($env:PORT) { $env:PORT } else { "8000" }
Write-Host "VAYU-SARTHI: open http://${hostAddr}:${port}"
Set-Location engine
python -m uvicorn sarthi.api:app --host $hostAddr --port $port
