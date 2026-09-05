#!/usr/bin/env bash
# SentinelChat dev environment setup (macOS/Linux). Idempotent - safe to re-run after pulling
# changes.
#
# Usage: bash infra/development/scripts/unix/setup-dev.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT_DIR"

IS_MACOS=false
if [ "$(uname -s)" = "Darwin" ]; then
  IS_MACOS=true
fi

echo "==> Backend: installing Python dependencies (uv sync)"
if ! command -v uv >/dev/null 2>&1; then
  if [ "$IS_MACOS" = true ]; then
    echo "ERROR: uv not found. Install it: brew install uv"
  else
    echo "ERROR: uv not found. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
  fi
  echo "       (or https://docs.astral.sh/uv/getting-started/installation/)"
  exit 1
fi
uv sync

if [ ! -f .env ]; then
  echo "==> Creating .env from .env.example"
  cp .env.example .env
else
  echo "==> .env already exists, leaving it alone"
fi

echo "==> Checking Docker (needed for the local Postgres container)"
if ! command -v docker >/dev/null 2>&1; then
  if [ "$IS_MACOS" = true ]; then
    echo "ERROR: docker not found. Install Docker Desktop for Mac: https://docs.docker.com/desktop/install/mac-install/"
  else
    echo "ERROR: docker not found. Install Docker Engine: https://docs.docker.com/engine/install/"
  fi
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  if [ "$IS_MACOS" = true ]; then
    echo "ERROR: Docker daemon is not running."
    echo "       Start Docker Desktop (open -a Docker) and wait for it to finish starting, then retry."
  else
    echo "ERROR: Docker daemon is not running or the current user lacks permission to reach it."
    echo "       Try: sudo systemctl start docker    (or add your user to the 'docker' group and re-login)"
  fi
  exit 1
fi

echo "==> Starting local Postgres (docker compose up -d --wait db)"
docker compose -f infra/development/docker-compose.yml up -d --wait db

echo "==> Checking for Ollama (http://localhost:11434)"
if curl -sf http://localhost:11434 -o /dev/null 2>&1; then
  echo "    Found - the Local LLM option in Settings will work."
else
  echo "    Not found. Install it to use the Local LLM option: https://ollama.com"
  echo "    (not required if you'll only use the OpenAI option)"
fi

echo "==> UI: installing Node dependencies (npm install)"
if ! command -v npm >/dev/null 2>&1; then
  if [ "$IS_MACOS" = true ]; then
    echo "ERROR: npm not found. Install Node.js: brew install node"
    echo "       (or https://nodejs.org/)"
  else
    echo "ERROR: npm not found. Install Node.js via your package manager (apt/dnf/pacman) or nvm:"
    echo "       https://nodejs.org/"
  fi
  exit 1
fi
(cd ui && npm install)

echo "==> Backend checks (ruff, mypy, pytest)"
uv run ruff check .
uv run mypy .
uv run pytest -q

echo "==> UI checks (unit tests)"
(cd ui && npx ng test --watch=false)

echo ""
echo "Setup complete. Postgres is running in Docker (docker compose -f infra/development/docker-compose.yml down to stop it)."
echo "To run the app:"
echo "  terminal 1: uv run uvicorn app.main:app --reload   # http://localhost:8000"
echo "  terminal 2: cd ui && npm start                     # http://localhost:4200"
