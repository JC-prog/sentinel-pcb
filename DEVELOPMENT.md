# Development Guide

How to actually build in this repo: environment setup, branching/PR workflow, architecture at a
glance, and Definition of Done for a PR. `README.md` is the quick start; this is the longer
version for someone about to write code.

## 1. One-time setup

```bash
bash infra/development/scripts/unix/setup-dev.sh        # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1   # Windows
```

[`ONBOARDING.md`](ONBOARDING.md) is the exact step-by-step: software to install first, what the
script does, the one `.env` value you may need to set (`LITELLM_OPENAI_API_KEY`), how to run and
verify the app, per-section extras, and troubleshooting. Re-run the setup script any time after
pulling changes; it's idempotent.

Sanity check before writing anything:

```bash
uv run ruff check . && uv run mypy . && uv run pytest
cd ui && npx ng test --watch=false && npx ng build
```

## 2. Branching and PRs

- **`dev` is the trunk.** Create feature branches from `dev`, and open PRs against `dev` (not
  `main`, and not another feature branch).
- **`main` is production.** It only moves forward via a deliberate `dev` -> `main` PR when the
  accumulated work on `dev` is actually ready to ship. Don't propose merging a feature branch
  into `main` directly.
- Branch names: `feat/<short-description>`, `fix/<short-description>`, `chore/<short-description>`,
  `docs/<short-description>`, `infra/<short-description>` - matches the commit prefixes below.
- Commit messages and PR titles follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `chore:`, `ci:`, `docs:`, `infra:`) - `CHANGELOG.md` and any future
  auto-generated release notes depend on this being consistent.
- Opening a PR against `dev` picks up `.github/PULL_REQUEST_TEMPLATE.md` automatically. Fill in
  the test plan for real - don't leave checkboxes unchecked as a TODO.
- CI (`.github/workflows/ci.yml`) runs backend (ruff, mypy, pytest) and frontend (ng test, ng
  build) checks on every PR. Both must pass before merging.

## 3. Architecture at a glance

- **Module layout** (`app/`): three independent feature modules plus shared infrastructure, each
  with its own `api/` (routes only), `agents/`, `services/`, `core/` and `db/` where it needs them:
  - `app/shared/` - auth, config/settings/logging, the DB `Base` + engine/session + auth models
    (`User`, `RefreshToken`) + the model-operations tables (model versions, drift reports,
    retraining jobs and tickets, with their rules in `app/shared/modelops/`), the
    inference-service client, and the auth/health routes.
  - `app/chat/` - the chat SSE stream, the tool-calling agents (`app/chat/agents/`), long-term
    memory, chat uploads, and every chat-owned table (conversations, cases, golden images -
    `app/chat/db/`).
  - `app/workflow/` - the Work tab: `orchestrator_agent` and `explainability_review_agent`
    (`app/workflow/agents/`), plus the run-streaming/upload glue (`app/workflow/services/`).
    Owns no tables.
  - `app/modelops/` - the Models tab's API (`/api/models/*`): model versions, drift reports and the
    retraining queue (QA/Admin can read; only an Admin can approve or cancel a retraining job,
    promote a model version or roll back). It syncs versions and job progress from the inference
    service each time the tab loads and degrades to last-known data if the service is down
    (`MODELOPS_ENABLED` is the kill switch). Owns no tables.
  - `app/main.py` is only the composition root: it mounts `shared`, `chat`, `workflow` and `modelops`'s
    routers and imports each module's models so `create_all` sees them.

  **Rule: `chat` and `workflow` never import each other, and `shared` imports neither** -
  enforced by `tests/test_module_boundaries.py`, which also scans lazy in-function imports. Code
  both sides need goes in `shared`. `tests/` mirrors this layout (`tests/shared|chat|workflow/`).
- **Backend** (`app/`): FastAPI. `POST /api/chat/stream` streams the assistant's reply over
  Server-Sent Events (`event: delta` / `error` / `done`); `POST`/`GET /api/uploads` handles chat
  image uploads. Stateless by design - no per-request state is shared across instances, which
  matters once this runs as more than one ECS task.
