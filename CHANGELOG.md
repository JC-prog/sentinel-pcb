# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Angular chat UI in the style of ChatGPT: a left history panel and a main chat panel, with text
  messages, image attachments, and a light/dark theme toggle that persists per browser.
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
