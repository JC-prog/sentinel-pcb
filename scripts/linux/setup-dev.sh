#!/usr/bin/env bash
# SentinelPCB dev environment setup (Linux). Idempotent - safe to re-run after pulling changes.
#
# Usage: bash scripts/linux/setup-dev.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> Backend: installing Python dependencies (uv sync)"
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "       (or https://docs.astral.sh/uv/getting-started/installation/)"
  exit 1
fi
uv sync

if [ ! -f .env ]; then
  echo "==> Creating .env from .env.example"
  cp .env.example .env
  echo "    Fill in ANTHROPIC_API_KEY / DATABASE_URL / etc. before running anything that needs them."
else
  echo "==> .env already exists, leaving it alone"
fi

echo "==> Checking for the ADC model (models/pcb_feature_detector.onnx)"
if [ ! -f models/pcb_feature_detector.onnx ]; then
  echo "    NOT FOUND - Multi-Modal Inference (and anything that calls it) will fail until this"
  echo "    exists. It's gitignored (80MB binary) - see models/README.md to generate it via"
  echo "    ../pcb-inspect-ai/scripts/export_onnx.py. Everything else in this script still works"
  echo "    without it."
else
  echo "    Found."
fi

echo "==> UI: installing Node dependencies (npm install)"
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install Node.js via your package manager (apt/dnf/pacman) or nvm:"
  echo "       https://nodejs.org/"
  exit 1
fi
# --legacy-peer-deps works around an npm arborist bug (Cannot read properties of null
# (reading 'edgesOut')) seen with this dependency graph on npm 10.x - see ui/README.md.
(cd ui && npm install --legacy-peer-deps)

echo "==> Backend checks (ruff, mypy, pytest)"
uv run ruff check .
uv run mypy .
uv run pytest -q

echo ""
echo "Setup complete. To run the app:"
echo "  terminal 1: uv run uvicorn app.main:app --reload         # http://localhost:8000"
echo "  terminal 2: cd ui && npm start                           # http://localhost:4200"
echo "  optional:   uv run python simulation/simulate_line.py    # simulated AOI camera"
