# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SentinelChat: an Angular chat UI over a FastAPI SSE backend, with PCB-defect inspection agents (LangGraph) reachable either as chat tools or from a separate "Work" tab. Three independently-built pieces live in one repo: the backend (`app/`, root `pyproject.toml`), the Angular UI (`ui/`), and a standalone ONNX classification microservice (`inference/`, its **own** `pyproject.toml`, venv, and CI job - never lint/type-check it against the root project).

`README.md`, `ONBOARDING.md`, `DEVELOPMENT.md` and `docs/ARCHITECTURE.md` are detailed and worth reading.

## Commands

Backend (run from repo root, uses `uv`; Python 3.12):

```bash
uv sync
uv run ruff check .
uv run mypy .                                   # strict
uv run pytest                                   # needs Postgres (see below)
uv run pytest tests/chat/agents/test_adc_policy.py   # one file; add ::test_name for one test
uv run pytest tests/workflow                    # one module (tests/shared, tests/chat, tests/workflow)
uv run uvicorn app.main:app --reload            # http://localhost:8000
uv run alembic revision --autogenerate -m "..." # after changing app/*/db/models/
uv run alembic upgrade head
```

UI (from `ui/`, Node 22; Angular 22 + Vitest via `@angular/build:unit-test`):

```bash
npm start                                       # http://localhost:4200
npx ng test --watch=false                       # all specs
npx ng test --watch=false --include='src/app/work/**/*.spec.ts'   # one area (chat/ or work/)
npx ng build
```

Inference service (from `inference/`, separate venv): `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q`. Note it also enforces `ruff format`; the backend does not.

Local stack: `bash infra/development/scripts/unix/setup-dev.sh` (or `powershell -File infra\development\scripts\windows\setup-dev.ps1`) is idempotent and does everything, including `docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait db qdrant litellm`. **Always pass `--env-file .env`** to hand-run compose commands, otherwise `${...}` substitutions (e.g. the LiteLLM key) silently go blank. `ui`, `inference`, `langfuse` are opt-in `--profile`s.

CI (`.github/workflows/ci.yml`) runs exactly: backend ruff + mypy + pytest against a real Postgres service, inference checks, UI `ng test` + `ng build`.

## Testing notes

- DB-backed tests hit real Postgres (`DATABASE_URL`; dev compose maps it to host port 5433) via the `db_session`/`client`/`authenticated_client` fixtures in `tests/conftest.py`, and **skip** (not fail) if it's unreachable. If Postgres rejects the credentials the whole run exits with reset instructions (stale `db` volume).
- `tests/` mirrors `app/` (`tests/shared`, `tests/chat`, `tests/workflow`); `conftest.py` stays at `tests/`. It imports `app.chat.db.models` itself because `create_all` runs before `app.main` is imported - a new module with tables needs the same.
- `tests/conftest.py` autouse-disables long-term memory, file logging and the intent router, because their extra LLM/embedding calls would interleave with the mocked `httpx.AsyncClient` chat-provider requests. Tests that cover those re-enable them explicitly - do the same rather than removing the fixtures.
- The **first** user ever registered is forced to Admin, so a test needing a QA-role user must first request `authenticated_client` (see `qa_authenticated_client`).
- `asyncio_mode = "auto"`: no `@pytest.mark.asyncio` needed.
- `monkeypatch.setattr("app.x.y.z", ...)` string targets and `getLogger(__name__)` names embed module paths, so they break silently when a module moves - grep for the old path after any move.

## Architecture

### Module boundaries (the main rule)

`app/` is two independent feature modules over shared code. Each owns its own `api/` (routes only), `agents/`, `services/`, `core/` and `db/` as needed:

