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
uv run pytest -m eval -s                        # live-LLM tool-selection evals (tests/evals/); excluded by default, costs tokens
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
- `tests/conftest.py` autouse-disables long-term memory, file logging and the chat guardrails, because their extra LLM/embedding calls would interleave with the mocked `httpx.AsyncClient` chat-provider requests. Tests that cover those re-enable them explicitly - do the same rather than removing the fixtures.
- The **first** user ever registered is forced to Admin, so a test needing a QA-role user must first request `authenticated_client` (see `qa_authenticated_client`).
- `asyncio_mode = "auto"`: no `@pytest.mark.asyncio` needed.
- `monkeypatch.setattr("app.x.y.z", ...)` string targets and `getLogger(__name__)` names embed module paths, so they break silently when a module moves - grep for the old path after any move.

## Architecture

### Module boundaries (the main rule)

`app/` is three independent feature modules over shared code. Each owns its own `api/` (routes only), `agents/`, `services/`, `core/` and `db/` as needed:

- `app/shared/` - auth, config (`settings.py` is still one class for everything), `db/` (`Base`, engine/session, auth models `User`/`RefreshToken`, and the model-operations tables `ModelVersion`/`DriftReport`/`RetrainingJob`/`RetrainingTicket` - shared because chat's monitoring agent and the Models tab both use them), `modelops/` (version registry, drift reports, tickets, retraining-job state machine, and `run_drift.py` - the pure drift/operator-correction maths over a Work-tab run's stored samples, used by both workflow and chat; no HTTP), the `inference/` client (classify, plus versions/activate/rollback/jobs), and the auth/health routes.
- `app/chat/` - chat SSE, tool-calling agents (`agents/`), `memory/`, `uploads/`, `services/` (providers, history, streaming), and every chat-owned table (`db/`: conversations, cases, golden images). `RetrainingTicket` references `cases` by foreign key string only.
- `app/workflow/` - the Work tab: `src/agent1_orchestrator/` (+ sibling top-level `planner/`, `state/`, `verification/`) is a close-to-verbatim drop-in of `pcb_agentic_inspector`'s `OrchestratorAgent`/`DatasetPreparationService`/`DatasetVerificationService` - see its own "Ported-code carve-out" note below. `api/orchestrator.py` and `services/` (`streaming.py`, `uploads.py`, `monitoring.py`, `reviews.py`, `run_store.py`, `schemas.py`) are this repo's own thin FastAPI layer around it - `services/streaming.py`'s `_bridge_sys_path()` is the one place that reaches into the drop-in's bare (unqualified) import style. The Review Console's **Drift & Retraining** tab follows the Explanation Review: `GET /api/orchestrator/monitoring/run-drift` computes per-model drift and the operator's corrections (final decision != Agent 1's label) server-side from the stored run via `shared/modelops/run_drift.py`, and `POST .../run-retraining-tickets` queues a `RetrainingTicket` (keyed by `run_id`+`sample_ref`, idempotent) built from the stored sample and decision - the operator queues, nothing is auto-ticketed; chat's `draft_retraining_plan` then drafts those tickets into a job an Admin approves in the Models tab. Owns no tables: a finished run, its Agent 2 reviews and the operator's decisions are payload points in three Qdrant collections (`adc_orchestrator_runs`/`adc_inspection_results`/`adc_agent2_reviews`) written through `services/run_store.py` over the ported `adc_shared/repository.py` (fail-open to memory; decisions fall back to `workflow_review_decisions` in Postgres).
- `app/modelops/` - the Models tab: `api/models.py` (`/api/models/*`: model versions, drift reports, the retraining queue; QA/Admin read, **Admin-only** approve/cancel/promote/rollback) and `services/` (`views.py` read models, `operations.py` Admin actions, `reconcile.py` syncing versions/jobs from the inference service, best-effort so an unreachable service degrades to last-known data). Owns no tables - they are in `shared` because the chat monitoring agent writes them too. The inference service holds no durable state; the app's DB is the record.
- `app/main.py` - composition root only: mounts `shared`/`chat`/`workflow`/`modelops` routers (each module's `api/__init__.py` exposes `router` and `STREAMING_PATHS`) and imports each module's models so `create_all` sees them.

**No feature module (`chat`, `workflow`, `modelops`) may import another, and `shared` imports none of them** - `tests/test_module_boundaries.py` AST-scans every import (lazy ones too). Anything both need goes in `shared`. Within `chat`, **agents (`chat/agents/<name>/`) don't import each other either** - `tests/chat/test_agent_boundaries.py` enforces it (lazy imports too, no allowlist); what two agents share (case lookup `services/cases.py`, `services/xml_measurements.py`, uploads) lives in `chat/services/`. Add endpoints in the owning module's `api/`, not `main.py`. All models share one `Base`/Alembic history.

### Other layering conventions

- `app/chat/core/` and `app/shared/core/` hold IO-free `Protocol`s/exceptions (`MemoryStore`, `ToolContext`); the chat models are LangChain models built by one factory, `app/shared/config/llm.py` (`build_chat_model("ollama"|"openai")` for a conversation, or the agents' own model from `AGENT_LLM_*`), and chat's long-term memory is the only part that writes to Qdrant through `app/chat/memory/qdrant_store.py` (the Work tab's `app/workflow/services/run_store.py` writes its own payload-only collections; chat reads them, read-only, in `app/chat/services/run_samples.py`). Swap points are single files.
- Backend is stateless per request (except chat/Work uploads on local disk - a known gap that blocks multi-task deploys).
- Nearly every subsystem has a settings kill switch (`MEMORY_ENABLED`, `CHAT_TOOL_CALLING_ENABLED`, `ADC_INSPECTION_AGENT_ENABLED`, `RELABEL_AGENT_ENABLED`, `MONITORING_AGENT_ENABLED`, `ORCHESTRATOR_AGENT_ENABLED`, ...) in `app/shared/config/settings.py`, and agents degrade gracefully (templated/heuristic fallback, fail-open) rather than raising. Preserve that when adding LLM steps.
- All OpenAI-compatible calls go to `settings.openai_base_url`, a **LiteLLM proxy** (`infra/litellm/`), never `api.openai.com`; `OPENAI_API_KEY` is a LiteLLM key. Ollama is called directly. Every chat model - the supervisor's, memory's fact extraction, the inspect agent's ReAct pass - is built in one place, `app/shared/config/llm.py` (LangChain `init_chat_model`; `AGENT_LLM_PROVIDER`/`AGENT_LLM_MODEL`, blank model -> `OPENAI_MODEL`), so a model or provider change is a setting; with `LANGFUSE_ENABLED` every chat turn and inspection is traced (`app/shared/config/langfuse.py`).

### Chat flow and tool calling (`app/chat/services/streaming.py`)

`POST /api/chat/stream` -> `chat_sse` streams `event: delta|tool_call|tool_result|error|done`. It handles everything around the model (`/remember`, guardrails, memory, persistence) and hands the reply to the **supervisor** (`app/chat/agents/supervisor.py`): a LangGraph agent (LangChain `create_agent`) over the tools this request may use, streamed back as plain events (`TextChunk`, `ToolStarted`, `ToolFinished` with an optional card, `Answer`) that `chat_sse` turns into SSE; `streaming.py` names no tool. The model is the conversation's provider choice via `build_chat_model`; `CHAT_TOOL_MAX_ROUNDS` bounds tool-calling rounds (LangGraph's recursion limit), and the user and conversation are attached to the Langfuse trace. A tool is a LangChain `@tool` (`app/chat/agents/toolkit.py`): its docstring is what the model reads, its annotated parameters are what the model may supply, plus one injected `runtime: Runtime` (`ToolContext`: session, user, conversation, `turn_started_at`, attached upload ids, from `app/chat/core/tools.py`) that LangGraph fills in and the model never sees - so it can never supply the user, the session or an upload. Each is wrapped in a `ChatTool` declaring its `label`, `requires_image` (only offered with an image attached), `shows_card` (result also sent as `event: tool_result`) and `enabled()` (its kill switch); `tool_registry.available()` filters on those plus `access.py::TOOL_ROLES`, and only the tools it returns are given to the model, so anything else cannot run. **Adding a chat tool means**: in the owning agent's `tool.py`, write an `@tool("name")` + `@returns_json` async function (return a dict; raise `ToolRefused` for anything the model should be told - it becomes `{"error": ...}`; an unexpected exception is logged and the model told only that it failed), wrap it in a `ChatTool`, add it to `tool_registry` in `app/chat/agents/__init__.py` and to `TOOL_ROLES` (`tests/chat/agents/test_registry.py` fails if a tool has no role entry). Keep tools thin: logic lives below them (each agent's `service.py`/`pipeline.py`). Tests script the model with `tests/chat/_llm.py` (`install(monkeypatch, model)`) - nothing in the suite touches a real LLM; live tool-selection evals are opt-in (`uv run pytest -m eval`, `tests/evals/`).

### The agents - names are confusing, read this

- `chat/agents/inspection_agent/`: the **inspect agent**, chat tool `inspect_image` ("what defect does this image have?" - the user sees the result as a card). Sub-agents: `verifier.py` (readable image, optional inspection-XML validation), `classifier.py` (region model then the matching defect model, via `app/shared/inference/`), `verdict.py` (pure rules -> ACCEPTED/REVIEW_REQUIRED + reasons), `pipeline.py` (runs them and parks the result as a draft - `inspection_drafts`, one per conversation+user - with the model versions that answered). On top, an **LLM-driven ReAct pass** (`react.py`, LangChain `create_agent` on LangGraph; its `@tool`s are the inspection steps, each checked by `policy.py`) chooses the order and writes a summary. **The LLM never decides the verdict or saves anything**: `pipeline._complete` runs any step it skipped, then `verdict.decide` and the draft write are fixed code - so with no key / `INSPECTION_AGENT_LLM_ENABLED` off / any LLM failure it just runs unassisted. An inference outage is an error with no draft. **`inspect_image` saves no Case**: the user is asked whether they want one (a Case is what they relabel or review), and the `create_case` tool (`case_creation.py`, no model-supplied data) turns the draft into a Case only in a *later* chat turn (`services/confirmation.py`), consuming the draft in the same commit. `board_id`/`component_ref` are optional. (`golden_images.py` and the admin upload route remain but nothing in chat uses them.)
- `chat/agents/relabel_agent/`: tools `relabel_case` (propose) and `confirm_relabel` (commit). The QA says the model's defect label on a Case is wrong; `resolver.py` finds the Case, `labels.py` validates the label against the model's real classes from the inference service (fails closed), `service.py` holds the propose/confirm rules and `recorder.py` only the writes (the proposal parked in the Case's `pending_*` columns; on confirmation `corrected_*` - the model's own `defect_label` is never overwritten - and the `RetrainingTicket`, in one commit). **The confirm step is enforced in code**: a proposal can only be committed in a *later* chat turn (`service.confirm` compares the proposal's timestamp with `ToolContext.turn_started_at`) by the same user. Corrections feed drift (`correction_rate`).
- `workflow/src/agent2_explainability/` (current meaning): a **Work-tab-only** unrelated pipeline ported from `pcb_agentic_inspector`'s Agent 2 (A2A/MCP scaffolding + review pipeline). Never a chat tool. **Currently unwired** - `agents/orchestrator.py` is constructed with `enable_a2a=False` (`app/workflow/services/streaming.py`), so nothing calls into it yet; a deliberate scope cut, not an oversight, when the orchestrator was routed through this repo's own FastAPI layer.
- `workflow/src/agent1_orchestrator/`: **Work-tab-only** bulk CSV/XML dataset workflow, a close-to-verbatim drop-in of `pcb_agentic_inspector`'s Agent 1 (modes `prepare`, `prepare_verify`, `run_full`). `OrchestratorAgent.run()` is synchronous and returns a `WorkflowState`, not an async generator - `app/workflow/services/streaming.py` runs it via `asyncio.to_thread` and live-streams progress through the one hook added to it, `run(..., on_step=...)`. Served by `workflow/api/orchestrator.py` (QA/Admin only) as SSE (`/api/orchestrator/run/stream`) with its own `/api/orchestrator/uploads/*`. `services/model_lifecycle.py` and `services/multimodal_inference.py` are the two files in this drop-in adapted (not ported verbatim) to call the `inference/` microservice over HTTP instead of loading local ONNX files - see their module docstrings. The UI counterpart is `ui/src/app/work/` (default landing route).
- `chat/agents/sample_agent/`: read-only lookups of what the Work tab stored in Qdrant - `get_sample` (a dataset `sample_id` such as `S000001`, optional `run_id`: board/component, Agent 1's verdict, Agent 2's review, the operator's decision; several runs -> latest plus `other_runs`) `list_review_cases` and `get_run_drift` (per-model drift for a run and the operator's corrections, with which are already queued as retraining tickets). Reads through `chat/services/run_samples.py`, a chat-owned read-only reader that re-states the collection names (chat may not import workflow; `tests/chat/agents/test_sample_agent.py` checks them against what the workflow writes). A sample is not a Case and a lookup never creates one. Kill switch `SAMPLE_LOOKUP_AGENT_ENABLED`.
- `chat/agents/monitoring_agent/`: model health - `report_model_drift` / `get_drift_summary` (indicators computed from Cases in `chat/services/drift.py`: review, override and correction rates, confidence; reports stored via `shared/modelops`), `draft_retraining_plan` (open tickets -> a `RetrainingJob` in `pending_approval`) and Admin-only `monitoring_status`. **Chat never approves a job or promotes a model** - those are Admin actions in the Models tab (`modelops_enabled` gates these tools).

**Ported-code carve-out:** `app/workflow/src/` (Agent 1 and Agent 2) and its sibling top-level `planner/`, `state/`, `verification/`, `adc_shared/`, `data/`, `tests/`, `adc_rest.py`, `test_rest.py` are a close-to-verbatim drop-in of `pcb_agentic_inspector`, kept unformatted/untyped so diffs against upstream stay legible - see `app/workflow/INTEGRATION_NOTES.md` for what was changed, moved or deleted from the raw drop-in and why. They're excluded from ruff (`extend-exclude`) and mypy (`exclude`) in `pyproject.toml`. Don't reformat or "fix the types" in them; match their existing style instead. Only `app/workflow/api/` and `app/workflow/services/` (the thin FastAPI layer wired around this drop-in) are this repo's own code.

### Memory

Short-term: `Conversation`/`Message` rows in Postgres (`app/chat/db/models/chat.py`), replayed by `app/chat/services/history.py`. Long-term: extracted facts embedded into Docker Qdrant behind `MemoryStore` (`app/chat/memory/`). Both scoped per user.

### Frontend

Standalone Angular components with signals (no NgRx). Mirrors the backend split: `ui/src/app/chat/` (chat component plus its services, responders, models, `sidebar/`, `settings/`) `ui/src/app/work/` (work component, `work.service.ts`, `work-orchestrator-client.ts`, models) and `ui/src/app/models/` (the Models tab: `models.ts`, `model-ops.service.ts` state + polling while a retraining job is moving, `model-ops-client.ts` over `/api/models/*`; Admin-only buttons are hidden from QA but the backend enforces it); shared shell/auth/theme stay at the app root. `ChatResponder` (`chat/chat-responder.ts`) is the swap point between `HttpChatResponder` (real backend) and `MockChatResponder` (test double); responders stream chunks. A tool's structured result (inspection, relabel) arrives as a `toolResult` event and renders as a card on the assistant message (`chat/tool-result-card/`). Routes: `/` redirects to `/work`; `/chat`, `/c/:id`, `/work`, `/models` are behind `authGuard`; the mode toggle (`mode-toggle/`) switches between Chat | Work | Models. Tailwind v4 via PostCSS.

### Infra

`infra/Dockerfile` is the single backend image for dev and prod (its `CMD` uses `.venv/bin/uvicorn` directly, not `uv run`, to avoid re-syncing at container start). Production is Terraform on AWS (`infra/production/`): one CloudFront distribution serves both the S3 UI and `/api/*` (don't add a second distribution or point the UI at the ALB), with LiteLLM and inference reachable only over Cloud Map private DNS.

## Workflow conventions

- Branch from `dev` and open PRs against `dev`; `main` is production and only advances via a deliberate `dev` -> `main` PR. Conventional Commits (`feat:`, `fix:`, `chore:`, `ci:`, `docs:`, `infra:`) are required.
- User-visible changes need a `CHANGELOG.md` `[Unreleased]` entry; version bumps (`pyproject.toml`, `ui/package.json`) are semver and independent per package.
- New behavior needs a test.
