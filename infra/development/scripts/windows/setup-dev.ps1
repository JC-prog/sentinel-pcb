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

# Fill in a JWT_SECRET_KEY if it's still blank - the app refuses to issue usable tokens without
# one, and there's no reason to make every developer do this by hand.
if (Select-String -Path ".env" -Pattern '^JWT_SECRET_KEY=$' -Quiet) {
    Write-Host "==> Generating JWT_SECRET_KEY in .env"
    $secret = (uv run python -c "import secrets; print(secrets.token_hex(32))").Trim()
    (Get-Content ".env") -replace '^JWT_SECRET_KEY=$', "JWT_SECRET_KEY=$secret" | Set-Content ".env"
}

if (Select-String -Path ".env" -Pattern '^LITELLM_OPENAI_API_KEY=$' -Quiet) {
    Write-Host "    Note: put your own OpenAI key in LITELLM_OPENAI_API_KEY in .env for the OpenAI"
    Write-Host "    features - only your local LiteLLM proxy container sees it. Ollama-only? Leave"
    Write-Host "    it blank. Using a shared team proxy instead? See infra/litellm/README.md."
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

Write-Host "==> Starting local Postgres + Qdrant + LiteLLM proxy (docker compose up -d --wait)"
docker compose -f infra/development/docker-compose.yml up -d --wait db qdrant litellm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Checking for Ollama (http://localhost:11434) - optional"
try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:11434" -TimeoutSec 2 | Out-Null
    Write-Host "    Found - the Local LLM option in Settings will work."
} catch {
    Write-Host "    Not found. Only needed for the Local LLM option: https://ollama.com"
    Write-Host "    The OpenAI path (via the LiteLLM proxy) does not need it."
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
Write-Host ""
Write-Host "Optional docker-compose profiles (only run your slice):"
Write-Host "  --profile ui         the containerized UI"
Write-Host "  --profile inference  the ONNX inference service (needs inference/models.toml filled)"
Write-Host "  --profile full       everything"
