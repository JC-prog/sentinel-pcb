# SentinelChat

A ChatGPT-style chat UI (Angular) backed by a FastAPI SSE streaming backend, running on OpenAI
with a key the server operator configures, user accounts, and both short-term (per-conversation) and long-term
(cross-conversation) chat memory. The assistant can also call tools mid-conversation -
PCB defect inspection of an attached image (via the paperclip button or by dragging a file onto
the chat window), recording a QA's label corrections and case reviews, looking up what the Work
tab stored about a sample or run, and reporting model drift and drafting retraining plans -
deciding on its own when one is actually needed. A **Work** tab runs bulk datasets through the
two-stage classifier with an explanation-review step, and a **Models** tab manages model versions
and retraining. See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) for what to type/attach/click to
trigger each capability, [`docs/DEMO.md`](docs/DEMO.md) for a step-by-step demo script, and
[`docs/REVIEW_DRIFT_RETRAINING.md`](docs/REVIEW_DRIFT_RETRAINING.md) for how review, drift and
retraining fit together.

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
history) and `qdrant` (long-term cross-conversation memory) containers via
`docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait`. Then, in
two terminals:

```bash
uv run uvicorn app.main:app --reload   # http://localhost:8000
cd ui && npm start                      # http://localhost:4200
```

The only value you may need to set by hand is `OPENAI_API_KEY` in `.env` - your own OpenAI key -
(chat, the agents' LLM calls, memory embeddings, the guardrails check, Agent 2 and the Work tab's
LLM planner all need it). Free host ports needed: 8000, 4200, 5433,
6333, 6334.

### External dependencies

| Dependency | Required? | Notes |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Yes | Runs local Postgres and Qdrant via Compose; the setup script starts them for you. |
| Postgres | Auto (Docker) | User accounts, auth, and per-conversation chat history (Alembic-migrated - see `alembic/`). |
| Qdrant | Auto (Docker) | Long-term, cross-conversation memory (`app/chat/memory/`). Can be turned off entirely with `MEMORY_ENABLED=False` in `.env`. Ships its own web dashboard at [localhost:6333/dashboard](http://localhost:6333/dashboard) - no extra setup, useful for browsing collections/points directly. |
| OpenAI access | Required | Chat, the chat agents' LLM calls (the inspect agent's ReAct pass), memory embeddings, the guardrails check, Agent 2's grounding and vision steps and the Work tab's LLM planner all call OpenAI directly with the server-side `OPENAI_API_KEY` in `.env`. Put **your own** key there. No per-request bring-your-own-key - never entered in the browser. |
| LangFuse | Optional (Docker profile) | Self-hosted LLM observability/tracing for the chat agents (`app/shared/config/langfuse.py`). Off by default (`LANGFUSE_ENABLED=False`); `docker compose -f infra/development/docker-compose.yml --env-file .env --profile langfuse up -d --wait` brings it up at [localhost:3000](http://localhost:3000), auto-provisioning an org/project/API key that match `.env`'s `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` - no manual UI setup. |

See [`DEVELOPMENT.md`](DEVELOPMENT.md) for the branching/PR workflow, an architecture overview,
and known gotchas before you start changing things.

## Repo layout

```
app/                  # FastAPI backend, as independent feature modules over shared code:
├── shared/             #   auth, config, DB base/session, model-ops tables + rules, inference client (every module uses this)
├── chat/               #   chat SSE, tool-calling agents, long-term memory (chat/memory/ is Qdrant-backed)
├── workflow/           #   the Work tab's orchestrator + explainability-review agents
└── modelops/           #   the Models tab's API: model versions, drift reports, retraining queue
alembic/              # Postgres schema migrations (users, conversations, messages)
ui/                   # Angular frontend
infra/
├── Dockerfile          # backend image (shared by dev and prod)
├── development/         # Docker Compose + per-OS setup scripts, for local dev
└── production/           # Terraform (AWS: ECS Fargate, RDS, S3 + CloudFront) - see its README
tests/                 # backend tests (pytest), mirroring app/: tests/shared, tests/chat, tests/workflow, tests/modelops
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
