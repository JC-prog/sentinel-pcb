# SentinelChat

A ChatGPT-style chat UI (Angular) backed by a FastAPI SSE streaming backend, with a choice of
LLM provider per conversation (a local Ollama model, or OpenAI with your own API key),
user accounts, and both short-term (per-conversation) and long-term (cross-conversation) chat
memory. The assistant can also call tools mid-conversation - the current time, live weather for
a named location, and (when you attach an image) PCB defect diagnosis via the Explainability &
Review Agent - deciding on its own when one is actually needed.

## Getting started

```bash
bash infra/development/scripts/unix/setup-dev.sh        # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1   # Windows
```

Installs backend + UI dependencies, creates `.env` from `.env.example`, and starts local
Postgres and Qdrant containers (`docker compose -f infra/development/docker-compose.yml up -d
--wait db qdrant`) - Postgres backs user accounts and per-conversation chat history, Qdrant backs
long-term cross-conversation memory. Then, in two terminals:

```bash
uv run uvicorn app.main:app --reload   # http://localhost:8000
cd ui && npm start                      # http://localhost:4200
```

### External dependencies

| Dependency | Required? | Notes |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Yes | Runs local Postgres + Qdrant via Compose; the setup script starts both for you. |
| Postgres | Auto (Docker) | User accounts, auth, and per-conversation chat history (Alembic-migrated - see `alembic/`). |
| Qdrant | Auto (Docker) | Long-term, cross-conversation memory (`app/memory/`). Can be turned off entirely with `MEMORY_ENABLED=False` in `.env`. |
| [Ollama](https://ollama.com) | Optional | Needed for the Local LLM option in Settings. **Not installed or pulled automatically** - install it, then pull the models you intend to use: `ollama pull llama3.2` (default chat model) and `ollama pull nomic-embed-text` (default long-term-memory embedding model). Without the embedding model pulled, long-term memory silently no-ops instead of erroring. |
| OpenAI API key | Optional | Bring-your-own-key alternative to Ollama for chat, and also what the Explainability & Review Agent (`POST /api/agents/explainability-review`) uses for its GPT-4o reasoning/vision calls - paste a key into the Settings panel, or set `EXPLAINABILITY_AGENT_OPENAI_API_KEY` as a server-side fallback for that agent specifically. Never stored server-side either way. |

If you only plan to use the OpenAI option, Ollama can be skipped entirely.

The Explainability & Review Agent additionally uses its own **embedded, file-based Qdrant
instance** (`data/images/qdrant_db/`, see `EXPLAINABILITY_AGENT_DATA_DIR`) for historical defect
lookups - separate from the Docker Qdrant container above, and not provisioned automatically. Its
CLIP image-embedding model (`sentence-transformers`, ~350 MB, pulls in `torch`) downloads on first
use of the agent rather than at install time, so the first request after a fresh setup will be
slower. See [`DEVELOPMENT.md`](DEVELOPMENT.md) for how to seed it via
`scripts/explainability_agent/`.

See [`DEVELOPMENT.md`](DEVELOPMENT.md) for the branching/PR workflow, an architecture overview,
and known gotchas before you start changing things.

## Repo layout

```
app/                  # FastAPI backend (app/memory/ is the Qdrant-backed long-term chat memory)
alembic/              # Postgres schema migrations (users, conversations, messages)
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
uv run alembic upgrade head            # apply Postgres migrations
```
