# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Angular chat UI in the style of ChatGPT: a left history panel and a main chat panel, with text
  messages, image attachments, and a light/dark theme toggle that persists per browser. The theme
  toggle sits in the top-right corner of every page, including the login and registration pages.
- Chat history and theme preference persisted to the browser's local storage.
- FastAPI backend: chat replies stream to the UI over Server-Sent Events instead of arriving as
  one blocking response, and a separate endpoint handles image uploads for chat attachments.
- A mocked chat responder is kept as a fallback/test double; the UI talks to the real backend by
  default.
- LLM provider selection in a new Settings panel: a local Ollama model, or OpenAI using a single
  server-side key (`OPENAI_API_KEY`) the server operator configures - no per-request key entered
  in the browser.
- Local development environment under `infra/development/`: a Docker Compose stack (Postgres and
  Qdrant, both provisioned ahead of need for planned future work) plus per-OS setup scripts.
- Production infrastructure under `infra/production/` (Terraform): ECS Fargate, RDS, ECR, and
  S3 with CloudFront for the AWS deployment, once that work is picked up.
- User accounts: register, log in (with a username, not an email), and log out, with role-based
  access (QA, Admin) - both are selectable on the public registration form.
  Sessions use short-lived JWT access tokens plus a rotating, revocable refresh token, both in
  httpOnly cookies. The very first account ever created becomes Admin automatically regardless of
  what was requested, as a safety net; `scripts/create_admin_user.py` can also create or promote
  any account to Admin from the CLI. The chat itself now requires being logged in.
- Chat now remembers the conversation so far: replies take prior turns in the same conversation
  into account instead of treating every message as a one-off. Conversations and their messages
  are persisted server-side and scoped per account, so they survive a reload and follow you to
  any device you're logged in on, instead of living only in that browser's local storage.
- Long-term memory: the assistant can recall durable facts about you (stated preferences,
  ongoing context) across separate conversations, not just within one. Say `/remember <text>` in
  chat to save something explicitly, or let it pick things up on its own as you chat. Backed by
  Qdrant; disable with `MEMORY_ENABLED=False` if it ever needs to be turned off without a deploy.
- Explainability & Review Agent: `POST /api/agents/explainability-review` diagnoses a PCB
  component defect from an inspection image, combining GPT-4o visual evidence, historical defect
  precedents, IPC-A-610 standards, and AOI/ICT telemetry into a grounded root-cause explanation.
  Uses the same server-side `OPENAI_API_KEY` as chat; disable with
  `EXPLAINABILITY_AGENT_ENABLED=False`.
- Chat can now call tools mid-conversation instead of only answering from what it already knows:
  the current time, live weather for a named location (a new `get_weather` tool, via Open-Meteo -
  no API key needed), and the Explainability & Review Agent above (only offered when you've
  attached an image to the message). Works with both the local Ollama and OpenAI providers.
  Disable with `CHAT_TOOL_CALLING_ENABLED=False`; `CHAT_TOOL_MAX_ROUNDS` caps how many tool calls
  one message can trigger before the assistant answers with what it has.
