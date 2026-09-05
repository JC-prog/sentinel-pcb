# SentinelChat dev environment setup (Windows). Idempotent - safe to re-run after pulling changes.
#
# Usage: powershell -File infra\development\scripts\windows\setup-dev.ps1

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..")
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
} else {
    Write-Host "==> .env already exists, leaving it alone"
}

Write-Host "==> Checking Docker (needed for the local Postgres container)"
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

Write-Host "==> Starting local Postgres + Qdrant (docker compose up -d --wait db qdrant)"
docker compose -f infra/development/docker-compose.yml up -d --wait db qdrant
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Checking for Ollama (http://localhost:11434)"
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:11434" -TimeoutSec 2 | Out-Null
    Write-Host "    Found - the Local LLM option in Settings will work."
} catch {
    Write-Host "    Not found. Install it to use the Local LLM option: https://ollama.com"
    Write-Host "    (not required if you'll only use the OpenAI option)"
}

Write-Host "==> UI: installing Node dependencies (npm install)"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: npm not found. Install Node.js: https://nodejs.org/"
    exit 1
}
Push-Location ui
try {
    npm install
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

Write-Host "==> UI checks (unit tests)"
Push-Location ui
try {
    npx ng test --watch=false
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Setup complete. Postgres + Qdrant are running in Docker (docker compose -f infra/development/docker-compose.yml down to stop them)."
Write-Host "To run the app:"
Write-Host "  terminal 1: uv run uvicorn app.main:app --reload   # http://localhost:8000"
Write-Host "  terminal 2: cd ui && npm start                     # http://localhost:4200"
