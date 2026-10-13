# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Chat: a **`list_runs`** tool answers "which Work-tab runs do I have?" with the stored runs (id,
  when saved, status; newest first, at most 10), so a run id can be found without leaving chat. The
  empty state has a matching suggestion. Read-only, QA and Admin, behind
  `SAMPLE_LOOKUP_AGENT_ENABLED`.
- Work tab: the Review Console's **Model feedback** tab is now charts first. A one-line read-out
  (how the defect model did, with the next step as a button) sits above headline rings, then:
  the outcome donut; **Stage 1 - Region model** (accuracy, kept vs corrected per part area) and
  **Stage 2 - Defect models** (one stacked bar of right / wrong / not classified / not reviewed per
  model, with confidence bars) as two separate sections, so the region model is never mistaken for
  a defect model; where the flagged samples went; who made the final call; how useful Agent 2 was;
  the commonest mistakes; and a grid of what the model is confused with. The numbers are also
  available as a table. **Retraining queue** puts the status and the road to a retrained model
  (To send -> Sent -> Awaiting Admin -> Retrained, current step marked) in one card, labels its
  columns "Model said" / "Corrected to" / "Where it stands", colours the label mix legend to match
  its bar, and keeps one send button in reach (per-model buttons only when there are several
  models). Each tab says what it is for ("Step 1/2/3").
- Chat: assistant replies are rendered as Markdown (headings, lists, tables, code, bold/italic,
  http(s) links) by a small built-in renderer that never uses `innerHTML`; a **Stop** button cancels
  a reply that is streaming (the text so far is kept); the "Thinking / Calling …" status now sits
  inside the reply being written; the message box grows with its content and keeps focus after
  sending; the thread only follows new text while you are at the bottom, with a **Jump to latest**
  button when you have scrolled up. Sending is disabled while a reply is streaming.
- Chat: a failed reply is shown as an error with a **Retry** button (text that arrived is kept);
  **Regenerate**, **Copy** and **Edit** (puts a sent message back in the box as a new message) on
  messages, with timestamps; a **Copy** button on code blocks; one paperclip attaches images and
  inspection XML, and pasted images attach too; assistant replies use the full width; SVG icons
  replace the emoji; focus rings and reduced-motion support. A message sent with files still says
  "N attachments" after a reload. The empty state has eight suggestions (the self-contained ones
  send on click; the image one opens the attach picker), and an inspection or a relabel/review
  proposal is followed by one-click replies ("Open a case…", "Yes, confirm…"). Retry and
  Regenerate resend the last user message, so the backend sees it twice.
- Chat: a reply that is stopped or fails part-way is now saved (`messages.interrupted`) instead of
  lost, and the cards a reply showed are saved with it (`messages.tool_results`), so a reopened
  conversation or another device shows both. New migration `d4a1c6e8b2f7`. Conversations can be
  renamed (`PATCH /api/conversations/{id}`) and searched in the sidebar, and a conversation
  continued on another device is refreshed when opened. `create_case` now shows a **Case created**
  card with the case number and status, followed by one-click Approve / Override / Relabel replies.
- Chat: failures say what happened (session expired, rate limited, can't reach the server, a file
  the server rejected) instead of one generic message; unsupported or over-20 MB files say why
  they were not attached; Enter no longer sends while an input method is composing; screen
  readers hear the reply's progress rather than every streamed chunk; deleting a chat asks first
  and its buttons are always visible on touch screens; the sidebar becomes a menu drawer on narrow
  screens; replies render nested lists, quotes and rules.
- Chat: each reply shows the tokens the turn used (prompt plus reply, summed over its tool-calling
  rounds) next to its timestamp, from the `usage` the backend now sends on the `done` SSE frame
  (absent when the provider reports none).
- Infra: a **daily** cost budget alongside the monthly one (`daily_budget_usd`, default 3), so a
  stack left running emails you the next morning instead of after a week; and an optional
  `auto_stop_schedule` that scales the three ECS services to zero on a schedule (`auto_stop.tf`;
  `terraform apply` starts them again).
- Infra: an optional monthly AWS cost budget (`infra/production/budget.tf`). Set `budget_alert_email`
  (and `monthly_budget_usd`, default 50) to get emails at 50% and 80% of actual spend and when the
  forecast passes 100%; unset, nothing is created.