- `app/shared/` - auth, config (`settings.py` is still one class for everything), `db/` (`Base`, engine/session, auth models `User`/`RefreshToken`), the `inference/` client, and the auth/health routes.
- `app/chat/` - chat SSE, tool-calling agents (`agents/`), `memory/`, `uploads/`, `services/` (providers, history, streaming), and every chat-owned table (`db/`: conversations, cases, golden images, retraining tickets).
- `app/workflow/` - the Work tab: `agents/{orchestrator_agent,explainability_review_agent}`, `services/` (run streaming, upload storage, schemas) and `api/orchestrator.py`. Owns no tables.
- `app/main.py` - composition root only: mounts `shared`/`chat`/`workflow` routers (each module's `api/__init__.py` exposes `router` and `STREAMING_PATHS`) and imports each module's models so `create_all` sees them.

**`chat` and `workflow` must never import each other and `shared` imports neither** - `tests/test_module_boundaries.py` AST-scans every import (lazy ones too). Anything both need goes in `shared`. Add endpoints in the owning module's `api/`, not `main.py`. All models share one `Base`/Alembic history.

### Other layering conventions

- `app/chat/core/` and `app/shared/core/` hold IO-free `Protocol`s/exceptions (`ChatService`, `MemoryStore`, `Tool`); each has one factory picking the concrete implementation (`get_chat_service()` in `app/chat/services/service.py`, Ollama vs OpenAI; Qdrant is only known to `app/chat/memory/qdrant_store.py`). Swap points are single files.
- Backend is stateless per request (except chat/Work uploads on local disk - a known gap that blocks multi-task deploys).
- Nearly every subsystem has a settings kill switch (`MEMORY_ENABLED`, `CHAT_TOOL_CALLING_ENABLED`, `INTENT_ROUTER_ENABLED`, `ADC_INSPECTION_AGENT_ENABLED`, `EXPLAINABILITY_AGENT_ENABLED`, `ORCHESTRATOR_AGENT_ENABLED`, ...) in `app/shared/config/settings.py`, and agents degrade gracefully (templated/heuristic fallback, fail-open) rather than raising. Preserve that when adding LLM steps.
- All OpenAI-compatible calls go to `settings.openai_base_url`, a **LiteLLM proxy** (`infra/litellm/`), never `api.openai.com`; `OPENAI_API_KEY` is a LiteLLM key. Ollama is called directly.

### Chat flow and tool calling (`app/chat/services/streaming.py`)

`POST /api/chat/stream` -> `chat_sse` streams `event: delta|tool_call|error|done`. If tool calling is on, it optionally runs the intent router (`app/chat/agents/router_agent/`, one LLM call; picks a tool or asks a clarifying question; fails open), then loops up to `CHAT_TOOL_MAX_ROUNDS` executing tool calls through `ToolRegistry`/`call_tool` (`app/chat/agents/registry.py`). Two gates decide tool availability: `_available_tool_specs()` (image-requiring tools are only offered when the message has an attached image, because the model can't name a real upload id - `_run_tool_call` overrides whatever id it supplies) and role checks in `app/chat/agents/access.py::TOOL_ROLES`, re-checked at dispatch. **Adding a chat tool means**: implement `Tool`, register it in the registry, add it to `TOOL_ROLES`, and add a label to `_TOOL_DISPLAY_LABELS`. `ChatService.stream_with_tools()` is the tool-aware provider method; `stream_reply()` is still used by memory fact-extraction.

### The agents - names are confusing, read this

- `chat/agents/adc_inspection_agent/`: chat tools `create_case`, `list_cases`, `review_case`. Cyclic LangGraph Planner -> PolicyEngine -> execute -> replan for **one image**; persists a `Case` row; calls the `inference/` service via `app/shared/inference/client.py`; on REVIEW_REQUIRED escalates in-process to the case review agent. (Its internal state type is called `OrchestratorState` - unrelated to `workflow/agents/orchestrator_agent`.)
- `chat/agents/case_review_agent/` (formerly `explainability_review_agent/`): the **chat-facing** diagnosis pipeline (`explainability_review`, `investigate_case` tools; also `POST /api/agents/explainability-review`). Uses lazily-loaded CLIP + an *embedded file-based* Qdrant (`data/images/qdrant_db/`, seeded manually by `scripts/explainability_agent/`), separate from the Docker Qdrant used for chat memory. It and `adc_inspection_agent` import each other (both in chat).
- `workflow/agents/explainability_review_agent/` (current meaning): a **Work-tab-only** unrelated pipeline ported from `pcb_agentic_inspector`'s Agent 2. Never a chat tool; called in-process by the orchestrator for REVIEW_REQUIRED samples. Reads its own `config/agent2_config.yaml` and a raw `OPENAI_API_KEY` env var (deliberately not `settings`), and uses a local Ollama LLaVA.
- `workflow/agents/orchestrator_agent/`: **Work-tab-only** bulk CSV/XML dataset workflow ported from `orchestrator-agent/adc_agentic_project` (modes `prepare`, `prepare_verify`, `run_full`). Served by `workflow/api/orchestrator.py` (QA/Admin only) as SSE (`/api/orchestrator/run/stream`) with its own `/api/orchestrator/uploads/*`; its `run()` is an async generator and wraps sync planner/pandas/opencv work in `asyncio.to_thread`. The UI counterpart is `ui/src/app/work/` (default landing route).
- `chat/agents/{weather,time}_agent/`: small LangGraph tools (`get_weather`, `current_time`); time has no LLM step by design. `chat/agents/monitoring_agent/`: `flag_case_for_retraining` (real DB write of a `RetrainingTicket`) and an Admin-only `monitoring_status` placeholder.

**Ported-code carve-out:** `app/workflow/agents/orchestrator_agent/` and `app/workflow/agents/explainability_review_agent/` are close ports of external projects, kept unformatted/untyped so diffs against upstream stay legible. They are excluded from ruff (`extend-exclude`) and have mypy `ignore_errors` in `pyproject.toml`. Don't reformat or "fix the types" in them; match their existing style instead.

### Memory

Short-term: `Conversation`/`Message` rows in Postgres (`app/chat/db/models/chat.py`), replayed by `app/chat/services/history.py`. Long-term: extracted facts embedded into Docker Qdrant behind `MemoryStore` (`app/chat/memory/`). Both scoped per user.

### Frontend

Standalone Angular components with signals (no NgRx). Mirrors the backend split: `ui/src/app/chat/` (chat component plus its services, responders, models, `sidebar/`, `settings/`) and `ui/src/app/work/` (work component, `work.service.ts`, `work-orchestrator-client.ts`, models); shared shell/auth/theme stay at the app root. `ChatResponder` (`chat/chat-responder.ts`) is the swap point between `HttpChatResponder` (real backend) and `MockChatResponder` (test double); responders stream chunks. Routes: `/` redirects to `/work`; `/chat`, `/c/:id`, `/work` are behind `authGuard`. Tailwind v4 via PostCSS.

### Infra

`infra/Dockerfile` is the single backend image for dev and prod (its `CMD` uses `.venv/bin/uvicorn` directly, not `uv run`, to avoid re-syncing at container start). Production is Terraform on AWS (`infra/production/`): one CloudFront distribution serves both the S3 UI and `/api/*` (don't add a second distribution or point the UI at the ALB), with LiteLLM and inference reachable only over Cloud Map private DNS.

## Workflow conventions

- Branch from `dev` and open PRs against `dev`; `main` is production and only advances via a deliberate `dev` -> `main` PR. Conventional Commits (`feat:`, `fix:`, `chore:`, `ci:`, `docs:`, `infra:`) are required.
- User-visible changes need a `CHANGELOG.md` `[Unreleased]` entry; version bumps (`pyproject.toml`, `ui/package.json`) are semver and independent per package.
- New behavior needs a test.
