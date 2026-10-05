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
  - `app/workflow/` - the Work tab: `src/agent1_orchestrator/` and `src/agent2_explainability/`
    are a close-to-verbatim drop-in of `pcb_agentic_inspector` (see
    `app/workflow/INTEGRATION_NOTES.md`); `app/workflow/api/` and `app/workflow/services/` are
    this repo's own thin FastAPI layer (SSE run streaming, upload glue, monitoring routes) around
    it. Owns no tables.
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
- **LLM providers** (`app/shared/config/llm.py`): every chat model - the supervisor's, memory's fact
  extraction, the inspect agent's ReAct pass - is a LangChain chat model built by
  `build_chat_model()`: `"ollama"` (`langchain-ollama`, a local server at `OLLAMA_BASE_URL`) or
  `"openai"`. That factory is the swap point for another provider. OpenAI uses a single server-side
  key (`settings.openai_api_key`, set via `OPENAI_API_KEY`) - no per-request bring-your-own-key; the
  UI's Settings panel only lets a user pick Ollama vs OpenAI, not supply a key. Every
  OpenAI-compatible call (chat, memory embeddings, the agents) goes to `settings.openai_base_url`
  (`OPENAI_BASE_URL`), which is a LiteLLM proxy in every real setup, not `api.openai.com` - see
  `infra/litellm/README.md`. `OPENAI_API_KEY` is then a LiteLLM key. Memory's embeddings are the one
  thing still called directly over HTTP (`app/chat/memory/embeddings.py`).
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
- **Agent tool-calling in chat**: `POST /api/chat/stream`'s `chat_sse` (`app/chat/services/streaming.py`)
  handles everything around the model - `/remember`, guardrails, memory, persistence - and hands the
  reply to the **supervisor** (`app/chat/agents/supervisor.py`): a LangGraph agent (LangChain
  `create_agent`) over the tools this request may use, streamed back as plain events that `chat_sse`
  turns into SSE frames. `CHAT_TOOL_CALLING_ENABLED` is the kill switch (no tools at all);
  `CHAT_TOOL_MAX_ROUNDS` bounds the rounds (LangGraph's recursion limit); `CHAT_LLM_TIMEOUT_SECONDS`
  bounds one model call. The supervisor picks among the three agents' tools (below) and writes the
  answer. Tools registered: `inspect_image` (only offered when the message has an attached image,
  since the model has no way to reference a real upload id itself), `relabel_case` /
  `confirm_relabel`, and the monitoring tools `get_drift_summary`, `report_model_drift`,
  `draft_retraining_plan` and `monitoring_status` (Admin only) - none of these need an image; they
  work from a case number or the conversation's latest case. Every tool is role-gated in
  `access.py`, and only the tools a request may use are given to the model at all, so any other
  call cannot run.
  A tool is a LangChain `@tool` (`app/chat/agents/toolkit.py`) with an injected `ToolContext`
  (session, user, conversation, upload ids) the model never sees - see CLAUDE.md for how to add
  one. Just before a tool runs, `chat_sse` emits an `event: tool_call` frame (`{name, label}`) so
  the UI can show "Calling Image inspection..."; after it, for the tools that declare a card
  (`inspect_image`, `relabel_case`, `confirm_relabel`), an `event: tool_result` frame carrying the
  tool's structured result, which the UI shows as a card (`ui/src/app/chat/tool-result-card/`).
  Neither is persisted to history. A model failure reaches the client as a fixed "assistant is
  unavailable" error frame - the upstream error is logged, never forwarded.
- **Inspect agent** (`app/chat/agents/inspection_agent/`): `inspect_image` - a QA/Admin attaches an
  image and gets the inference result back (and as a card). Built from sub-agents, each callable on
  its own: `verifier.py` (is the image readable; validate an attached inspection XML's
  measurements), `classifier.py` (region model, then the defect model for that region, through the
  `inference/` microservice - `app.shared.inference.classify()`), `verdict.py` (pure rules ->
  ACCEPTED / REVIEW_REQUIRED plus every reason that applies) and `pipeline.py`, which runs them and
  persists a `Case` stamped with the model versions that answered. On top sits an LLM-driven ReAct
  pass (`react.py`, LangChain `create_agent` on LangGraph): the inspection steps are its tools, it
  chooses the order and writes a short summary. Every step it asks for passes `policy.py` - the same
  step-order rules the deterministic path uses - so an out-of-order call is refused with the reason.
  **The LLM never decides the verdict or saves anything**: after the pass, `pipeline._complete` runs
  any step it skipped, `verdict.decide` applies the fixed rules, and the Case is written, so a
  failed or confused LLM run only costs time (and with no key, or `INSPECTION_AGENT_LLM_ENABLED`
  off, the pipeline simply runs unassisted). An inference-service outage is an error with no Case
  rather than a case parked in review. `ADC_INSPECTION_AGENT_ENABLED` is the kill switch;
  `ADC_REGION_CONFIDENCE_THRESHOLD` / `ADC_DEFECT_CONFIDENCE_THRESHOLD` tune the gates. The golden-image
  alignment check of the previous design is no longer part of inspection (the golden-image bank and
  its admin upload route remain, unused by chat).
- **Relabel agent** (`app/chat/agents/relabel_agent/`): the QA says the model's defect label on a
  Case is wrong and gives the right one. Two tools, because a relabel is two turns: `relabel_case`
  *proposes* (resolver finds the Case; the label validator checks the label against the model's real
  classes from the inference service - fails closed if it cannot - and returns the model's own
  spelling; the proposal is parked on the Case in `pending_*`), and `confirm_relabel` *commits* it
  (the recorder sets `corrected_label` and who/when/why on the Case - the model's own `defect_label`
  is never overwritten - and queues the `RetrainingTicket`, atomically). The confirm step is
  **enforced in code**, not by the prompt: a proposal can only be committed in a *later* chat turn than
  the one that proposed it, by the same user (`streaming.py` injects when the turn began;
  `service.py` compares it with the proposal's timestamp). `RELABEL_AGENT_ENABLED` is the kill
  switch. Corrections also feed drift (`correction_rate`, below).
- **Tracing** (`app/shared/config/langfuse.py`): with `LANGFUSE_ENABLED` and keys set, the inspect
  agent's ReAct run is one trace (`inspect-react`, nested under `inspection`) with every model call and
  tool call under it, stamped with the user and conversation; relabel proposals and confirmations are
  spans too. Off, or with a blank key, it all degrades to no tracing. The agents' LLM is built in
  `app/shared/config/llm.py` from `AGENT_LLM_PROVIDER` / `AGENT_LLM_MODEL` (blank model -> `OPENAI_MODEL`),
  so changing the model - or provider, with its `langchain-<name>` package installed - is a setting.
  The supervisor's turn is traced too, with the user and conversation attached.
- **Orchestrator agent, Work tab** (`app/workflow/src/agent1_orchestrator/`): the *bulk* counterpart
  to the inspect agent above, and unrelated to it despite the shared origin - a
  close-to-verbatim drop-in of `pcb_agentic_inspector`'s Agent 1, a dataset workflow (CSV +
  inspection XML + image root; modes `prepare`, `prepare_verify`, `run_full`). Never a chat tool:
  `app/workflow/` has no route into the chat tool-calling loop, and `app/chat/` cannot import it.
  Served by `app/workflow/api/orchestrator.py` (QA/Admin only) as SSE at
  `POST /api/orchestrator/run/stream`, with its own uploads under `/api/orchestrator/uploads/*`
  (`ORCHESTRATOR_DATA_DIR`, `ORCHESTRATOR_AGENT_ENABLED` kill switch). Unlike the old hand-port
  this replaced, `OrchestratorAgent.run()` is synchronous and returns a `WorkflowState`, not an
  async generator - `app/workflow/services/streaming.py` runs it via `asyncio.to_thread` and
  streams progress live through one additive hook added to it (`on_step`, see
  `app/workflow/INTEGRATION_NOTES.md`). It is a close-to-verbatim drop-in kept unformatted and
  untyped on purpose so diffs against upstream stay legible, same as `agent2_explainability/`
  above - both are excluded from ruff and mypy (`exclude`) in `pyproject.toml`; don't "fix" them.
  Inspection-model calls go through the shared `app/shared/inference/` client (two files inside
  the drop-in, `services/{model_lifecycle,multimodal_inference}.py`, were adapted for this - the
  only two files in the drop-in that aren't verbatim besides the `on_step` hook).
- **Monitoring agent** (`app/chat/agents/monitoring_agent/`): the model-health tools.
  `get_drift_summary` compares a model's recent window with the one before (review rate, reviewer
  override rate, how often QA corrected the label, low-confidence share, mean confidence, per model
  version - `app/chat/services/drift.py`); `report_model_drift` files a drift report with that
  snapshot; `draft_retraining_plan` turns the model's open tickets (queued by the relabel agent)
  into a `RetrainingJob` in `pending_approval`. Nothing here retrains or promotes: an Admin approves
  a job (and promotes its result) in the Models tab, and the job runs on the separate inference
  server. `monitoring_status` is the Admin-only read-only overview. `MONITORING_AGENT_ENABLED` and
  `MODELOPS_ENABLED` both gate these tools.
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
  request/response payload (`app/shared/config/llm.py`'s `LlmDebugLogger`, including the tools offered) - gated behind an `isEnabledFor()` check so there's zero extra buffering when it's off
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
- **Chat tool-calling needs a tool-capable Ollama model**: not every Ollama model supports tools -
  check for a "tools" tag on ollama.com's model library (`ollama show <model>` lists its
  capabilities) before setting `OLLAMA_MODEL`, or chat will silently never call a tool (the model just
  answers directly, no error). `llama3.2` and `gemma4:12b` do. `CHAT_TOOL_MAX_ROUNDS` (default 4)
  caps how many tool-call rounds one message can trigger before the user gets an apology instead of
  an answer, in case a model keeps calling tools without ever finishing.

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