- Chat: the Settings window lets you pick the model for the assistant's replies from the models the
  server allows (`CHAT_MODELS`, default `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini`, `gpt-4.1`, `gpt-5-mini`, `gpt-5`, `gpt-6-luna`, `gpt-6-sol`; the
  `OPENAI_MODEL` default is always offered first). The choice is remembered in the browser and only
  changes the reply: memory extraction, the guardrails check and the agents keep their own models.
  `GET /api/chat/models` lists the options; an unknown model falls back to the default.
- Work tab: the Review Console now has three tabs. **Model feedback** is a report that adds the
  **region model** (accuracy, confidence, what you changed it to), a funnel of where flagged samples
  went, and how useful Agent 2 was. **Retraining queue** is the work list: your corrections grouped by
  the model they teach, each group with its label mix, a hint when the batch is tiny or lopsided, and
  its own send button. Part-area corrections can now be sent for retraining as tickets for the region
  model, so one sample can carry a region and a defect correction. The ticket uniqueness now includes
  the model: on an existing database run
  `DROP INDEX uq_retraining_tickets_run_sample; CREATE UNIQUE INDEX uq_retraining_tickets_run_sample ON retraining_tickets (run_id, sample_ref, model_name) WHERE run_id IS NOT NULL;`.
- Work tab: the Review Console separates the two agents. **Agent 1** is a card with the model
  inference only (Machine is the label from the uploaded folder; AI is the defect model, with the
  region model's and defect model's confidence). **Agent 2** is its own advisory card: verdict,
  diagnosis, where its visual evidence came from, whether physical measurements were recorded, and
  the IPC clauses it weighed. The decision is Machine, AI or Manual (plus the part area).
- Work tab: the Review Console shows how sure both models were (the region model that picks the
  part area, then the defect model, or that no defect model ran) and lets the operator correct the
  part area as well as the defect. A corrected region sends the label to the defect model for that
  area. The region choice is stored with the decision (`region_result`) on the run's Qdrant point; the
  Postgres fallback keeps the defect decision only.
- Work tab: **Corrections & Retraining** is now **Model feedback**, built around one question:
  where do my corrections stand? It opens with a plain status sentence (with a way back to review
  when samples are still unreviewed), a four-stage tracker (to send, sent, awaiting Admin, retrained),
  and the corrections ready to send, all pre-ticked so it is one click. Below, analytics show
  reviewed share, model accuracy, an outcome donut, error rate by model, how you decided and the most
  common mistakes. The list shows the same machine verdict as the Review Console, and samples the defect
  model never classified are labelled "Not classified" with the reason.
- Work tab: the workflow summary has a **Failed** tile (preparation, verification and inference
  failures added up; red when above 0). The status line says at which stage samples failed, and the
  server's reasons ("Verification GOLDEN_IMAGE_NOT_FOUND: 76 sample(s) (e.g. S000001)") are listed
  under it, so an aborted run explains itself instead of showing only "Verified 0". The run status
  frames now carry `preparation_failed`, `verification_failed` and `inference_aborted`.
- Work tab: the Review Console is easier to scan. The queue shows machine and AI verdicts as
  coloured chips, Pending and Reviewed as badges and a progress bar; the case view compares Machine
  and AI side by side with an agree or disagree banner and a confidence meter, and the final decision
  options are colour-coded cards with a green confirm button. Tabs, buttons and image panels carry icons.
- Work tab: the Review Console no longer jumps when you pick another sample. The window has a
  fixed size, the queue and the detail pane scroll on their own, images load into fixed boxes, and
  the result of saving a decision appears below the form. Arrow keys move through the queue.
- Work tab: the Settings button is a visible bordered button, and the settings window applies
  changes only when you press **Save settings**; Cancel, Escape or clicking outside discards them.
- Work tab: renamed to the AOI Inspection Agent, with a short three-step guide, a status light
  (ready, uploading, running, failed, to review, complete), icons on the inputs and buttons, the run
  controls inside the Inputs card, and colour on the Accepted and Review counters.
- Work tab: a cleaner layout. The Output JSON field and the long explanatory note are gone (the
  result downloads as `result-<run id>.json`), inputs show what each file is for and which are
  required, and the run counters are tiles instead of a text line. Each run now starts with an empty
  log.
