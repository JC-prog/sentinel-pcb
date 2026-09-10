# SentinelPCB production build (Windows): backend + UI Docker images.
#
# Usage: powershell -File scripts\windows\build-prod.ps1 [image-tag]   (default tag: latest)

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RootDir

$Tag = if ($args.Count -ge 1) { $args[0] } else { "latest" }

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

if (-not (Test-Path "models\pcb_feature_detector.onnx")) {
    Write-Host "ERROR: models\pcb_feature_detector.onnx is missing."
    Write-Host "       The backend image would still build, but Multi-Modal Inference (and everything"
    Write-Host "       downstream of it) would fail at request time - see models\README.md."
    exit 1
}

Write-Host "==> Backend checks (must pass before a production build)"
uv run ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run mypy .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Backend: building Docker image (infra/Dockerfile)"
docker build -f infra/Dockerfile -t "sentinel-pcb-backend:$Tag" .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> UI: unit tests + Docker image (ui/Dockerfile)"
Push-Location ui
try {
    npx ng test --watch=false
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
docker build -f ui/Dockerfile -t "sentinel-pcb-ui:$Tag" ui
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Build complete:"
Write-Host "  sentinel-pcb-backend:$Tag  (run: docker run -p 8000:8000 --env-file .env sentinel-pcb-backend:$Tag)"
Write-Host "  sentinel-pcb-ui:$Tag       (run: docker run -p 4200:80 sentinel-pcb-ui:$Tag)"
Write-Host ""
Write-Host "Or run both together (plus ollama): docker-compose up --build"
