# Integration notes: pcb_agentic_inspector -> app/workflow/

What's in this directory, how it got here, and what was moved, adapted, or deleted along the way.
Written for whoever next touches `app/workflow/` and wonders why it looks like two different
codebases stitched together - it is.

## What happened

`app/workflow/` was emptied and replaced with a raw copy of a teammate's separate project,
`pcb_agentic_inspector` (an "Agent 1 orchestrator" + "Agent 2 explainability" pair, previously
ported/adapted by hand into this repo's own conventions under `app/workflow/agents/
{orchestrator_agent,explainability_review_agent}/` - that hand-port is gone now, replaced by this
closer-to-source drop-in). Then:

1. The raw drop-in's copies of the four ONNX models (`models/{feature,body,lead,text}/`) were
   deleted - they were exact duplicates of what `inference/` already deploys as
   `pcb_region`/`pcb_body_defect`/`pcb_lead_defect`/`pcb_text_defect` (same Hugging Face repos,
   same labels, same input sizes). Their model-card documentation was kept, consolidated into
   [`inference/MODELS.md`](../../inference/MODELS.md) instead of living twice in the repo.
2. `app/workflow/api/` and `app/workflow/services/` - this repo's own thin FastAPI routing layer
   (the drop-in has no HTTP entrypoint of its own; only a tkinter UI, a CLI batch runner, and two
   standalone REST APIs that persist *results after* a run, not what runs one) - were rebuilt
   around the drop-in's actual business logic.
3. Two files inside the drop-in were adapted (not left verbatim) to call this repo's `inference/`
   microservice over HTTP instead of loading local ONNX files, and one file got a minimal,
   additive hook for live progress streaming. Everything else in `src/agent1_orchestrator/` (and
   its sibling top-level `planner/`, `state/`, `verification/` packages) is untouched.
4. A handful of files that only made sense for the drop-in's own standalone entrypoints (tkinter
   launcher, CLI runner, a one-time source patcher) were deleted as unused now that routing goes
   through `app/workflow/api/` instead.
5. The drop-in's committed sample images/datasets were moved out of `app/workflow/` (source) to
   `data/workflow/` (data), matching this repo's existing `data/images`, `data/golden_images`,
   `data/orchestrator` convention.

## Adapted (not verbatim) - the inference swap

- **`src/agent1_orchestrator/services/model_lifecycle.py`** - originally loaded four local ONNX
  files via `config/models.yaml` + `ONNXImageClassifier`. Now resolves each of the four approved
  model keys (`feature`/`body`/`lead`/`text`) straight to the matching `inference/models.toml`
  model name (`pcb_region`/`pcb_body_defect`/`pcb_lead_defect`/`pcb_text_defect`) - no local
  loading at all. `config/models.yaml` and `src/agent1_orchestrator/inference/onnx_classifier.py`
  are deleted as a result (confirmed nothing else referenced either).
- **`src/agent1_orchestrator/services/multimodal_inference.py`** - originally called
  `.predict()` on the local ONNX classifier objects. Now calls `app.shared.inference.classify()`
  (the same async HTTP client `inspect_image`/Models-tab code already uses) via `asyncio.run()`
  internally, since the surrounding method stays synchronous (see below) but the call it makes is
  not. `routing` gained a `service_model` field alongside the existing `selected_model` (the real
  inference-service model name vs. the internal routing key - `app/workflow/services/
  monitoring.py`'s drift-report/retraining-ticket routes need the former, never the latter), and
  each stage result gained `model_version`.
- **Why still synchronous:** `OrchestratorAgent._execute_inference()` calls
  `self.inference.infer_sample(sample)` with no `await` - changing that would mean touching
  `agents/orchestrator.py` well beyond the one small hook described below. Since `infer_sample()`
  only ever runs inside `app/workflow/services/streaming.py`'s `asyncio.to_thread(agent.run, ...)`
  worker thread (which has no event loop of its own), calling `asyncio.run()` once per classify
  call from inside it is safe and keeps `agents/orchestrator.py` itself unchanged.

## Adapted (not verbatim) - the live-progress hook

`src/agent1_orchestrator/agents/orchestrator.py`'s `OrchestratorAgent.run()` is the one other
touched file, and the only change to it is additive and three lines: an `on_step: Optional[
Callable[[WorkflowState], None]]` parameter (default `None`, so any other caller - `ui.py`, the
drop-in's own tests - needs no change), and one `if on_step: on_step(state)` call at the end of
each planner loop iteration. `app/workflow/services/streaming.py` uses it to stream `log`/
`plan_step`/`status` SSE events live, instead of the Work tab only learning the outcome once the
whole run finishes.

## Everything else under `src/agent1_orchestrator/`, `planner/`, `state/`, `verification/`

Untouched. `DatasetPreparationService`, `DatasetVerificationService`, `PolicyEngine`, `Planner`
(the LLM/deterministic planner), `WorkflowState`, and the verification helpers are exactly what
was dropped in. `app/workflow/services/streaming.py`'s `_bridge_sys_path()` is the one place that
reaches into them, by inserting `app/workflow/` and `app/workflow/src/agent1_orchestrator/` onto
`sys.path` - mirroring exactly what the drop-in's own `ui.py` does for a direct tkinter launch, so
its bare (`agents.orchestrator`, `services.dataset_preparation`, ...) imports resolve the same way
they always have.

## Deleted as unused

Once `app/workflow/api/` routes to the drop-in's services directly, these standalone entrypoints
had nothing left calling them:

- `main.py` - a separate CLI batch runner with its own dataset-discovery logic; never called
  `OrchestratorAgent` at all.
- `run_ui.bat` - launched the tkinter UI directly; not applicable to a web backend.
- `integrate_ui.py` - a one-time source patcher already applied to produce the `ui.py` now here.
- `src/agent1_orchestrator/inference/onnx_classifier.py`, `config/models.yaml` - unused after the
  inference swap above (grep-verified nothing else referenced either).
- `qdrant_db/`, `outputs/` - runtime-generated artifacts (an embedded-Qdrant scaffold, an example
  `result.json`), not real inputs; neither is written by the new routing layer either
  (`populate_vector_db=False` by default, same as `ui.py`'s own default - see "not wired" below).
- `tests/workflow/agents/{test_multimodal_inference,test_orchestrator_escalation,
  test_review_agent_pure_nodes}.py` (repo-root `tests/`, not this drop-in's own `tests/`) tested
  the old hand-port's internals directly and don't apply to this drop-in's different shape -
  replaced by `tests/workflow/test_orchestrator_inference_swap.py`.

`ui.py` (the tkinter app), `main.py`'s sibling `example_result.json`, `LICENSE`, `README.md`,
`README_rest.md`, `requirements*.txt`, and the drop-in's own `tests/` are kept as reference/
documentation even though nothing in this repo runs them.

## Moved: static sample data -> `data/workflow/`

The drop-in's committed sample images/datasets don't belong inside `app/workflow/` (source) - they
now live under `data/workflow/`, the same top-level-`data/` convention as `data/images`,
`data/golden_images`, `data/orchestrator` (the last one, `settings.orchestrator_data_dir`, is
where *uploaded* Work-tab runs land - unaffected by this, it was already outside `app/workflow/`):

- `app/workflow/sample_data/` -> `data/workflow/sample_data/`
- `app/workflow/data/{35-900032-AAA-RV1,inputs}/` -> `data/workflow/data/`
  (`app/workflow/data/qdrant_indexer.py` - code, not data - stayed in place)

## Agent 2 review + human-in-the-loop (second pass, `feat/workflow-agent2-hitl`)

The teammate's updated drop-in ("UI Review Updated") adds an Agent 2 review step and a tkinter
conflict-resolution dialog/console that persist operator decisions to a Qdrant-backed Data API.
Here that became:

- **`src/agent2_explainability/pipeline/review_graph.py`** - replaced with the updated version
  (real precedent/telemetry/VLM/GPT-4o-with-heuristic-fallback nodes). One adaptation: its
  `config/agent2_config.yaml` is now resolved relative to the file, not the cwd, because the web
  backend is not launched from `app/workflow/`; and `locate_image()` no longer falls back to
  `rglob()`-ing `.` and `../..` for a missing image (that crawled `.venv`/`node_modules` and the
  repo's parent directory on every miss - the backend only passes absolute, already-resolved
  paths, so a miss now just skips visual inspection). `adc_shared/*` were refreshed to the updated
  copies too, but remain uncalled reference code (see below).
- **`app/workflow/services/reviews.py`** + routes under `/api/orchestrator/reviews/` - the flow
  `ui.py` intends: when a full run finishes, **every REVIEW_REQUIRED sample is sent to Agent 2
  automatically** (`streaming._auto_review`, the equivalent of `_dispatch_agent2_reviews`), with the
  same live log blocks (Agent 1 baseline, Agent 2 audit, full explanation, "Review Required" /
  "agents agree") and a `review` SSE event per sample. The Agent 2 pipeline runs in-process
  (`OrchestratorAgent` is still built with `enable_a2a=False` - A2A is a dead protocol in the
  source project: its README says so, but its dispatcher/agent-card code was never removed). The
  run mints a `run_id` and registers each sample's Agent 2 input **server-side**; the browser only
  ever sends `{run_id, sample_id}`, and the golden/defect images are served by
  `GET .../reviews/image` from that registry. A browser-supplied image path would be read from disk
  and sent to a VLM, and the monitoring routes' "trust what the browser sends" approach is not
  acceptable there. The registry (inputs and Agent 2 results) is in-memory and bounded, so a
  backend restart means the dataset must be rerun to review its samples again.
- **Nothing is auto-approved.** Like the updated `ui.py` (whose per-sample modal was replaced by a
  persistent Review Console), agreement between the agents only changes what the log says -
  "Operator may still review it". The operator makes the final call for every case.
- **Decisions persist in Postgres** (`workflow_review_decisions`, one row per `(run_id, sample_id)`,
  a later decision replaces the earlier), not the source project's Qdrant Data API - the app
  already has Postgres, Alembic and auth. `selected_source` keeps the source's `MACHINE`/`AI`/
  `MANUAL` vocabulary; `final_result` is the canonical lower-case IPC class.
- **Work tab** - an "Open Review Console" button (next to Run Agentic Workflow, with a pending
  count) opens a popup in `ui/src/app/work/` mirroring the tkinter `ReviewConsole`: an "Explanation
  Review Queue" with a Pending/Reviewed filter and a list of the run's cases (item, machine, AI,
  status) and, for the selected one, the golden and defect images side by side, the Machine / AI
  results, Agent 2's explanation, and Accept Machine / Accept AI / Manual with notes and Confirm
  Decision. It refetches as Agent 2 finishes each sample; Esc, Close or the backdrop dismiss it.
- Still chat-invisible: the routes are QA/Admin-gated, behind both `ORCHESTRATOR_AGENT_ENABLED` and
  `EXPLAINABILITY_REVIEW_AGENT_ENABLED`, and nothing here is registered in `app/chat/`.

## Explicitly kept, not wired

- **`src/agent2_explainability/{a2a,mcp}/`** - the A2A server and the FastMCP tool server. Present
  in the drop-in, unused: the review pipeline is called directly in-process.
- **`adc_shared/`, `adc_rest.py`, `test_rest.py`, `README_rest.md`, `compose.qdrant.yaml`** - a
  separate Shared Data API + Agent 2 REST API, backed by their own dedicated Qdrant (distinct from
  both this repo's Docker Qdrant for chat memory and `data/images/qdrant_db/` for case review).
  Not called by anything in `app/workflow/api/` or `app/workflow/services/` - a natural follow-up,
  not something removed.

## See also

- [`inference/MODELS.md`](../../inference/MODELS.md) - the four models' documentation, consolidated
  here from the drop-in's own copy after its `models/*.onnx` files were deleted as duplicates.
- `tests/workflow/test_orchestrator_inference_swap.py` - unit tests for the two adapted files
  above, mocking `app.shared.inference.classify()`.