- Work tab: the Review Console traps keyboard focus, returns it to the button on close and will not
  close while a decision is saving. **Confirm & next pending** saves and opens the next unreviewed
  sample. The queue, images and Save show loading states, the log and form controls are labelled for
  screen readers, low-contrast text is darker, and a slow response can no longer overwrite newer data.
- Work tab: a **Stop** button cancels a run in progress, upload and run failures show the server's
  reason instead of a bare status code, and the review queue can be driven from the keyboard.
- Work tab: the policy thresholds and LLM planner options moved into a **Settings** panel (button
  bar), with a one-line summary of the active choices left on the page. They are remembered across
  reloads in the browser. Thresholds are kept within 0 to 1, and a cleared field no longer sends an
  invalid value that the server rejects.
- Work tab: the execution log is easier to follow and keep. It scrolls to the newest line while
  you are at the bottom and pauses when you scroll up, with a "N new lines - jump to latest" button.
  Lines carry the time they arrived, plan steps show their planner source and flag policy rejections,
  and errors stand out. A filter narrows it to plan steps and errors, or errors only. **Download log**
  saves the whole run as a text file and **Copy log** puts it on the clipboard. Only the latest 500
  lines are drawn (the download always has everything), which keeps long bulk runs responsive.
- Work tab: the Drift & Retraining tab is rebuilt around one idea - the operator's final decision in
  the Explanation Review is the correct label. It shows how often the model was wrong in the run
  (drift: of the samples the model classified, the share it got wrong - samples it couldn't decide on,
  where no defect model ran, are counted separately and are not drift), per model, with a breakdown of
  the final decisions and the model's commonest mistakes; then **Add N to training data** adds the
  samples the model got wrong or couldn't decide - with the operator's label, for the defect model
  they belong to (the region model's guess, routed like Agent 1 does, when no defect model ran) - and
  puts them in a retraining plan in one step, with each sample's training-data status. Drafting a plan
  syncs model versions from the inference service when none are recorded yet. The manual tools
  (report drift, flag samples) are removed from the tab.
- Work tab: the Drift & Retraining tab has a **Retraining plan** section. Queued corrections wait as
  tickets (shown as "unplanned" in the Models tab); **Draft retraining plan** turns a model's open
  tickets into a retraining job awaiting Admin approval, so the whole flow works without leaving the
  Work tab. New `POST /api/orchestrator/monitoring/retraining-plan`; `run-drift` also returns
  `open_tickets` per model.
- Docs: a rewritten user guide (every chat prompt and UI action and what it triggers, verified
  against the running app), `docs/DEMO.md` (a rehearsed demo script with the sample data and
  thresholds to use) and `docs/REVIEW_DRIFT_RETRAINING.md` (the Qdrant schema, the review -> drift ->
  retraining chain, API and tool reference, failure behaviour and limitations).
- Work tab: the Review Console's **Drift & Retraining** tab now follows the Explanation Review. When
  the operator relabels a sample in User Final Decision, the tab shows per-model drift for the run
  (reviewed, corrected, Agent 2 disagreements, confidence) and a list of the operator's corrections,
  computed on the server from the stored decisions; "Queue N for retraining" files a retraining
  ticket per correction (once per run and sample). The tickets are drafted into a retraining plan by
  chat's `draft_retraining_plan` and approved in the Models tab. New
  `GET /api/orchestrator/monitoring/run-drift` and `POST .../run-retraining-tickets`; drift reports
  filed for a run snapshot the server's numbers. Tickets now record their run
  (Alembic `c7e2b9d4f163`).
- Chat: `get_run_drift` (sample agent) - how the models did in a Work-tab run and what the operator
  corrected, and which corrections already have a retraining ticket.
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

### Fixed

- Approving a retraining job made of Work-tab tickets failed: the inference service requires a string
  `case_id` per sample and these tickets have none. They are now sent as `<run_id>:<sample_id>`.

### Changed

- Models tab: the Approve confirmation now says retraining is currently simulated (the inference
  service's only trainer is a stub), so an Admin isn't led to expect a new model.
- Chat: `get_sample` looking a sample id up in several runs without a run id now tells the model to
  name the run it answered from and offer the others (a sample id restarts every run), instead of
  leaving that to the tool description.
- Docs: README no longer lists a current-time/weather tool (neither exists); the User Guide
  describes the chat model picker in Settings; CLAUDE.md no longer mentions the golden-image bank
  removed in migration `f2a7c4e9d013`.
- Infra: the ALB accepts traffic only from CloudFront - its security group allows CloudFront's
  origin-facing prefix list instead of `0.0.0.0/0`, and the listener answers 403 unless the
  `X-Origin-Verify` header CloudFront adds is present - so the ALB's own DNS name no longer reaches
  the API. The deploy health check is now `<cloudfront_domain>/api/health` (new route; `/health`
  remains).
- Agent 2 was re-ported from `pcb_agentic_inspector` (Oct 10): its grounding prompt weights visual
  evidence and IPC precedents instead of letting laser/ICT readings decide. Missing readings are no
  longer invented as a 0.0 um height, which had made every review say "missing part". Its vision step
  now uses an OpenAI vision model.
- **Ollama is removed.** Chat, memory embeddings and Agent 2 use OpenAI only (`OPENAI_API_KEY` is
  required). The Settings window no longer offers a Local LLM, `OLLAMA_*` settings and the
  `langchain-ollama` dependency are gone, and the setup scripts, compose file and docs no longer
  mention it.

- Work tab: the Review Console's **Drift & Retraining** tab is renamed **Corrections & Retraining**
  and reworded so it can be used without knowing the pipeline. It opens with the three steps (review
  flagged samples, send corrections for retraining, an Admin approves the plan in the Models tab) and
  marks the current one. The headline is now the **error rate on reviewed samples**, with how many
  flagged samples have been reviewed (it is partial until all are). The row of counts becomes one bar
  (right, wrong, couldn't decide, not reviewed). The per-model table credits a model only with samples
  it classified; samples no defect model ran on are their own "No defect model ran" row. The
  "Your final decisions" bars are removed. **Add N to training data** is now **Send N corrections for
  retraining**: nothing is ticked until chosen (with select all), sent samples show a tick, labels and
  decision kinds use the Explanation Review's wording, and errors and confirmations share one status
  line. The tab says what to do first when there is no run yet. The "create retraining plan" prompt
  counts only samples from this run and notes how many more waiting from chat or other runs the plan
  would also take (`run-drift` returns `run_open_tickets` beside `open_tickets`). Chat's wording for
  the tab follows.

- Agent 2 review pipeline (`app/workflow/.../review_graph.py`) updated from the upstream `pcb_agentic_inspector`: tombstone precedent, telemetry read from the local cache with more field names and an explicit ICT flag, vision images downscaled to 384px with shorter prompts and a longer timeout, and revised cross-modal contradiction rules. Our config-path and no-repo-crawl fixes are kept. Adds `tests/test_agent_graph.py`.
- Defect-label comparison (Agent 1 vs Agent 2 conflicts and run corrections) now treats the AOI
  datasets' misspelling `SolderInsuffcient` as `solder insufficient`, as Agent 1's own pipeline
  already did; before, those samples showed false conflicts and false corrections.
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

- Registration no longer asks for a department or shift (`users.department_shift` is dropped, and the
  API no longer takes or returns it). On an existing database:
  `ALTER TABLE users DROP COLUMN department_shift;` (Alembic `b3d8f2a6c914` does the same). Until
  then a database that still has the old NOT NULL column rejects new users.
- LiteLLM proxy. The app now calls OpenAI directly with `OPENAI_API_KEY` (a real `sk-` key);
  `OPENAI_BASE_URL` defaults to `https://api.openai.com/v1` and only needs setting for another
  OpenAI-compatible endpoint. Removed `infra/litellm/`, the `litellm` compose service and its
  `LITELLM_*` variables, the production Fargate service, task definition, log group, security group,
  Cloud Map entry and `sentinelchat/litellm` secret. **Upgrading:** in `.env`, delete
  `OPENAI_BASE_URL=http://localhost:4000/v1`, put your real key in `OPENAI_API_KEY` (it used to hold
  `sk-litellm-dev`), and drop `LITELLM_OPENAI_API_KEY`; then
  `docker compose -f infra/development/docker-compose.yml --env-file .env down --remove-orphans`.
  In production, `terraform apply` creates `sentinelchat/openai` and rolls the backend; review the
  plan first. The dev proxy used to alias `gpt-4o` to `gpt-4o-mini`, so the Explainability agent now
  bills real `gpt-4o`. The Work tab's planner and Agent 2 use `ORCHESTRATOR_OPENAI_API_KEY` if set,
  else `OPENAI_API_KEY` (new `settings.orchestrator_api_key`).
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
