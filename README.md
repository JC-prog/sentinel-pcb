# SentinelChat

A ChatGPT-style chat UI (Angular) backed by a FastAPI SSE streaming backend, with a choice of
LLM provider per conversation: a local Ollama model, or OpenAI with your own API key.

## Getting started

```bash
bash infra/development/scripts/unix/setup-dev.sh        # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1   # Windows
```

Installs backend + UI dependencies, creates `.env` from `.env.example`, and starts local
Postgres and Qdrant containers (`docker compose -f infra/development/docker-compose.yml up -d
--wait db qdrant`) - both provisioned ahead of need (multi-user/auth work, and a future
RAG/semantic-search feature, respectively); nothing reads either yet. Then, in two terminals:

```bash
uv run uvicorn app.main:app --reload   # http://localhost:8000
cd ui && npm start                      # http://localhost:4200
```

Install [Ollama](https://ollama.com) locally to use the Local LLM option in Settings; the OpenAI
option needs no local setup, just an API key pasted into the same Settings panel.

See [`DEVELOPMENT.md`](DEVELOPMENT.md) for the branching/PR workflow, an architecture overview,
and known gotchas before you start changing things.

## Repo layout

```
app/                  # FastAPI backend
ui/                   # Angular frontend
infra/
├── Dockerfile          # backend image (shared by dev and prod)
├── development/         # Docker Compose + per-OS setup scripts, for local dev
└── production/           # Terraform (AWS: ECS Fargate, RDS, S3 + CloudFront) - see its README
tests/                 # backend tests (pytest)
```

## Commands

```bash
uv run ruff check .                    # lint
uv run mypy .                          # type check (strict)
uv run pytest                          # backend tests
cd ui && npx ng test --watch=false     # frontend tests
cd ui && npx ng build                  # production UI build
```
