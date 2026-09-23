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

Local stack: `bash infra/development/scripts/unix/setup-dev.sh` (or `powershell -File infra\development\scripts\windows\setup-dev.ps1`) is idempotent and does everything, including `docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait db qdrant litellm`. **Always pass `--env-file .env`** to hand-run compose commands, otherwise `${...}` substitutions (e.g. the LiteLLM key) silently go blank. `ui`, `inference`, `langfuse` are opt-in `--profile`s. After that, `start-dev.sh`/`start-dev.ps1` (same `scripts/{unix,windows}/` dirs) start the backend, `inference/`, and the UI natively with live reload in one command - also fills in `INFERENCE_BASE_URL` in `.env` if it's blank/missing, so the two are wired together without manual setup.

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

`app/` is three independent feature modules over shared code. Each owns its own `api/` (routes only), `agents/`, `services/`, `core/` and `db/` as needed:

- `app/shared/` - auth, config (`settings.py` is still one class for everything), `db/` (`Base`, engine/session, auth models `User`/`RefreshToken`, and the model-operations tables `ModelVersion`/`DriftReport`/`RetrainingJob`/`RetrainingTicket` - shared because chat's monitoring agent and the Models tab both use them), `modelops/` (version registry, drift reports, tickets, retraining-job state machine; no HTTP), the `inference/` client (classify, plus versions/activate/rollback/jobs), and the auth/health routes.
- `app/chat/` - chat SSE, tool-calling agents (`agents/`), `memory/`, `uploads/`, `services/` (providers, history, streaming), and every chat-owned table (`db/`: conversations, cases, golden images). `RetrainingTicket` references `cases` by foreign key string only.
- `app/workflow/` - the Work tab: `src/agent1_orchestrator/` (+ sibling top-level `planner/`, `state/`, `verification/`) is a close-to-verbatim drop-in of `pcb_agentic_inspector`'s `OrchestratorAgent`/`DatasetPreparationService`/`DatasetVerificationService` - see its own "Ported-code carve-out" note below. `api/orchestrator.py` and `services/` (`streaming.py`, `uploads.py`, `monitoring.py`, `schemas.py`) are this repo's own thin FastAPI layer around it - `services/streaming.py`'s `_bridge_sys_path()` is the one place that reaches into the drop-in's bare (unqualified) import style. Owns no tables.
- `app/modelops/` - the Models tab: `api/models.py` (`/api/models/*`: model versions, drift reports, the retraining queue; QA/Admin read, **Admin-only** approve/cancel/promote/rollback) and `services/` (`views.py` read models, `operations.py` Admin actions, `reconcile.py` syncing versions/jobs from the inference service, best-effort so an unreachable service degrades to last-known data). Owns no tables - they are in `shared` because the chat monitoring agent writes them too. The inference service holds no durable state; the app's DB is the record.
- `app/main.py` - composition root only: mounts `shared`/`chat`/`workflow`/`modelops` routers (each module's `api/__init__.py` exposes `router` and `STREAMING_PATHS`) and imports each module's models so `create_all` sees them.

**No feature module (`chat`, `workflow`, `modelops`) may import another, and `shared` imports none of them** - `tests/test_module_boundaries.py` AST-scans every import (lazy ones too). Anything both need goes in `shared`. Within `chat`, **agents (`chat/agents/<name>/`) don't import each other either** - `tests/chat/test_agent_boundaries.py` enforces it (lazy imports too, no allowlist); what two agents share (case lookup `services/cases.py`, `services/xml_measurements.py`, uploads) lives in `chat/services/`. Add endpoints in the owning module's `api/`, not `main.py`. All models share one `Base`/Alembic history.

### Other layering conventions

- `app/chat/core/` and `app/shared/core/` hold IO-free `Protocol`s/exceptions (`ChatService`, `MemoryStore`, `Tool`); each has one factory picking the concrete implementation (`get_chat_service()` in `app/chat/services/service.py`, Ollama vs OpenAI; Qdrant is only known to `app/chat/memory/qdrant_store.py`). Swap points are single files.
- Backend is stateless per request (except chat/Work uploads on local disk - a known gap that blocks multi-task deploys).
- Nearly every subsystem has a settings kill switch (`MEMORY_ENABLED`, `CHAT_TOOL_CALLING_ENABLED`, `INTENT_ROUTER_ENABLED`, `ADC_INSPECTION_AGENT_ENABLED`, `EXPLAINABILITY_AGENT_ENABLED`, `ORCHESTRATOR_AGENT_ENABLED`, ...) in `app/shared/config/settings.py`, and agents degrade gracefully (templated/heuristic fallback, fail-open) rather than raising. Preserve that when adding LLM steps.
- All OpenAI-compatible calls go to `settings.openai_base_url`, a **LiteLLM proxy** (`infra/litellm/`), never `api.openai.com`; `OPENAI_API_KEY` is a LiteLLM key. Ollama is called directly.

### Chat flow and tool calling (`app/chat/services/streaming.py`)

`POST /api/chat/stream` -> `chat_sse` streams `event: delta|tool_call|error|done`. If tool calling is on, it optionally runs the intent router (`app/chat/agents/router_agent/`, one LLM call; picks a tool or asks a clarifying question; fails open), then loops up to `CHAT_TOOL_MAX_ROUNDS` executing tool calls through `ToolRegistry`/`call_tool` (`app/chat/agents/registry.py`). Two gates decide tool availability: `_available_tool_specs()` (image-requiring tools are only offered when the message has an attached image, because the model can't name a real upload id - `_run_tool_call` overrides whatever id it supplies) and role checks in `app/chat/agents/access.py::TOOL_ROLES`, re-checked at dispatch. **Adding a chat tool means**: implement `Tool`, register it in `tool_registry` (`streaming.py`), add it to `TOOL_ROLES`, add a label to `_TOOL_DISPLAY_LABELS`, and - if it just needs the DB session/user/conversation - add its name to `_CONTEXT_TOOLS` so `_run_tool_call` injects them. `ChatService.stream_with_tools()` is the tool-aware provider method; `stream_reply()` is still used by memory fact-extraction.

### The agents - names are confusing, read this

- `chat/agents/inspection_agent/`: chat tool `inspect_image` ("what defect does this image have?"). Two stages: an **LLM-driven ReAct pass** (`react.py`; its tools are the inspection steps, each checked by `PolicyEngine`, and it writes a summary), then the **deterministic pipeline** (`graph.py`: Planner -> PolicyEngine -> execute -> replan for one image), which finishes anything the LLM skipped, decides ACCEPTED/REVIEW_REQUIRED from fixed rules and persists the `Case` (always; REVIEW_REQUIRED is terminal, no hand-off to another agent). With no OpenAI key / `INSPECTION_AGENT_LLM_ENABLED` off / any LLM failure it just runs the deterministic stage - the verdict never comes from the LLM. Calls the `inference/` service via `app/shared/inference/client.py` and stamps each Case with the model versions that answered. `board_id`/`component_ref` are optional. (Its internal state type is called `OrchestratorState` - unrelated to `workflow/src/agent1_orchestrator`'s `OrchestratorAgent`.)
- `chat/agents/case_agent/` (formerly `explainability_review_agent/`): everything about existing cases - `find_similar_cases` (structured match over the `cases` table; defaults to the latest case in the conversation), `get_case`, `list_cases`, `review_case` (`case_tools.py`), plus the **heavy diagnosis pipeline** (`explainability_review`, `investigate_case`; also `POST /api/agents/explainability-review`), which uses lazily-loaded CLIP + an *embedded file-based* Qdrant (`data/images/qdrant_db/`, seeded manually by `scripts/explainability_agent/`), separate from the Docker Qdrant used for chat memory. Nothing calls it in-process.
- `workflow/src/agent2_explainability/` (current meaning): a **Work-tab-only** unrelated pipeline ported from `pcb_agentic_inspector`'s Agent 2 (A2A/MCP scaffolding + review pipeline). Never a chat tool. **Currently unwired** - `agents/orchestrator.py` is constructed with `enable_a2a=False` (`app/workflow/services/streaming.py`), so nothing calls into it yet; a deliberate scope cut, not an oversight, when the orchestrator was routed through this repo's own FastAPI layer.
- `workflow/src/agent1_orchestrator/`: **Work-tab-only** bulk CSV/XML dataset workflow, a close-to-verbatim drop-in of `pcb_agentic_inspector`'s Agent 1 (modes `prepare`, `prepare_verify`, `run_full`). `OrchestratorAgent.run()` is synchronous and returns a `WorkflowState`, not an async generator - `app/workflow/services/streaming.py` runs it via `asyncio.to_thread` and live-streams progress through the one hook added to it, `run(..., on_step=...)`. Served by `workflow/api/orchestrator.py` (QA/Admin only) as SSE (`/api/orchestrator/run/stream`) with its own `/api/orchestrator/uploads/*`. `services/model_lifecycle.py` and `services/multimodal_inference.py` are the two files in this drop-in adapted (not ported verbatim) to call the `inference/` microservice over HTTP instead of loading local ONNX files - see their module docstrings. The UI counterpart is `ui/src/app/work/` (default landing route).
- `chat/agents/{weather,time}_agent/`: small LangGraph tools (`get_weather`, `current_time`); time has no LLM step by design. `chat/agents/monitoring_agent/`: model health - `flag_case_for_retraining` (a `RetrainingTicket` recording the model version and labels), `report_model_drift` / `get_drift_summary` (indicators computed from Cases in `chat/services/drift.py`; reports stored via `shared/modelops`), `draft_retraining_plan` (open tickets -> a `RetrainingJob` in `pending_approval`) and Admin-only `monitoring_status`. **Chat never approves a job or promotes a model** - those are Admin actions in the Models tab (`modelops_enabled` gates the drift/plan tools).

**Ported-code carve-out:** `app/workflow/src/` (Agent 1 and Agent 2) and its sibling top-level `planner/`, `state/`, `verification/`, `adc_shared/`, `data/`, `tests/`, `adc_rest.py`, `test_rest.py` are a close-to-verbatim drop-in of `pcb_agentic_inspector`, kept unformatted/untyped so diffs against upstream stay legible - see `app/workflow/INTEGRATION_NOTES.md` for what was changed, moved or deleted from the raw drop-in and why. They're excluded from ruff (`extend-exclude`) and mypy (`exclude`) in `pyproject.toml`. Don't reformat or "fix the types" in them; match their existing style instead. Only `app/workflow/api/` and `app/workflow/services/` (the thin FastAPI layer wired around this drop-in) are this repo's own code.

### Memory

Short-term: `Conversation`/`Message` rows in Postgres (`app/chat/db/models/chat.py`), replayed by `app/chat/services/history.py`. Long-term: extracted facts embedded into Docker Qdrant behind `MemoryStore` (`app/chat/memory/`). Both scoped per user.

### Frontend

Standalone Angular components with signals (no NgRx). Mirrors the backend split: `ui/src/app/chat/` (chat component plus its services, responders, models, `sidebar/`, `settings/`) `ui/src/app/work/` (work component, `work.service.ts`, `work-orchestrator-client.ts`, models) and `ui/src/app/models/` (the Models tab: `models.ts`, `model-ops.service.ts` state + polling while a retraining job is moving, `model-ops-client.ts` over `/api/models/*`; Admin-only buttons are hidden from QA but the backend enforces it); shared shell/auth/theme stay at the app root. `ChatResponder` (`chat/chat-responder.ts`) is the swap point between `HttpChatResponder` (real backend) and `MockChatResponder` (test double); responders stream chunks. Routes: `/` redirects to `/work`; `/chat`, `/c/:id`, `/work`, `/models` are behind `authGuard`; the mode toggle (`mode-toggle/`) switches between Chat | Work | Models. Tailwind v4 via PostCSS.

### Infra

`infra/Dockerfile` is the single backend image for dev and prod (its `CMD` uses `.venv/bin/uvicorn` directly, not `uv run`, to avoid re-syncing at container start). Production is Terraform on AWS (`infra/production/`): one CloudFront distribution serves both the S3 UI and `/api/*` (don't add a second distribution or point the UI at the ALB), with LiteLLM and inference reachable only over Cloud Map private DNS.

## Workflow conventions

- Branch from `dev` and open PRs against `dev`; `main` is production and only advances via a deliberate `dev` -> `main` PR. Conventional Commits (`feat:`, `fix:`, `chore:`, `ci:`, `docs:`, `infra:`) are required.
- User-visible changes need a `CHANGELOG.md` `[Unreleased]` entry; version bumps (`pyproject.toml`, `ui/package.json`) are semver and independent per package.
- New behavior needs a test.
