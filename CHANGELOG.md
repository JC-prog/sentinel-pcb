# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Chat: the **sample agent** (`app/chat/agents/sample_agent/`). Give it a dataset `sample_id` such as
  `S000001` and `get_sample` reports what the Work tab stored in Qdrant - board and component, the
  machine's call and failed measurements, Agent 1's verdict, Agent 2's review and the operator's
  decision - and `list_review_cases` lists what a run flagged for review. Read-only (a sample is not a
  Case); a sample found in several runs answers from the latest and names the others. New
  `SAMPLE_LOOKUP_AGENT_ENABLED` kill switch.
- Chat: a `create_case` tool. After an image inspection the assistant asks whether you want a case
  created, and makes one only after you say yes in your next message (enforced in code, like relabel
  and review). New `inspection_drafts` table (Alembic `b5c1f8a3d742`) holds the inspection meanwhile.
- Work tab: a finished run, its Agent 2 reviews and the operator's decisions are now persisted to
  Qdrant (the source project's `adc_orchestrator_runs` / `adc_inspection_results` /
  `adc_agent2_reviews` collections, via `app/workflow/services/run_store.py`), so the Review Console
  still lists a run's cases, reviews and decisions after a backend restart. The first Agent 2 review
  of a sample is immutable (asking again returns it). If Qdrant is unreachable the run still works
  from memory and decisions fall back to Postgres.
- Chat: an always-present system prompt that says what the assistant is for and which tool answers
  which question, and asks for confirmation before tools that change something.