- **LLM providers** (`app/chat/services/providers/`): `OllamaChatService` and `OpenAiChatService`, both
  behind the `get_chat_service()` factory in `app/chat/services/service.py` - that factory is the swap
  point for adding another provider later. OpenAI uses a single server-side key
  (`settings.openai_api_key`, set via `OPENAI_API_KEY`) - no per-request bring-your-own-key; the
  UI's Settings panel only lets a user pick Ollama vs OpenAI, not supply a key. Every
  OpenAI-compatible call (chat, memory embeddings, the explainability agent) goes to
  `settings.openai_base_url` (`OPENAI_BASE_URL`), which is a LiteLLM proxy in every real setup,
  not `api.openai.com` - see `infra/litellm/README.md`. `OPENAI_API_KEY` is then a LiteLLM key.
- **Frontend** (`ui/`): Angular, standalone components, signals for state (no NgRx/service
  subjects). `ChatResponder` (`ui/src/app/chat-responder.ts`) is the frontend's equivalent swap
  point - `HttpChatResponder` talks to the real backend, `MockChatResponder` is a fallback/test
  double. Both stream multiple chunks rather than returning one value; `ChatService` accumulates
  them into the assistant message as they arrive. Image attachments (`ui/src/app/chat/chat.ts`)
  can be added via the paperclip button or by dragging a file onto the chat window - both funnel
  through the same `addFiles()`/`pendingImages` signal, so nothing downstream (upload, preview,
  send) needs to know which one was used. `app.html`'s root wrapper guards against a drop that
  misses the chat window (e.g. lands on the sidebar) navigating the browser away from the SPA.
- **Memory**: two tiers, both server-side and scoped per account. Short-term (per-conversation)
  memory is `Conversation`/`Message` rows in Postgres (`app/chat/db/models/chat.py`), assembled back
  into context for each reply by `app/chat/services/history.py`. Long-term (cross-conversation) memory
  lives in Qdrant behind the `MemoryStore` interface (`app/chat/core/memory.py`) - `app/chat/memory/`
  extracts durable facts from a conversation and retrieves them for a new one; `MEMORY_ENABLED`
  is a kill switch, and `app/chat/memory/qdrant_store.py` is the only file that knows it's Qdrant, so
  swapping the store later doesn't touch the rest of the app.
