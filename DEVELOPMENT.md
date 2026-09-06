# Development Guide

How to actually build in this repo: environment setup, branching/PR workflow, architecture at a
glance, and Definition of Done for a PR. `README.md` is the quick start; this is the longer
version for someone about to write code.

## 1. One-time setup

```bash
bash infra/development/scripts/unix/setup-dev.sh        # macOS/Linux
powershell -File infra\development\scripts\windows\setup-dev.ps1   # Windows
```

See `README.md` for what this does, how to run the app afterward, and the full external
dependency list (Docker/Postgres/Qdrant auto-provisioned; Ollama models are not - see that
table before assuming the Local LLM option or long-term memory "just work"). Re-run the setup
script any time after pulling changes; it's idempotent.

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

- **Backend** (`app/`): FastAPI. `POST /api/chat/stream` streams the assistant's reply over
  Server-Sent Events (`event: delta` / `error` / `done`); `POST`/`GET /api/uploads` handles chat
  image uploads. Stateless by design - no per-request state is shared across instances, which
  matters once this runs as more than one ECS task.
- **LLM providers** (`app/chat/providers/`): `OllamaChatService` and `OpenAiChatService`, both
  behind the `get_chat_service()` factory in `app/chat/service.py` - that factory is the swap
  point for adding another provider later. OpenAI uses a single server-side key
  (`settings.openai_api_key`, set via `OPENAI_API_KEY`) - no per-request bring-your-own-key; the
  UI's Settings panel only lets a user pick Ollama vs OpenAI, not supply a key.
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
  memory is `Conversation`/`Message` rows in Postgres (`app/db/models/chat.py`), assembled back
  into context for each reply by `app/chat/history.py`. Long-term (cross-conversation) memory
  lives in Qdrant behind the `MemoryStore` interface (`app/core/memory.py`) - `app/memory/`
  extracts durable facts from a conversation and retrieves them for a new one; `MEMORY_ENABLED`
  is a kill switch, and `app/memory/qdrant_store.py` is the only file that knows it's Qdrant, so
  swapping the store later doesn't touch the rest of the app.
- **Agent tool-calling in chat**: `app/agents/registry.py`'s `Tool`/`ToolRegistry` scaffold now
  has a real, live caller - `POST /api/chat/stream`'s `_chat_sse` (`app/main.py`) sends
  `tool_registry.specs()` as `tools` to whichever provider is selected, and loops (bounded by
  `CHAT_TOOL_MAX_ROUNDS`) executing any tool calls the model requests via `call_tool()` before
  streaming a final answer. `CHAT_TOOL_CALLING_ENABLED` is the kill switch - disabling it sends no
  `tools` field at all, identical to the pre-tool-calling request shape. Three tools are
  registered: `current_time` (trivial), `get_weather` (`app/agents/weather_agent/`, Open-Meteo,
  no key needed), and `explainability_review` (below) - only offered to the model when the chat
  message has an attached image, since the model has no way to reference a real upload id itself.
  `ChatService.stream_with_tools()` (`app/core/chat.py`) is the tool-aware method both providers
  implement, translating a provider-agnostic `ChatMessage` list to/from each API's own
  tool-calling wire format; the older `stream_reply()` is untouched and still used by
  `app/memory/service.py`'s fact extraction, which never needs tools.
- **Explainability & Review Agent** (`app/agents/explainability_review_agent/`): a LangGraph
  pipeline (context retrieval -> visual evidence -> measurement evidence -> reasoning) that
  diagnoses a PCB defect from an inspection image, ported from a teammate's standalone prototype
  into the app's `Tool`/`ToolRegistry` pattern (`app/core/tools.py`, `app/agents/registry.py`).
  Callable directly via `POST /api/agents/explainability-review`, or through chat (above) when an
  image is attached to the message. Uses the same server-side `settings.openai_api_key` as chat -
  not a key of its own; `EXPLAINABILITY_AGENT_ENABLED` is its kill switch. The CLIP embedding
  model and embedded Qdrant collection it uses for historical-case lookup are loaded lazily on
  first use, not at import time, to keep app startup
  and test runs fast. See "Known gotchas" below for gaps carried over from the original prototype.