- Chat: the **relabel agent** (`app/chat/agents/relabel_agent/`). A QA/Admin says the model's defect
  label on a Case is wrong and gives the right one; `relabel_case` proposes it (the label is checked
  against the model's real classes from the inference service) and `confirm_relabel` commits it - which
  records the correction on the Case (the model's own label is kept) and queues a retraining ticket.
  The confirmation is enforced in code: a proposal can only be committed in a later chat turn than the
  one that proposed it. New `cases.corrected_*` / `pending_*` columns (Alembic `e8b3d1f5a2c7`) and a
  `RELABEL_AGENT_ENABLED` kill switch.
- Chat: the user now sees the inference result as a **card** (case number, verdict and reasons, region
  and defect with confidences, runner-up scores; relabel proposals and confirmations too), sent as a new
  `event: tool_result` SSE frame and rendered by `ui/src/app/chat/tool-result-card/`.
- Drift: `get_drift_summary` also reports how often reviewers corrected a case's label
  (`corrected`, `correction_rate`) and calls out a jump in it.
- Tracing: Langfuse now traces the chat agents - an inspection is one trace with every model call and
  tool call nested under it, attributed to the user and conversation. `LANGFUSE_BASE_URL` is accepted
  as an alias for `LANGFUSE_HOST`.
- The agents' LLM is built in one place (`app/shared/config/llm.py`) from `AGENT_LLM_PROVIDER` /
  `AGENT_LLM_MODEL`, so changing the model or provider is a setting.
- Every chat model - the chat supervisor's, memory's fact extraction and the inspect agent's - is now a
  LangChain model built in one place (`app/shared/config/llm.py`): `build_chat_model("ollama"|"openai")`
  for a conversation, and `AGENT_LLM_PROVIDER` / `AGENT_LLM_MODEL` for the agents' own, so changing the
  model or provider is a setting. Adds `langchain-ollama` and a `CHAT_LLM_TIMEOUT_SECONDS` setting.
- Chat: the reply is now produced by a **supervisor** (`app/chat/agents/supervisor.py`), a LangGraph
  agent over the tools the request may use, replacing the hand-written Ollama/OpenAI streaming layer
  (`app/chat/services/providers/`, removed). Tools are LangChain `@tool`s (`app/chat/agents/toolkit.py`)
  that receive the user, session and uploads through an injected context the model never sees. Every chat
  turn is traced in Langfuse with its user and conversation. A model failure now reaches the client as a
  fixed "assistant is unavailable" error rather than the upstream error text.
- Inspection agent: two read-only tools for the LLM pass, `get_scores` (ranked per-label
  classifier scores, to report runner-up labels) and `check_image_quality` (size/brightness/
  contrast/blur without needing a golden reference). Neither touches state the verdict reads.
- Work tab (`app/workflow/`): Agent 2 review and a human-in-the-loop Review Console, from the
  teammate's updated `pcb_agentic_inspector`. When a full run finishes, every REVIEW_REQUIRED sample
  is sent to Agent 2 automatically (live diagnosis lines in the log), and the new Review Console
  lists them (opened from an "Open Review Console" popup button) with the golden and defect images,
  both verdicts and Agent 2's explanation. The
  operator records the final call - Machine, AI or a manual IPC class, with notes - saved to a new
  `workflow_review_decisions` table (`GET .../reviews/cases`, `GET .../reviews/image`,
  `PUT .../reviews/decision`; Alembic migration `d7e2a4b9c015`). QA/Admin only, gated by
  `ORCHESTRATOR_AGENT_ENABLED` and `EXPLAINABILITY_REVIEW_AGENT_ENABLED`, never a chat tool. Agent 2's
  inputs are kept server-side per run, so a backend restart requires rerunning the dataset to review
  its samples. See `app/workflow/INTEGRATION_NOTES.md`.

### Changed

- Chat: `inspect_image` no longer saves a Case on its own - it reports the result and the assistant
  asks whether to create one (see `create_case`). The inspection card shows "Not saved as a case".
  Drift numbers are computed from saved Cases, so they now cover the inspections users chose to keep
  as cases rather than every inspection.
- Chat agents rebuilt around three: an **inspect agent** (verifier and classifier sub-agents, fixed
  verdict rules, and an LLM-driven ReAct pass on LangChain `create_agent`), the new relabel agent, and
  the monitoring agent. The LLM never decides the verdict or saves anything; an inference-service outage
  is now an error with no Case rather than a case parked in review, and a low-confidence region with an
  unrouted label goes to review instead of aborting. The inspect agent no longer does the golden-image
  alignment check. `flag_case_for_retraining` is replaced by the relabel flow. The chat system prompt,
  tool-call labels and suggestion chips describe the new tools.
- Work tab (`app/workflow/`): replaced the hand-adapted `orchestrator_agent`/
  `explainability_review_agent` port with a close-to-verbatim drop-in of the upstream
  `pcb_agentic_inspector` project (`app/workflow/src/`), rebuilding `app/workflow/api/` and
  `app/workflow/services/` as a thin FastAPI layer around its actual `OrchestratorAgent`/
  `DatasetPreparationService`/`DatasetVerificationService`. Same routes, same request/response
  shapes, same Angular UI - see `app/workflow/INTEGRATION_NOTES.md` for exactly what was moved,
  adapted, or deleted, and why. Adds a live-progress hook to the drop-in's `OrchestratorAgent.run()`
  (`on_step`, additive and optional) so the Work tab's Execution Log streams a run's plan steps as
  they happen again, instead of only showing the outcome once the run finishes. Model serving now
  goes through the `inference/` microservice (`inference_base_url`) instead of loading local ONNX
  files, consistent with every other agent in this repo. Sample images/datasets used to smoke-test
  the drop-in moved from `app/workflow/{data,sample_data}/` to `data/workflow/`.
- The sidebar (branding, Settings, user info, log out) now shows on the Work and Models tabs too,
  not just Chat - previously logging out was only reachable from Chat. Its "New chat" button and
  conversation history stay chat-only (`Sidebar.showChatNav`), since Work/Models have their own
  controls and no conversation history to show.
- Work tab: a full run now indexes its prepared samples into this app's own Docker Qdrant
  (`QDRANT_URL`) instead of a local, throwaway embedded store nothing else could read - same
  container chat's long-term memory uses, its own `ipc_defect_precedents` collection. See
  `app/workflow/INTEGRATION_NOTES.md`'s "vector-db indexing" section.

### Removed

- Chat: the weather and time agents (`get_weather`, `current_time`), the intent router, and the case
  agent - `find_similar_cases`, `get_case`, `list_cases`, `review_case`, the heavy diagnosis pipeline
  (`explainability_review`, `investigate_case`, `POST /api/agents/explainability-review`, its CLIP /
  embedded-Qdrant seed scripts) - along with the `INTENT_ROUTER_*`, `EXPLAINABILITY_AGENT_*` and
  `WEATHER_ADVISORY_ENABLED` settings and the alignment-threshold settings.

### Added

- Work tab: once a bulk run finishes, QA/Admin can file a drift report for the run's model and flag
  selected samples as retraining tickets, straight from the run's results - using the same shared
  model-operations tables the chat monitoring agent and Models tab already use
  (`POST /api/orchestrator/monitoring/{drift-report,retraining-tickets}`, requiring both
  `ORCHESTRATOR_AGENT_ENABLED` and `MODELOPS_ENABLED`). `RetrainingTicket.case_id` is now optional; a
  ticket may instead carry a Work-tab `sample_ref` when it has no chat Case behind it. Includes a
  database migration.
- New **Models** tab beside Chat and Work: the live version of each model and its version history,
  drift reports people have filed (with the numbers behind them), and the retraining queue with each
  job's progress and the cases it was drafted from. Admins can approve or cancel a retraining plan,
  make a version live, roll back and resolve drift reports; QA can read everything and withdraw a
  plan they drafted. It refreshes while a job is running and says so when the inference service
  can't be reached. A run of the placeholder trainer is labelled as simulated.
- Backend for a new Models tab (`/api/models/*`): what version of each model is live (and its
  history), drift reports people have filed, and the retraining queue with each job's progress. QA
  and Admin can read it; only an Admin can approve a retraining job (which sends it to the inference
  service), cancel one, promote a model version, or roll back - a QA user can also withdraw a plan
  they drafted while it is still pending. It syncs with the inference service each time it is read
  and shows last-known data, flagged, if the service is down. Disable with `MODELOPS_ENABLED=False`.
- Chat: ask about what an uploaded image shows and the inspection agent now works through the steps
  with an LLM (and writes a short summary), falling back to the fixed pipeline if no OpenAI key is
  set or the LLM fails - the ACCEPTED/REVIEW_REQUIRED verdict is always decided by the same fixed
  rules. Board and component are no longer required to inspect an image. Disable just the LLM part
  with `INSPECTION_AGENT_LLM_ENABLED=False`.
- Chat: ask for cases similar to the current defect (`find_similar_cases` - ranked by shared defect,
  region, component, package and board, using the latest case in the conversation by default) and
  look up a full case record (`get_case`).
- Chat: report that a model has drifted (`report_model_drift`), check drift indicators
  (`get_drift_summary`), flag cases with the correct label, and draft a retraining plan
  (`draft_retraining_plan`) that waits for an Admin's approval. `monitoring_status` (Admin) now
  reports live model versions, open drift reports and tickets, and the retraining queue.
- Model operations groundwork: the app now keeps a registry of model versions (one live per model,
  enforced by the database), drift reports, and a retraining-job queue with an approval step - an
  Admin must approve a drafted job before anything is sent for retraining. Every new Case records
  which model versions produced its region and defect verdicts, and retraining tickets record the
  version and (optionally) the correct label. Adds the `modelops_enabled` setting and a database
  migration - on a database created before migrations were used, run
  `uv run alembic stamp ea8ea3053c3c` once, then `uv run alembic upgrade head`.
- Inference service: every model now reports a `version` (`<repo_id>@<revision>`), `/classify`
  returns the `model_version` that produced each verdict, and a new version can be hot-swapped in
  (`POST /models/{name}/activate`) or rolled back (`.../rollback`) without rebuilding the image.
  It also accepts retraining jobs (`POST /jobs`, `GET /jobs/{id}`, `.../cancel`) behind a pluggable
  trainer; only a simulated stub trainer ships so far - it exercises the queue end to end but
  produces no new model.
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

- The chat tool `create_case` is now `inspect_image` (its board/component arguments are optional),
  and `list_cases` / `review_case` moved to the case agent. The tool-call label shown in the chat
  is now "Inspection Agent" / "Case Agent" / "Monitoring Agent".
- Chat agents are now mutually independent. The inspection agent no longer hands REVIEW_REQUIRED
  cases to the case review agent automatically (the case is persisted with the classifier verdict;
  ask for a deeper diagnosis by case number), and the case lookup / inspection-XML helpers moved
  out of the inspection agent into `app/chat/services/`. Internal packages were renamed to
  `inspection_agent` and `case_agent`. A new test fails if one chat agent imports another.
- Internal restructure, no user-visible change: the backend is split into independent `app/chat/`
  and `app/workflow/` (Work tab) modules over shared code in `app/shared/`, each with its own
  routes, agents, services, and (for chat) DB models. `chat` and `workflow` may no longer import
  each other, enforced by a new test. API routes, environment variables, and database tables are
  unchanged. The Angular UI's chat and work files are grouped into `ui/src/app/chat/` and
  `ui/src/app/work/` the same way, and `tests/` mirrors the new layout.
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

- Work tab: a full run that verifies no samples now lists why (e.g. `GOLDEN_IMAGE_NOT_FOUND: 47
  sample(s)`) in the result's `errors`, instead of only the preparation errors.
- The chat sidebar no longer appears on the login and register pages.
- The local LiteLLM proxy silently ran with no upstream OpenAI key (every OpenAI call 401'd)
  whenever `docker compose -f infra/development/docker-compose.yml` was invoked without
  `--env-file .env` - Compose's project directory defaulted to the compose file's own directory,
  which has no `.env`, so `${LITELLM_OPENAI_API_KEY:-}` silently resolved empty. `setup-dev.sh`
  / `setup-dev.ps1` and every documented command now pass `--env-file .env`.