- Structured logging: `LOG_FORMAT=console` (default) gives a readable local terminal, or `json`
  for one parseable object per line in production, where ECS Fargate's `awslogs` log driver ships
  it straight to CloudWatch - no new infrastructure either way. A new per-request access log line
  (method, path, status, duration, and the caller's user id when authenticated) replaces having
  to piece that together from a raw traceback.
- Drag-and-drop image attachment: drop an image file anywhere on the chat window (not just via
  the paperclip button) to add it to the message you're composing - a highlighted drop zone
  appears while dragging. Dropping outside the chat window (e.g. on the sidebar) no longer
  navigates the browser away from the app.
- Verbose debug logging for troubleshooting, off by default: set `LOG_LEVEL=DEBUG` to log every
  API request/response body (with `password` redacted) and every LLM request/response payload
  sent to or received from Ollama/OpenAI, including the `tools` array and tool-call results. The
  live chat stream itself is never buffered for this, so `DEBUG` adds no latency to `/api/chat/stream`.
- SentinelChat branding: a logo (a shield enclosing a PCB-trace chip motif) and the browser tab
  title, replacing the Angular CLI's default scaffold title/favicon. Shown in the sidebar header
  and above the login/register forms.
- Backend availability notice: the UI now polls the API's `/health` endpoint in the background
  and, after two checks in a row fail to reach it, shows a banner across the top of the app
  telling users the server is having problems and to check back in a few minutes, with a "Retry
  now" button. The banner clears on its own once the backend responds again, and a check is also
  triggered when the browser regains its connection or the tab is refocused.
- ONNX inference service (`inference/`): a standalone FastAPI service that runs image
  classification models, one per request, chosen by the caller (`POST /classify` with `model`,
  `username`, and an image). Models and their preprocessing are declared in `inference/models.toml`
  and their ONNX files are pulled from Hugging Face and baked into the image at build time. Runs
  as its own ECS Fargate service (`infra/production/inference.tf`), reachable only from the
  backend over Cloud Map private DNS - no public route. `app/inference/` is the backend client
  (`INFERENCE_BASE_URL`), now called by the ADC Inspection Agent below. Registered models: the
  two-stage PCB ADC classifier (`pcb_region`, `pcb_body_defect`, `pcb_lead_defect`,
  `pcb_text_defect`).
- Weather Agent (`app/agents/weather_agent/`): the `get_weather` chat tool is now a small
  LangGraph pipeline instead of a single deterministic lookup - geocode, fetch current
  conditions plus a short forecast (still Open-Meteo, still no key), then an LLM-synthesized
  advisory that branches into a more cautious tone on a deterministic severe-weather signal
  (thunderstorm/heavy-precipitation WMO codes, or high wind). The advisory step is best-effort:
  it degrades to a templated summary, never an error, when `WEATHER_ADVISORY_ENABLED` is off or
  no OpenAI key is configured. Same tool name/shape as before, so nothing calling it changed.
- LiteLLM proxy (`infra/litellm/`): every OpenAI-compatible call (chat, memory embeddings, the
  Explainability & Review Agent) now goes through an OpenAI-compatible gateway
  (`OPENAI_BASE_URL`), so real provider keys stay off developer laptops and out of the backend
  task. Runs as a third ECS Fargate service (`infra/production/litellm.tf`), reachable only from
  the backend over Cloud Map private DNS; its config (`infra/litellm/config.prod.yaml`) is passed into
  the task definition, so a model or routing change is a `terraform apply` with no image build.
  Auth is master-key-only for now. Locally the proxy runs in Docker as part of the default dev
  stack; each developer supplies their own upstream OpenAI key via `LITELLM_OPENAI_API_KEY`
  (seen only by their local proxy container), while production uses one shared key in Secrets
  Manager.
- Optional file logging (`LOG_TO_FILE=True`, `app/config/logging_config.py`): writes the same
  lines already going to stdout to a rotating file (`LOG_DIR/app.log`, default `data/logs/`,
  10 MiB x 5 backups) as well, so past log lines can be inspected after the fact instead of only
  from a live terminal. Off by default; only host-visible for bare `uv run uvicorn`, same caveat
  as chat uploads.
- Time Agent (`app/agents/time_agent/`): the `current_time` chat tool is now a small LangGraph
  pipeline too - resolve an optional location to a timezone (UTC if none given, otherwise the
  same keyless Open-Meteo geocoding lookup the Weather Agent uses), then a deterministic
  business-hours branch. Unlike the other two agents, no LLM step at all - "what time is it" is
  fully structured, so there's nothing an LLM would add. Same tool name/shape as before, plus new
  optional `location` support and `timezone`/`day_of_week`/`utc_offset`/`is_business_hours`/
  `note` fields in the result.
- Orchestrator agent (`app/agents/adc_inspection_agent/`): the `create_case` chat tool runs a
  deterministic plan/policy loop over an uploaded PCB AOI image - looks up a matching golden
  reference image and checks phase-correlation alignment/quality against it, classifies the
  component region then the matching defect model for that region via the inference service
  above, optionally validates an attached inspection XML's measurements, and always persists the
  result as a reviewable Case with a case number (`CASE-000123`), whether the verdict is ACCEPTED
  or REVIEW_REQUIRED. When the verdict is REVIEW_REQUIRED, it automatically hands the case to the
  Explainability & Review Agent below and attaches its diagnosis to the case before persisting -
  no separate step required. `list_cases` and `review_case` list and resolve (approve/override)
  reviewable cases; golden reference images are registered one at a time via
  `POST /api/admin/golden-images` (Admin only). Only offered when an image is attached, same
  gating as `explainability_review`. Disable with `ADC_INSPECTION_AGENT_ENABLED=False`.
- Intent router (`app/agents/router_agent/`): chat now asks a clarifying question instead of
  guessing when a message doesn't clearly call for one tool over another - one LLM call picks the
  best-matching tool (or decides none is needed) with a confidence score ahead of the existing
  tool-calling loop; below `INTENT_ROUTER_CONFIDENCE_THRESHOLD` it asks the user for the missing
  detail instead of offering every tool to the model's own judgement. Fails open (offers every
  tool, no clarification) on any problem - no key configured, nothing to route among, upstream
  error. Disable with `INTENT_ROUTER_ENABLED=False`.
