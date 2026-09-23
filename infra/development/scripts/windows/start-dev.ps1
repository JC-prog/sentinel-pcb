# Start the whole local dev stack: db/qdrant/litellm (Docker, idempotent) in this window, then
# the backend, the ONNX inference service, and the UI dev server each in their own new window -
# all three natively with live reload.
#
# Run infra\development\scripts\windows\setup-dev.ps1 first (once, or after pulling dependency
# changes) - this script only starts things, it doesn't install anything.
#
# Usage: powershell -File infra\development\scripts\windows\start-dev.ps1

# See setup-dev.ps1's own comment on this - native commands' harmless stderr output shouldn't
# turn into a script-terminating exception; $LASTEXITCODE is checked explicitly instead.
$ErrorActionPreference = "Continue"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..") -ErrorAction Stop
Set-Location $RootDir

foreach ($cmd in @("uv", "npm", "docker")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: $cmd not found. Run infra\development\scripts\windows\setup-dev.ps1 first."
        exit 1
    }
}
if (-not (Test-Path ".env")) {
    Write-Host "ERROR: .env not found. Run infra\development\scripts\windows\setup-dev.ps1 first."
    exit 1
}

Write-Host "==> Docker: db + qdrant + litellm (idempotent)"
docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait db qdrant litellm
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Point the backend at the inference service we're about to start below, if .env doesn't already
# say otherwise (missing key, or present but left blank) - the same append-a-sane-default pattern
# setup-dev.ps1 uses for JWT_SECRET_KEY. Done before starting the backend so its first read of
# .env already has it.
$hasValue = Select-String -Path ".env" -Pattern '^INFERENCE_BASE_URL=.+' -Quiet -ErrorAction SilentlyContinue
if (-not $hasValue) {
    $hasBlankKey = Select-String -Path ".env" -Pattern '^INFERENCE_BASE_URL=$' -Quiet -ErrorAction SilentlyContinue
    if ($hasBlankKey) {
        (Get-Content ".env") -replace '^INFERENCE_BASE_URL=$', 'INFERENCE_BASE_URL=http://localhost:8001' |
            Set-Content ".env"
    } else {
        Add-Content ".env" "INFERENCE_BASE_URL=http://localhost:8001"
    }
    Write-Host "==> Set INFERENCE_BASE_URL=http://localhost:8001 in .env (was blank/missing)"
}

if (-not (Test-Path "inference\.venv")) {
    Write-Host "==> Inference: first run - installing dependencies (uv sync)"
    Push-Location inference
    try {
        uv sync
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

Write-Host "==> Opening backend, inference, and UI each in their own window"

# REQUIRE_MODELS_ON_STARTUP=false: boots without inference/models.toml's repos filled in yet -
# same default the "inference" Docker Compose profile uses. Fill them in for real classifications.
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$RootDir'; Write-Host 'Backend - http://localhost:8000'; uv run uvicorn app.main:app --reload"
) -WindowStyle Normal

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$RootDir\inference'; Write-Host 'Inference - http://localhost:8001 (REQUIRE_MODELS_ON_STARTUP=false)'; `$env:REQUIRE_MODELS_ON_STARTUP = 'false'; uv run uvicorn inference_service.main:app --app-dir src --port 8001 --reload"
) -WindowStyle Normal

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$RootDir\ui'; Write-Host 'UI - http://localhost:4200'; npm start"
) -WindowStyle Normal

Write-Host ""
Write-Host "Backend, inference, and UI are starting, each in its own window - Ctrl+C (or close the"
Write-Host "window) to stop that one. Docker services (db/qdrant/litellm) keep running in the"
Write-Host "background - stop them with:"
Write-Host "  docker compose -f infra/development/docker-compose.yml --env-file .env down"
