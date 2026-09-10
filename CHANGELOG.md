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
  access (QA, Operator, Admin) - all three are selectable on the public registration form.
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
  (`INFERENCE_BASE_URL`); nothing calls it yet, wiring it into the Explainability & Review Agent
  is the next step.
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

### Changed

- `ONBOARDING.md`: a step-by-step dev environment setup guide (prerequisites, the setup script,
  the one `.env` value to set, verification, per-section extras, troubleshooting, ports).
  `README.md` and `DEVELOPMENT.md` point at it.
- The backend no longer calls `api.openai.com` directly - it calls `settings.openai_base_url`
  (`OPENAI_BASE_URL`, default unchanged at OpenAI direct for a bare checkout). `OPENAI_API_KEY`
  is a LiteLLM key wherever a proxy is configured.
- `infra/development/docker-compose.yml` runs `db` + `qdrant` + `app` by default; `ui`,
  `offline-llm`, and `inference` moved behind `--profile` flags so a developer only builds and
  runs their slice.
- `setup-dev.sh` / `setup-dev.ps1` now generate `JWT_SECRET_KEY` when it's blank and no longer
  push Ollama as a default dependency.

### Fixed

- The chat sidebar no longer appears on the login and register pages.