- **Agent tool-calling in chat**: `app/chat/agents/registry.py`'s `Tool`/`ToolRegistry` scaffold now
  has a real, live caller - `POST /api/chat/stream`'s `chat_sse` (`app/chat/services/streaming.py`) sends
  `tool_registry.specs()` as `tools` to whichever provider is selected, and loops (bounded by
  `CHAT_TOOL_MAX_ROUNDS`) executing any tool calls the model requests via `call_tool()` before
  streaming a final answer. `CHAT_TOOL_CALLING_ENABLED` is the kill switch - disabling it sends no
  `tools` field at all, identical to the pre-tool-calling request shape. Tools registered:
  `current_time` and `get_weather` (generic, every role); `inspect_image` and `explainability_review`
  (both below, only offered when the chat message has an attached image, since the model has no
  way to reference a real upload id itself); `find_similar_cases`, `get_case`, `investigate_case`,
  `list_cases`, `review_case`, `flag_case_for_retraining`, `report_model_drift`,
  `get_drift_summary` and `draft_retraining_plan` (all below, need no image attached - they work
  from a case number or the conversation's latest case); `monitoring_status` (Admin only). Just before each tool call actually
  dispatches, `chat_sse` emits an `event: tool_call` SSE frame (`{name, label}`, `_TOOL_DISPLAY_LABELS`)
  purely so the UI can show "Calling Orchestrator Agent…" instead of a generic "Thinking…" -
  never persisted to conversation history.
  `ChatService.stream_with_tools()` (`app/chat/core/chat.py`) is the tool-aware method both providers
  implement, translating a provider-agnostic `ChatMessage` list to/from each API's own
  tool-calling wire format; the older `stream_reply()` is untouched and still used by
  `app/chat/memory/service.py`'s fact extraction, which never needs tools.
- **Intent router** (`app/chat/agents/router_agent/`): runs ahead of the tool-calling loop above, when
  `INTENT_ROUTER_ENABLED` is on and at least one tool is on offer. A one-node LangGraph pipeline
  makes one LLM call to pick the single best-matching tool (or decide none is needed) with a
  confidence score; below `INTENT_ROUTER_CONFIDENCE_THRESHOLD` it short-circuits the turn with a
  clarifying question instead of guessing. Fails open on any problem (no key, upstream error,
  nothing to route among) - same as every other kill-switchable piece here, a router outage
  degrades to "offer every tool, no clarification," never a broken chat turn.
  `tests/conftest.py` disables this by default across the whole suite (it makes its own sync
  OpenAI call that the usual `httpx.AsyncClient` mocking doesn't catch); re-enabled explicitly in
  `tests/agents/test_router_agent.py`.
- **Case Review Agent** (`app/chat/agents/case_agent/`, formerly `explainability_review_agent/`):
  a LangGraph pipeline (context retrieval -> visual evidence -> measurement evidence -> reasoning)
  that diagnoses a PCB defect from an inspection image, ported from a teammate's standalone
  prototype into the app's `Tool`/`ToolRegistry` pattern (`app/chat/core/tools.py`,
  `app/chat/agents/registry.py`). Two entry points: `explainability_review` (chat, or
  `POST /api/agents/explainability-review`) takes a raw uploaded image; `investigate_case` (chat
  only) takes a case number instead and resolves that Case's stored image/inspection XML/board/
  component fields itself - no re-upload needed. Also called in-process (not a chat tool call) by
  the ADC inspection agent's `escalate_review` step below, whenever a case's verdict is
  REVIEW_REQUIRED. Uses the same server-side `settings.openai_api_key` as chat - not a key of its
  own; `EXPLAINABILITY_AGENT_ENABLED` is its kill switch. Historical-case lookup (`mcp_client.py`'s
  `search_historical()`) is a real CLIP embedding similarity search against the embedded Qdrant
  collection (seeded by `scripts/explainability_agent/populate_qdrant.py`); AOI/ICT telemetry
  (`get_measurements()`) reads a case's actual attached inspection-XML measurements when available
  (reusing `app/chat/agents/inspection_agent/xml_measurements.py`'s parsing), falling back to a
  hardcoded mock only when no XML is attached at all; and the reasoning step falls back to a
  deterministic, physics-based self-check (laser-height/side-overhang thresholds, `graph.py`'s
  `_heuristic_self_check`) when the OpenAI reasoning call itself fails. The CLIP embedding model
  and embedded Qdrant collection are loaded lazily on first use, not at import time, to keep app
  startup and test runs fast. See "Known gotchas" below for the remaining stubs carried over from
  the original prototype (bounding-box detection, IPC standards lookup). Renamed from
  `explainability_review_agent` when a second, unrelated "explainability and review" agent (below)
  was ported in and took that name instead - this one is the chat-facing agent, that one is
  Work-tab-only.
- **Explainability Review Agent** (`app/workflow/agents/explainability_review_agent/`, Work-tab-only): ported
  as-is from a teammate's separate standalone prototype, `pcb_agentic_inspector`'s "Agent 2"
  (`src/agent2_explainability/pipeline/review_graph.py`) - a different LangGraph pipeline
  (`retrieve_precedents` -> `extract_telemetry` -> `inspect_visuals` -> `grounding_self_check`)
  from the Case Review Agent above, and NOT related to it despite the similar name. Never a chat
  tool - called in-process only by `orchestrator_agent`'s `_execute_inference` (`orchestrator.py`'s
  `_escalate_review` helper) for a Work-tab run's REVIEW_REQUIRED samples, attaching its diagnosis
  under `explainability_result` in that sample's result payload. Ported unchanged, including its
  own `config/agent2_config.yaml` (repo-root-relative, mirrors this file's own cwd-relative
  convention) and its direct `OPENAI_API_KEY` env var read (not routed through
  `settings.openai_api_key`/`settings.openai_base_url` the way every other agent here is) -
  `explainability_review_agent_enabled` is its kill switch, the one thing `orchestrator.py` needs
  from settings to gate the call without touching the ported module. Visual evidence comes from a
  local Ollama LLaVA VLM (`ollama pull llava`; base URL from the YAML config, defaults to
  `http://localhost:11434`); the grounding/self-check reasoning step calls OpenAI GPT-4o directly
  and falls back to a deterministic heuristic (laser-height/overhang thresholds) on any failure or
  missing key, same graceful-degradation shape as the Case Review Agent. Its precedent-retrieval
  node is a hardcoded mock in the source and was left that way - not this change's job to wire up
  real retrieval.
- **Weather agent** (`app/chat/agents/weather_agent/`): a small LangGraph pipeline - geocode ->
  fetch current conditions plus a short forecast -> an LLM-synthesized advisory - exposed to
  chat as the single `get_weather` tool (same name/shape as before, so nothing calling it had to
  change). The advisory step is a real branch, not just a label: a deterministic check (severe
  WMO codes or high wind, in `graph.py`) routes to a more cautious prompt/tone, not just a
  different flag value. Uses the shared `settings.openai_api_key`/`openai_model`/`openai_base_url`
  (the LiteLLM gateway, same as chat); the geocode and forecast calls (Open-Meteo) still need no
  key at all, and the advisory step itself degrades to a templated summary - never an error -
  when `WEATHER_ADVISORY_ENABLED` is off or no key is configured, same graceful-degradation
  stance as the rest of this codebase's agents.
- **Time agent** (`app/chat/agents/time_agent/`): a small LangGraph pipeline - resolve an optional
  location to a timezone (same Open-Meteo geocoding endpoint the Weather Agent uses; UTC if no
  location is given) -> compute the current time there -> a deterministic business-hours branch
  - exposed as the single `current_time` tool. Unlike the other two agents, this one has **no
  LLM step at all**: "what time is it" is fully structured, so there's nothing an LLM would add
  besides latency and cost. The branch is still real, not cosmetic - `business_hours`/
  `after_hours` produce a different `note`, decided by a plain weekday/hour check
  (`_BUSINESS_HOURS_START`/`_END` in `graph.py`). `tzdata` was added as a direct dependency as a
  portability safety net for `zoneinfo` - `python:3.12-slim` (this repo's Docker base) already
  has the system IANA database and works without it, but the Python docs recommend it explicitly
  since not every environment does (Windows, some minimal/Alpine images).
- **Inspection agent** (`app/chat/agents/inspection_agent/`, chat side): the `inspect_image` tool first
  runs an LLM-driven ReAct pass (`react.py` - the inspection steps as tools, each guarded by the
  `PolicyEngine`, ending in a short summary; skipped without an OpenAI key or with
  `INSPECTION_AGENT_LLM_ENABLED` off) and then a
  cyclic LangGraph pipeline that reproduces `orchestrator-agent/adc_agentic_project`'s
  Planner -> PolicyEngine -> execute -> replan loop for exactly one case, rather than a batch of
  dataset rows - `planner.py`'s `Planner` proposes the next step (deterministic only; a single
  case never has genuine step-order ambiguity, see its docstring), `policy_engine.py`'s
  `PolicyEngine` validates it before `graph.py` dispatches, and a rejection routes straight to
  `abort` (not back to the planner - a pure function asked again would propose the same rejected
  step forever). Step sequence: verify the image is readable -> look up a matching golden
  reference image (`golden_images.py`) -> validate an attached inspection XML's measurements, if
  one was attached (`xml_measurements.py`) -> check phase-correlation alignment/quality against
  the golden reference, if one was found (`verification.py`, ported from `orchestrator-agent`'s
  `verification/{image_alignment,image_quality}.py`, using numpy's FFT rather than adding an
  OpenCV dependency for two narrow checks) -> classify region then the matching defect model for
  that region via the `inference/` microservice (`app.shared.inference.client.classify()`, see
  `inference/models.toml`'s four `pcb_*` models) -> finalize a verdict (always from these fixed
  rules, never from the LLM; the pipeline also completes any step the LLM skipped) -> persist the
  result as a `Case` row (`app/chat/services/cases.py`) stamped with the model versions that
  answered, always, whether ACCEPTED or REVIEW_REQUIRED. REVIEW_REQUIRED is terminal: no other
  agent is called. The Case Agent's `list_cases` and `review_case` list and resolve
  (approve/override) reviewable cases, and `find_similar_cases` ranks earlier cases by shared
  defect, region, component, package and board (the latest case in the conversation if no case
  number is given). `ADC_INSPECTION_AGENT_ENABLED`
  is its kill switch; `ADC_REGION_CONFIDENCE_THRESHOLD`/`ADC_DEFECT_CONFIDENCE_THRESHOLD`/
  `ADC_ALIGNMENT_WARNING_SHIFT_PX`/`ADC_ALIGNMENT_FAIL_SHIFT_PX` tune its gates. The full
  batch/dataset ingestion mode from the source prototype (a CSV of many samples joined against an
  AOI machine's inspection XML) was deliberately not ported - there's no CSV row for a chat
  upload, and nothing in this app currently ingests one.
- **Orchestrator agent, Work tab** (`app/workflow/agents/orchestrator_agent/`): the *bulk* counterpart
  to the ADC inspection agent above, and unrelated to it despite the shared origin - a port of
  `orchestrator-agent/adc_agentic_project`'s dataset workflow (CSV + inspection XML + image root; modes
  `prepare`, `prepare_verify`, `run_full`). Never a chat tool: `app/workflow/` has no route into
  the chat tool-calling loop, and `app/chat/` cannot import it. Served by
  `app/workflow/api/orchestrator.py` (QA/Admin only) as SSE at `POST /api/orchestrator/run/stream`,
  with its own uploads under `/api/orchestrator/uploads/*` (`ORCHESTRATOR_DATA_DIR`,
  `ORCHESTRATOR_AGENT_ENABLED` kill switch). `run()` is an async generator; the sync planner and
  pandas/opencv work is wrapped in `asyncio.to_thread`. Like `explainability_review_agent`, it is a
  close port kept unformatted and untyped on purpose so diffs against upstream stay legible - both
  are excluded from ruff and have mypy `ignore_errors` in `pyproject.toml`; don't "fix" them.
  Inspection-model calls go through the shared `app/shared/inference/` client.
- **Monitoring agent** (`app/chat/agents/monitoring_agent/`): the model-health tools.
  `flag_case_for_retraining` (QA/Admin) queues a `RetrainingTicket` for a Case a reviewer believes
  the model got wrong, requiring a reason (it records the model version that made the call and,
  optionally, the correct label). `get_drift_summary` compares a model's recent window with the one
  before (review rate, reviewer override rate, low-confidence share, mean confidence, per model
  version - `app/chat/services/drift.py`); `report_model_drift` files a drift report with that
  snapshot; `draft_retraining_plan` turns the model's open tickets into a `RetrainingJob` in
  `pending_approval`. Nothing here retrains or promotes: an Admin approves a job (and promotes its
  result) in the Models tab, and the job runs on the separate inference server.
  `monitoring_status` is the Admin-only read-only overview. `MONITORING_AGENT_ENABLED` is the kill
  switch for all of them; `MODELOPS_ENABLED` additionally gates everything but flagging.
- **Logging** (`app/shared/config/logging_config.py`): `configure_logging()` runs once at import
  (`app/main.py`), configuring the root logger so every `logging.getLogger(__name__)` call
  app-wide is formatted consistently - `LOG_FORMAT=console` (default) for a readable local
  terminal, or `LOG_FORMAT=json` for one parseable object per line in production. Both write to
  stdout; ECS Fargate's `awslogs` log driver ships that straight to CloudWatch with no extra
  containers/infra. A request-logging middleware in `app/main.py` logs one `app.access` line per
  request (method, path, status, duration, and the caller's user id when authenticated) via
  `extra=`, which the JSON formatter surfaces as its own keys generically - any future
  `logger.info(..., extra={...})` call gets the same treatment, not just this one. `LOG_LEVEL=DEBUG`
  additionally logs every API request/response body (`password` redacted) and every LLM
  request/response payload (`app/chat/services/providers/`, including the `tools` array and tool-call
  results) - gated behind an `isEnabledFor()` check so there's zero extra buffering when it's off
  (default `INFO`). `/api/chat/stream`'s response is never buffered for this even at `DEBUG` -
  logging it there would delay the live SSE stream - it's logged separately, at the point
  `chat_sse` already assembles the final reply. `LOG_TO_FILE=True` additionally writes the same
  lines to a rotating file (`LOG_DIR/app.log`, default `data/logs/`, 10 MiB x 5 backups) - off by
  default, and only host-visible for bare `uv run uvicorn` (the containerized `app` service
  doesn't volume-mount `data/`, same caveat as `chat_upload_dir`).
- **Migrations**: `alembic/` - `uv run alembic revision --autogenerate -m "..."` after changing a
  model, then `uv run alembic upgrade head`. `app/shared/db/session.py`'s `create_all` still runs at
  startup for local/test convenience; a real deploy's schema is Alembic's migration history.
- **Infra** (`infra/`): `infra/Dockerfile` is the one backend image definition, used by both
  `infra/development/docker-compose.yml` (local dev) and the AWS deploy in `infra/production/`
  (Terraform - see its own README). The dev compose stack runs `db` + `qdrant` + `litellm` (a
  local LiteLLM proxy, so dev topology matches prod) + `app` by default; `ui`, `inference`, and
  `langfuse` (self-hosted LLM tracing - `app/shared/config/langfuse.py`) are opt-in via
  `--profile <name>` so you only build/run your slice.

## 4. Known gotchas

- **Ollama from inside a container**: the backend's default `OLLAMA_BASE_URL` assumes a
  host-native Ollama install. The dev Docker Compose stack points the containerized backend at
  `http://host.docker.internal:11434` instead - see the comment in
  `infra/development/docker-compose.yml` if this needs to change.
- **`uv run` vs the venv binary in Docker**: `infra/Dockerfile`'s `CMD` invokes `.venv/bin/uvicorn`
  directly, not `uv run uvicorn`. The latter re-syncs the environment (including dev-only
  packages) against the lockfile on every container start, which needs network access and adds
  startup latency - a real bug caught while building the infra, not a style preference.
- **No mixed content in production without a custom domain**: `infra/production/static_site.tf`
  routes both the UI and `/api/*` through one CloudFront distribution specifically so the ALB
  (which has no cert) is never called directly from the browser. Don't add a second, separate
  CloudFront distribution or point the UI at the ALB's own domain - see that file's comments.
- **Ollama models aren't auto-pulled - for either purpose**: installing Ollama itself isn't
  enough. `OLLAMA_MODEL` (default `llama3.2`) needs `ollama pull llama3.2` before the Local LLM
  chat option works at all, and separately `OLLAMA_EMBEDDING_MODEL` (default `nomic-embed-text`)
  needs `ollama pull nomic-embed-text` before long-term memory works - without it,
  extraction/retrieval silently no-ops (logged, not raised - see `app/chat/memory/service.py`) rather
  than erroring, which can look like "memory just isn't doing anything" with no obvious cause.
- **Chat tool-calling needs a tool-capable Ollama model**: `OLLAMA_MODEL`'s default (`llama3.2`)
  supports tool-calling, but not every Ollama model does - check for a "tools" tag on
  ollama.com's model library before swapping models, or chat will silently never call a tool
  (Ollama just answers directly, no error). Ollama also doesn't deliver tool-call data
  incrementally even with `stream: true` - the full `message.tool_calls` list only shows up on
  the final chunk - `app/chat/services/providers/ollama.py`'s `stream_with_tools()` accounts for this
  already, but it's worth knowing if you're debugging a hang-then-burst pattern. `CHAT_TOOL_MAX_ROUNDS`
  (default 4) caps how many tool-call round trips one message can trigger before the loop gives
  up and answers with what it has, in case a model keeps calling tools without ever finishing.
- **Case Review Agent still has two known stubs, faithfully ported rather than
  fixed**: `models.py`'s `BoundingBoxDetector` ("YOLO") always returns the same hardcoded bounding
  box, and `mcp_client.py`'s `get_standards()` is a hardcoded placeholder that doesn't actually
  read `data/ipc_standards/ipc_a_610_chip_components.json`. `get_measurements()` and
  `search_historical()` are no longer stubs (see the agent's own bullet above) - real telemetry
  and real similarity search. None of this blocks the pipeline from running end to end; it just
  means defect *location* and cited IPC clause text are still bounded by GPT-4o's reasoning over a
  fixed bounding box and mocked standards rather than real ones.
- **Case Review Agent data prep is a manual, admin-triggered step**: the agent needs
  PCB images under `data/images/inputs/` (`EXPLAINABILITY_AGENT_DATA_DIR`, gitignored - not
  committed; `data/images/ipc_standards/` in the same directory *is* committed, since that's a
  reference document rather than runtime data) before
  `scripts/explainability_agent/generate_telemetry.py` (synthetic AOI/ICT measurements) and
  `scripts/explainability_agent/populate_qdrant.py` (seeds the embedded Qdrant collection) have
  anything to process. Run both as modules (`uv run python -m
  scripts.explainability_agent.generate_telemetry`) after adding images - nothing in the app does
  this automatically, by design, so an administrator can re-run it on demand.

## 5. Definition of Done (per PR)

1. `uv run ruff check .`, `uv run mypy .`, `uv run pytest` all pass (backend changes).
2. `npx ng test --watch=false`, `npx ng build` both pass (frontend changes).
3. New behavior has a test - unit tests for logic, at minimum a manual verification note in the
   PR's test plan for anything that needs a real browser or a real external service (Ollama,
   OpenAI) to observe.
4. `CHANGELOG.md`'s `[Unreleased]` section is updated for anything a user of the app would
   notice. Version bumps (`pyproject.toml`, `ui/package.json`) follow semver independently per
   package - a new user-facing capability is a MINOR bump, infra/tooling-only changes usually
   don't need one.
