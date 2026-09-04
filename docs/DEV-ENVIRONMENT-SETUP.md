# Dev Environment Setup

Step-by-step guide to getting SentinelPCB running locally, for anyone new to the repo. If you get
stuck, see [Troubleshooting](#troubleshooting) below. Once your environment is running, switch to
[`DEVELOPMENT.md`](../DEVELOPMENT.md) for how contribution actually works here (specs, branching,
Definition of Done).

## Prerequisites

Install these before running the setup script:

| Tool | Why | Install |
|---|---|---|
| **git** | clone the repo | usually preinstalled; otherwise [git-scm.com](https://git-scm.com/downloads) |
| **[uv](https://docs.astral.sh/uv/)** | Python dependency manager for the backend | macOS: `brew install uv`. Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh`. Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| **Docker** | runs the local Postgres/pgvector database (no native DB install needed) | macOS/Windows: [Docker Desktop](https://www.docker.com/products/docker-desktop/). Linux: [Docker Engine](https://docs.docker.com/engine/install/) |
| **Node.js + npm** | UI dependencies (Angular) | [nodejs.org](https://nodejs.org/) (LTS) |
| **[Ollama](https://ollama.com)** *(recommended, not required day one)* | local LLM fallback if you'll be iterating on a reasoning agent (Orchestrator, Dataset Preparation, Explainability & Review) | [ollama.com](https://ollama.com) — just install it, the setup script detects it automatically |
| **Anthropic API key** | needed for anything that calls the real Claude API | ask a teammate/lead if you don't have one |

You do **not** need to install Postgres natively, the setup script runs it in Docker for you.

## Step-by-step setup

1. **Clone the repo** and `cd` into it.

2. **Run the setup script for your OS**, from the repo root:

   ```bash
   bash scripts/macos/setup-dev.sh              # macOS
   bash scripts/linux/setup-dev.sh              # Linux
   powershell -File scripts\windows\setup-dev.ps1  # Windows
   ```

   This is idempotent, safe to re-run any time after pulling changes. It's covered step by step
   below in [What the script does](#what-the-script-does).

3. **Fill in `ANTHROPIC_API_KEY`** in the `.env` file the script created (from `.env.example`).
   Everything else in `.env` already has a working local default, including `DATABASE_URL`,
   which points at the Docker Postgres container the script just started
   (`localhost:5433`).

4. **Run the app**, in two terminals:

   ```bash
   uv run uvicorn app.main:app --reload   # http://localhost:8000
   cd ui && npm start                     # http://localhost:4200
   ```

5. Open `http://localhost:4200`, upload a PCB image, and run an inspection. Or drive it
   headlessly: `uv run python simulation/simulate_line.py --once`.

## What the script does

In order:

1. **`uv sync`** — installs the backend's Python dependencies (main + dev groups) into a local
   virtualenv.
2. **Creates `.env`** from `.env.example`, if it doesn't already exist (won't overwrite yours).
3. **Starts local Postgres/pgvector in Docker** (`docker compose up -d --wait db`) and waits for
   it to report healthy before continuing. This is the one external dependency the whole team
   needs, running it in Docker means nobody installs Postgres natively or fights version
   mismatches.
4. **Checks for Ollama** on `localhost:11434`. Informational only, doesn't fail if it's missing,
   you only need it when working on a reasoning agent.
5. **Checks for the ADC model** (`models/pcb_feature_detector.onnx`). It's gitignored (80MB
   binary) and not required to get started, everything except Multi-Modal Inference works without
   it. See [`models/README.md`](../models/README.md) to generate it later.
6. **`npm install`** in `ui/` — installs the Angular UI's dependencies.
7. **Runs `ruff check .`, `mypy .`, `pytest -q`** — the same checks CI runs. With Postgres up from
   step 3, this now exercises the full test suite (DB-backed tests included) instead of skipping
   them, so a clean run here is a real signal your environment works end to end.

## Troubleshooting

**"Docker daemon is not running"** — Start Docker Desktop (macOS/Windows) or
`sudo systemctl start docker` (Linux), then re-run the script.

**Port 5433 or 11434 already in use** — Something else on your machine is already using
Postgres's or Ollama's port. `docker compose down` stops SentinelPCB's own containers if a
previous run didn't clean up; otherwise find and stop whatever else is bound to that port.

**`npm install` fails with `Cannot read properties of null (reading 'edgesOut')`** — This is why
the script passes `--legacy-peer-deps`; it works around an npm arborist bug seen on npm 10.x with
this dependency graph. See [`ui/README.md`](../ui/README.md).

**ADC model not found** — Expected on a fresh clone, it's gitignored. Not a blocker for most of
the app; see [`models/README.md`](../models/README.md) when you're ready to generate it.

**`pytest` fails on DB-backed tests** — Confirm the `db` container is actually healthy:
`docker compose ps db`. If it's not there, re-run the setup script, or start it directly with
`docker compose up -d --wait db`.

## Next steps

Environment running? Head to [`DEVELOPMENT.md`](../DEVELOPMENT.md) for the spec-driven workflow,
branching/review rules, and Definition of Done, that's what actually governs how a change gets
made in this repo.
