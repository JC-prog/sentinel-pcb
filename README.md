# SentinelChat

A ChatGPT-style chat UI (Angular) backed by a FastAPI SSE streaming backend, with a choice of
LLM provider per conversation (a local Ollama model, or OpenAI using a key the server operator
configures), user accounts, and both short-term (per-conversation) and long-term
(cross-conversation) chat memory. The assistant can also call tools mid-conversation - the
current time, live weather for a named location, and (when you attach an image - via the
paperclip button or by dragging a file onto the chat window) PCB defect diagnosis via the
Explainability & Review Agent - deciding on its own when one is actually needed.

## Getting started

For the exact step-by-step (with verification and troubleshooting), see
[`ONBOARDING.md`](ONBOARDING.md). The short version:

Install first (the setup script checks for each and stops with an install hint if one is
missing):

- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** - manages Python deps and
  fetches Python 3.12 itself, so no separate Python install is needed
- **[Docker](https://docs.docker.com/get-docker/)** with Compose v2, and the daemon running
- **[Node.js](https://nodejs.org/) 22** (with npm) for the Angular UI
- **Git**

Then:

```bash
bash infra/development/scripts/unix/setup-dev.sh        # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1   # Windows
```

Installs backend + UI dependencies, creates `.env` from `.env.example` (generating
`JWT_SECRET_KEY`), and starts the local `db` (Postgres, user accounts + per-conversation
history), `qdrant` (long-term cross-conversation memory), and `litellm` (the OpenAI-compatible
LLM gateway) containers via `docker compose -f infra/development/docker-compose.yml up -d
--wait`. Then, in two terminals:

```bash
uv run uvicorn app.main:app --reload   # http://localhost:8000
cd ui && npm start                      # http://localhost:4200
```

The only value you may need to set by hand is `LITELLM_OPENAI_API_KEY` in `.env` - your own
OpenAI key, read only by your local `litellm` container - if your work exercises the OpenAI
provider, the Explainability & Review Agent, or OpenAI embeddings. Leave it blank for
Ollama-only. Free host ports needed: 8000, 4200, 4000, 5433, 6333, 6334.

### External dependencies

| Dependency | Required? | Notes |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Yes | Runs local Postgres, Qdrant, and the LiteLLM proxy via Compose; the setup script starts them for you. |
| Postgres | Auto (Docker) | User accounts, auth, and per-conversation chat history (Alembic-migrated - see `alembic/`). |
| Qdrant | Auto (Docker) | Long-term, cross-conversation memory (`app/memory/`). Can be turned off entirely with `MEMORY_ENABLED=False` in `.env`. |
| [Ollama](https://ollama.com) | Optional | Needed for the Local LLM option in Settings. **Not installed or pulled automatically** - install it, then pull the models you intend to use: `ollama pull llama3.2` (default chat model) and `ollama pull nomic-embed-text` (default long-term-memory embedding model). Without the embedding model pulled, long-term memory silently no-ops instead of erroring. |
| OpenAI access | Optional | The OpenAI provider in Settings and the Explainability & Review Agent's (`POST /api/agents/explainability-review`) GPT-4o calls go through a LiteLLM proxy (`OPENAI_BASE_URL`), not `api.openai.com` directly. The proxy runs in Docker as part of the dev stack; put **your own** OpenAI key in `LITELLM_OPENAI_API_KEY` in `.env` (only that container sees it), or leave it blank for Ollama-only. See `infra/litellm/README.md`. No per-request bring-your-own-key - never entered in the browser. |

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
├── litellm/            # LiteLLM proxy configs (dev + prod), shared by both - see its README
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