- **Logging** (`app/config/logging_config.py`): `configure_logging()` runs once at import
  (`app/main.py`), configuring the root logger so every `logging.getLogger(__name__)` call
  app-wide is formatted consistently - `LOG_FORMAT=console` (default) for a readable local
  terminal, or `LOG_FORMAT=json` for one parseable object per line in production. Both write to
  stdout; ECS Fargate's `awslogs` log driver ships that straight to CloudWatch with no extra
  containers/infra. A request-logging middleware in `app/main.py` logs one `app.access` line per
  request (method, path, status, duration, and the caller's user id when authenticated) via
  `extra=`, which the JSON formatter surfaces as its own keys generically - any future
  `logger.info(..., extra={...})` call gets the same treatment, not just this one. `LOG_LEVEL=DEBUG`
  additionally logs every API request/response body (`password` redacted) and every LLM
  request/response payload (`app/chat/providers/`, including the `tools` array and tool-call
  results) - gated behind an `isEnabledFor()` check so there's zero extra buffering when it's off
  (default `INFO`). `/api/chat/stream`'s response is never buffered for this even at `DEBUG` -
  logging it there would delay the live SSE stream - it's logged separately, at the point
  `_chat_sse` already assembles the final reply.
- **Migrations**: `alembic/` - `uv run alembic revision --autogenerate -m "..."` after changing a
  model, then `uv run alembic upgrade head`. `app/db/session.py`'s `create_all` still runs at
  startup for local/test convenience; a real deploy's schema is Alembic's migration history.
- **Infra** (`infra/`): `infra/Dockerfile` is the one backend image definition, used by both
  `infra/development/docker-compose.yml` (local dev) and the AWS deploy in `infra/production/`
  (Terraform - see its own README).

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
  extraction/retrieval silently no-ops (logged, not raised - see `app/memory/service.py`) rather
  than erroring, which can look like "memory just isn't doing anything" with no obvious cause.
- **Chat tool-calling needs a tool-capable Ollama model**: `OLLAMA_MODEL`'s default (`llama3.2`)
  supports tool-calling, but not every Ollama model does - check for a "tools" tag on
  ollama.com's model library before swapping models, or chat will silently never call a tool
  (Ollama just answers directly, no error). Ollama also doesn't deliver tool-call data
  incrementally even with `stream: true` - the full `message.tool_calls` list only shows up on
  the final chunk - `app/chat/providers/ollama.py`'s `stream_with_tools()` accounts for this
  already, but it's worth knowing if you're debugging a hang-then-burst pattern. `CHAT_TOOL_MAX_ROUNDS`
  (default 4) caps how many tool-call round trips one message can trigger before the loop gives
  up and answers with what it has, in case a model keeps calling tools without ever finishing.
- **Explainability & Review Agent has known stubs, faithfully ported rather than fixed**:
  `models.py`'s `BoundingBoxDetector` ("YOLO") always returns the same hardcoded bounding box, and
  `mcp_client.py`'s `get_standards()`/`get_measurements()` are hardcoded placeholders that don't
  actually read `data/ipc_standards/ipc_a_610_chip_components.json` or the telemetry file the
  scripts below generate. `search_historical()` does a Qdrant metadata filter, not an embedding
  similarity search - the CLIP encoder it loads is real (and needed so
  `scripts/explainability_agent/populate_qdrant.py` can embed images with the same model at seed
  time) but isn't queried by that method yet. None of this blocks the pipeline from running end to
  end; it just means the diagnosis quality is currently bounded by GPT-4o's reasoning over mocked
  standards/telemetry rather than real ones.
- **Explainability & Review Agent data prep is a manual, admin-triggered step**: the agent needs
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