- Explainability & Review Agent's historical-case lookup is now a real CLIP embedding similarity
  search against the embedded Qdrant collection instead of a metadata filter, and its AOI/ICT
  telemetry now reads a case's actual attached inspection-XML measurements when available
  (falling back to the original mock only when no XML is attached at all), with a deterministic,
  physics-based reasoning fallback (laser height/side-overhang thresholds) for when the OpenAI
  reasoning call fails.
- New `investigate_case` chat tool (QA/Admin): runs the Explainability & Review Agent's pipeline
  against an existing Case by case number (e.g. "investigate CASE-000123"), resolving its stored
  image, inspection XML, and board/component fields automatically - no need to re-attach the
  image.
- New `flag_case_for_retraining` chat tool (QA/Admin, `app/agents/monitoring_agent/`): flags a
  Case as a bad model call and queues a retraining ticket for engineering, requiring an
  explanation of what looked wrong. Only queues the request - actual model retraining happens on
  the separate inference server, never in this app.
- The chat UI now shows which agent is currently running (e.g. "Calling Orchestrator Agent…",
  "Calling Case Review Agent…") instead of a generic "Thinking…" while a tool call is in
  flight, via a new `event: tool_call` SSE frame - purely a progress indicator, never persisted
  to conversation history.
- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md): a practical guide to what to type/attach in chat to
  trigger each capability (submitting an image, getting a diagnosis, investigating a case,
  reviewing, flagging for retraining), linked from `README.md`.
- New Explainability Review Agent (`app/agents/explainability_review_agent/`, Work-tab-only):
  ported as-is from a teammate's standalone `pcb_agentic_inspector` prototype's Agent 2 pipeline
  (`retrieve_precedents -> extract_telemetry -> inspect_visuals -> grounding_self_check`; local
  Ollama LLaVA for visual evidence, GPT-4o for grounding with a deterministic heuristic fallback).
  Never a chat tool - the Work tab's `orchestrator_agent` now escalates any REVIEW_REQUIRED sample
  to it in-process, attaching its diagnosis to that sample's result under `explainability_result`.
  `explainability_review_agent_enabled` is its kill switch.

### Changed

- The chat-facing Explainability & Review Agent is renamed to Case Review Agent
  (`app/agents/case_review_agent/`, was `explainability_review_agent/`) to free up that name for
  the new Work-tab agent above - its tools (`explainability_review`, `investigate_case`), routes,
  and behavior are unchanged.

- `ONBOARDING.md`: a step-by-step dev environment setup guide (prerequisites, the setup script,
  the one `.env` value to set, verification, per-section extras, troubleshooting, ports).
  `README.md` and `DEVELOPMENT.md` point at it.
- The backend no longer calls `api.openai.com` directly - it calls `settings.openai_base_url`
  (`OPENAI_BASE_URL`, default unchanged at OpenAI direct for a bare checkout). `OPENAI_API_KEY`
  is a LiteLLM key wherever a proxy is configured.
- `infra/development/docker-compose.yml` runs `db` + `qdrant` + `litellm` + `app` by default
  (local topology now matches production); `ui` and `inference` moved behind `--profile` flags
  so a developer only builds and runs their slice.
- `setup-dev.sh` / `setup-dev.ps1` now generate `JWT_SECRET_KEY` when it's blank, start `litellm`
  alongside `db`/`qdrant`, and no longer push Ollama as a default dependency.
- User roles simplified from four (QA, Operator, Admin, Engineer) to two (QA, Admin) - QA is the
  role for day-to-day use (inspecting, reviewing, flagging), Admin is a superset of QA plus
  configuration-only actions (registering golden images, infra/monitoring visibility).
- The one-shot, non-persisting `adc_inspection` chat tool is removed - `create_case` (see the
  Orchestrator agent entry above) is now the only way to submit an image for inspection through
  chat, and it always persists a reviewable Case.

### Fixed

- The chat sidebar no longer appears on the login and register pages.
- The local LiteLLM proxy silently ran with no upstream OpenAI key (every OpenAI call 401'd)
  whenever `docker compose -f infra/development/docker-compose.yml` was invoked without
  `--env-file .env` - Compose's project directory defaulted to the compose file's own directory,
  which has no `.env`, so `${LITELLM_OPENAI_API_KEY:-}` silently resolved empty. `setup-dev.sh`
  / `setup-dev.ps1` and every documented command now pass `--env-file .env`.
