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

## Explicitly kept, not wired in this pass

- **`src/agent2_explainability/`** (Agent 2 - A2A/MCP scaffolding + review pipeline) - present in
  the drop-in, but `OrchestratorAgent` is constructed with `enable_a2a=False` in
  `app/workflow/services/streaming.py`, matching `ui.py`'s own current default. A sample that
  needs review stays `REVIEW_REQUIRED`; nothing escalates it further yet.
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
