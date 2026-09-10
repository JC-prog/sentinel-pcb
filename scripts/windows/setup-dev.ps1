# SentinelPCB dev environment setup (Windows). Idempotent - safe to re-run after pulling changes.
#
# Usage: powershell -File scripts\windows\setup-dev.ps1

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RootDir

Write-Host "==> Backend: installing Python dependencies (uv sync)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: uv not found. Install it: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    Write-Host "       (or https://docs.astral.sh/uv/getting-started/installation/)"
    exit 1
}
uv sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path ".env")) {
    Write-Host "==> Creating .env from .env.example"
    Copy-Item ".env.example" ".env"
    Write-Host "    Fill in ANTHROPIC_API_KEY / DATABASE_URL / etc. before running anything that needs them."
} else {
    Write-Host "==> .env already exists, leaving it alone"
}

Write-Host "==> Checking Docker (needed for the local Postgres/pgvector container)"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: docker not found. Install Docker Desktop for Windows: https://docs.docker.com/desktop/install/windows-install/"
    exit 1
}
docker info > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker daemon is not running."
    Write-Host "       Start Docker Desktop and wait for it to finish starting, then retry."
    exit 1
}

Write-Host "==> Starting local Postgres/pgvector (docker compose up -d --wait db)"
docker compose up -d --wait db
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Checking for Ollama (http://localhost:11434)"
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:11434" -TimeoutSec 2 | Out-Null
    Write-Host "    Found - local reasoning fallback is available."
} catch {
    Write-Host "    Not found. Install it if you'll be iterating on a reasoning agent: https://ollama.com"
    Write-Host "    (not required to run the rest of the app)"
}

Write-Host "==> Checking for the ADC model (models\pcb_feature_detector.onnx)"
if (-not (Test-Path "models\pcb_feature_detector.onnx")) {
    Write-Host "    NOT FOUND - Multi-Modal Inference (and anything that calls it) will fail until this"
    Write-Host "    exists. It's gitignored (80MB binary) - see models\README.md to generate it via"
    Write-Host "    ..\pcb-inspect-ai\scripts\export_onnx.py. Everything else in this script still works"
    Write-Host "    without it."
} else {
    Write-Host "    Found."
}

Write-Host "==> UI: installing Node dependencies (npm install)"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: npm not found. Install Node.js: https://nodejs.org/"
    exit 1
}
# --legacy-peer-deps works around an npm arborist bug (Cannot read properties of null
# (reading 'edgesOut')) seen with this dependency graph on npm 10.x - see ui/README.md.
Push-Location ui
try {
    npm install --legacy-peer-deps
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host "==> Backend checks (ruff, mypy, pytest)"
uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run mypy .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Setup complete. Postgres/pgvector is running in Docker (docker compose down to stop it)."
Write-Host "To run the app:"
Write-Host "  terminal 1: uv run uvicorn app.main:app --reload         # http://localhost:8000"
Write-Host "  terminal 2: cd ui && npm start                           # http://localhost:4200"
Write-Host "  optional:   uv run python simulation/simulate_line.py    # simulated AOI camera"
