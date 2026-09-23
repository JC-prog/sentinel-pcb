#!/usr/bin/env bash
# Start the whole local dev stack in one terminal: db/qdrant/litellm (Docker, idempotent), then
# the backend, the ONNX inference service, and the UI dev server - all three natively with live
# reload, their output interleaved and prefixed below so you can tell them apart.
#
# Run infra/development/scripts/unix/setup-dev.sh first (once, or after pulling dependency
# changes) - this script only starts things, it doesn't install anything.
#
# Usage: bash infra/development/scripts/unix/start-dev.sh
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT_DIR"

for cmd in uv npm docker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd not found. Run infra/development/scripts/unix/setup-dev.sh first."
    exit 1
  fi
done
if [ ! -f .env ]; then
  echo "ERROR: .env not found. Run infra/development/scripts/unix/setup-dev.sh first."
  exit 1
fi

echo "==> Docker: db + qdrant + litellm (idempotent)"
docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait db qdrant litellm

# Point the backend at the inference service we're about to start below, if .env doesn't already
# say otherwise (missing key, or present but left blank) - the same append-a-sane-default pattern
# setup-dev.sh uses for JWT_SECRET_KEY. Done before starting the backend so its first read of
# .env already has it.
if ! grep -qE '^INFERENCE_BASE_URL=.+' .env 2>/dev/null; then
  if grep -qE '^INFERENCE_BASE_URL=' .env; then
    tmp="$(mktemp)"
    sed "s|^INFERENCE_BASE_URL=$|INFERENCE_BASE_URL=http://localhost:8001|" .env > "$tmp" && mv "$tmp" .env
  else
    echo "INFERENCE_BASE_URL=http://localhost:8001" >> .env
  fi
  echo "==> Set INFERENCE_BASE_URL=http://localhost:8001 in .env (was blank/missing)"
fi

if [ ! -d inference/.venv ]; then
  echo "==> Inference: first run - installing dependencies (uv sync)"
  (cd inference && uv sync)
fi

PIDS=()
cleanup() {
  echo ""
  echo "==> Stopping backend/inference/UI. Docker services (db/qdrant/litellm) keep running -"
  echo "    stop them with: docker compose -f infra/development/docker-compose.yml --env-file .env down"
  for pid in "${PIDS[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  wait >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Backend    - http://localhost:8000"
(uv run uvicorn app.main:app --reload 2>&1 | awk '{ print "[backend]   " $0; fflush(); }') &
PIDS+=("$!")

# REQUIRE_MODELS_ON_STARTUP=false: boots without inference/models.toml's repos filled in yet -
# same default the "inference" Docker Compose profile uses. Fill them in for real classifications.
echo "==> Inference  - http://localhost:8001 (REQUIRE_MODELS_ON_STARTUP=false)"
(
  cd inference &&
  REQUIRE_MODELS_ON_STARTUP=false uv run uvicorn inference_service.main:app --app-dir src --port 8001 --reload \
    2>&1 | awk '{ print "[inference] " $0; fflush(); }'
) &
PIDS+=("$!")

echo "==> UI         - http://localhost:4200"
(cd ui && npm start 2>&1 | awk '{ print "[ui]        " $0; fflush(); }') &
PIDS+=("$!")

echo ""
echo "All three are starting - watch below for each one's own \"ready\"/\"compiled successfully\" line."
echo "Ctrl+C stops backend/inference/UI; Docker services (db/qdrant/litellm) keep running."
echo ""

wait
