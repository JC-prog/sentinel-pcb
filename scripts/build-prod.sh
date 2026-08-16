#!/usr/bin/env bash
# SentinelPCB production build: backend + UI Docker images.
#
# Usage: bash scripts/build-prod.sh [image-tag]   (default tag: latest)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

TAG="${1:-latest}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker not found. Install Docker: https://docs.docker.com/get-docker/"
  exit 1
fi

if [ ! -f models/pcb_feature_detector.onnx ]; then
  echo "ERROR: models/pcb_feature_detector.onnx is missing."
  echo "       The backend image would still build, but Multi-Modal Inference (and everything"
  echo "       downstream of it) would fail at request time - see models/README.md."
  exit 1
fi

echo "==> Backend checks (must pass before a production build)"
uv run ruff check .
uv run mypy .
uv run pytest -q

echo "==> Backend: building Docker image (infra/Dockerfile)"
docker build -f infra/Dockerfile -t "sentinel-pcb-backend:${TAG}" .

echo "==> UI: unit tests + Docker image (ui/Dockerfile)"
(cd ui && npx ng test --watch=false)
docker build -f ui/Dockerfile -t "sentinel-pcb-ui:${TAG}" ui

echo ""
echo "Build complete:"
echo "  sentinel-pcb-backend:${TAG}  (run: docker run -p 8000:8000 --env-file .env sentinel-pcb-backend:${TAG})"
echo "  sentinel-pcb-ui:${TAG}       (run: docker run -p 4200:80 sentinel-pcb-ui:${TAG})"
echo ""
echo "Or run both together (plus ollama): docker-compose up --build"
