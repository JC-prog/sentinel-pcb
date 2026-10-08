# Review, drift and retraining - technical reference

How a Work-tab run is stored, how the operator's decisions become drift numbers and retraining
tickets, how the chat agents read and extend that data, and which safeguards sit around it. For using
the app see [`USER_GUIDE.md`](USER_GUIDE.md); for the system as a whole see
[`ARCHITECTURE.md`](ARCHITECTURE.md). Module-level notes live in
[`app/workflow/INTEGRATION_NOTES.md`](../app/workflow/INTEGRATION_NOTES.md).

## 1. The chain

```
 Work tab run (Agent 1: region -> defect)
   │ saved:  adc_orchestrator_runs + adc_inspection_results            (Qdrant)
   ▼
 Agent 2 review (per REVIEW_REQUIRED sample)
   │ saved:  adc_agent2_reviews.result                                  (Qdrant)
   ▼
 Operator decision in Explanation Review ("User Final Decision")
   │ saved:  adc_agent2_reviews.human_decision                          (Qdrant)
   ▼
 GET  .../monitoring/run-drift  ── shared/modelops/run_drift.py ──►  Drift & Retraining tab
   │ corrections = final result != Agent 1's label
   ▼
 Operator queues corrections:  POST .../monitoring/run-retraining-tickets
   │ saved:  retraining_tickets (run_id + sample_ref)                   (Postgres)
   ▼
 Draft a plan: Work tab button (POST .../monitoring/retraining-plan)
   or chat draft_retraining_plan  ──►  retraining_jobs (pending_approval)   (Postgres)
   ▼
 Admin approves in the Models tab ──► inference service job (stub trainer)
```

Chat reads the same Qdrant data read-only (`get_sample`, `list_review_cases`, `get_run_drift`).
Separately, chat's own inspection flow creates **Cases** in Postgres - see section 6.

## 2. Storage

### 2.1 Qdrant: three payload-only collections

Written by the ported `Repository` (`app/workflow/adc_shared/repository.py`, unmodified from the
source project) through `app/workflow/services/run_store.py`, in-process, against the app's Docker
Qdrant (`QDRANT_URL`). They hold **payloads only** (`vectors_config={}`), so they never collide with
chat's long-term-memory collection. Point ids are `uuid5(NAMESPACE_URL, json.dumps(key parts))`;
`run_id`, `sample_id` and `final_decision` are keyword-indexed on every collection.

| Collection | Point id from | Payload |
| --- | --- | --- |
| `adc_orchestrator_runs` | `(run_id)` | `run_id`, `digest` (sha256 of the result), `status`, `saved_at_utc`, `storage_status` (`WRITING` -> `READY`), `result` (the whole `WorkflowState`: `inputs`, `prepared_samples`, `verified_samples`, `inference_results`, `observations`, `plan_history`, `tool_history`, `errors`, counters, `termination_reason`, `planner_backend`, ...) |
| `adc_inspection_results` | `(run_id, sample_id)` | `run_id`, `sample_id`, `sample` (board, package, component, source feature, machine defect, images, `failed_inspections`, ...), `inference` (Agent 1's result), `final_decision` |
| `adc_agent2_reviews` | `(run_id, sample_id)` | `run_id`, `sample_id`, `saved_at_utc`, `result` (`review_status: GENERATED_UNVALIDATED`, `source`, `output` = Agent 2's verdict, diagnosis, citations, ...), then once decided `human_decision`, `review_status: COMPLETED`, `reviewed_at_utc` |

`inference` has two shapes (see `run_drift._stage`): a completed sample has top-level
`feature_classification`, `defect_classification` and `routing.service_model` (the real inference
model, e.g. `pcb_text_defect`); a sample that stopped at the first stage has only
`details.feature_classification` and **no routing, so no model name**.

`human_decision` = `{selected_source: MACHINE|AI|MANUAL, final_result, operator_notes, machine_result,
ai_result, ai_diagnosis, decided_by_user_id}`.

**Write rules** (all from the repository):
- A run is written in two phases (`WRITING`, then sample points, then `READY`); readers refuse a run
  that is not `READY`. Saving the same `run_id` with different content raises a conflict; identical
  content is idempotent.
- The first Agent 2 review of a sample is **immutable** (`run_review` returns the stored one rather than
  re-running the model). A later decision only *adds* `human_decision` and never touches Agent 2's evidence.
- `sample_id` is only unique within a run - every lookup is by `(run_id, sample_id)`.

### 2.2 Postgres tables this feature touches

| Table | Role |
| --- | --- |
| `retraining_tickets` | One per flagged sample/case. New column **`run_id`** (nullable) and a partial unique index `uq_retraining_tickets_run_sample` on `(run_id, sample_ref) WHERE run_id IS NOT NULL` - makes queueing a run's sample idempotent. Chat tickets point at a `case_id` instead. (Alembic `c7e2b9d4f163`.) |
| `retraining_jobs` | A drafted plan; its `samples` snapshot now includes each ticket's `run_id`. |
| `drift_reports` | Reports; one filed for a run stores `stats.run_id`, `stats.run_totals` and `stats.model` (a snapshot of the server's numbers). |
| `workflow_review_decisions` | **Fallback only.** Used when Qdrant is unreachable or the run isn't stored there; `list_decisions` merges it with Qdrant. Dropping it is a follow-up (needs a migration). |
| `inspection_drafts` | An image inspection waiting for the user's "yes" (section 6). One per (conversation, user). (Alembic `b5c1f8a3d742`.) |
| `cases` | Chat cases (section 6). Not written by the Work tab. |

## 3. Drift and corrections (`app/shared/modelops/run_drift.py`)

Pure functions over the stored payload dicts - no I/O - so the Work tab (`run_store`) and chat
(`run_samples`) compute identical numbers without importing each other.

- **Agent 1's label** = `defect_classification.prediction`, else the dataset's `machine_defect` (the
  baseline Agent 2 audits for a sample that stopped at stage 1).
- **Normalisation** (`normalize_defect`): `WrongPart_13` -> `wrong part`, `Shift` -> `shifted`,
  `Golden` -> `no defect`, and the datasets' misspelling `SolderInsuffcient` -> `solder insufficient`.
- **Correction**: a decided sample whose normalised `final_result` differs from the normalised Agent 1
  label. *Accept Machine* is therefore agreement; *Accept AI* / *Manual* that differ are corrections.
  `queueable` is false when no model was recorded.
- **Per-model stats** (grouped by `routing.service_model`; un-routed samples group under `null`):
  `samples`, `review_required`, `decided`, `corrected`, `correction_rate` (corrected/decided),
  `agent2_disagreed`, `low_confidence_rate` (defect confidence < `ADC_DEFECT_CONFIDENCE_THRESHOLD`),
  `mean_confidence`, `model_version`.

Chat's existing `get_drift_summary` (`app/chat/services/drift.py`) is unchanged and measures **saved
Cases**; the run-based numbers are a separate view.

## 4. HTTP API (`app/workflow/api/orchestrator.py`)

All routes need a logged-in **QA or Admin**; the review/decision routes are behind
`ORCHESTRATOR_AGENT_ENABLED` and `EXPLAINABILITY_REVIEW_AGENT_ENABLED`, the monitoring routes behind
`ORCHESTRATOR_AGENT_ENABLED` and `MODELOPS_ENABLED` (503 otherwise).

| Route | Purpose |
| --- | --- |
| `POST /api/orchestrator/run/stream` | Run the workflow; SSE events `log`, `status`, `plan_step`, `result`, `review`, `error`, `done`. The finished run is saved and Agent 2 reviews each flagged sample. |
| `POST /api/orchestrator/reviews/run` `{run_id, sample_id}` | Run (or return the stored) Agent 2 review. |
| `PUT /api/orchestrator/reviews/decision` | Save the operator's decision (replaces an earlier one). Stored on the sample's review point. |
| `GET /api/orchestrator/reviews/cases?run_id=` | The Review Console list: each flagged sample with its review and decision. |
| `GET /api/orchestrator/reviews/decisions?run_id=` | Decisions (Qdrant plus any Postgres fallback rows). |
| `GET /api/orchestrator/reviews/image?run_id=&sample_id=&kind=golden\|defect` | The crop, looked up server-side (never a client-supplied path). |
| `GET /api/orchestrator/monitoring/run-drift?run_id=` | Per-model stats and the corrections, with each correction's `queued` flag. 404 unknown run; with Qdrant down `available:false` and a message (fail-open). |
| `POST /api/orchestrator/monitoring/run-retraining-tickets` `{run_id, sample_ids}` | Queue one ticket per selected correction, **built server-side** from the stored sample and decision. All-or-nothing on the selection: a sample with no correction, or no model recorded, rejects the whole request (422). Already-queued samples are skipped and reported (`already_queued`). 503 if the store is unreachable. |
| `POST /api/orchestrator/monitoring/retraining-plan` `{model_name, rationale?}` | Turn every **open** ticket for the model (Work-tab and chat alike) into a `retraining_job` in `pending_approval`, linking the model's open drift reports - the same job chat's `draft_retraining_plan` makes. 409 if there are no open tickets or the base version can't be determined. Approving stays an Admin action in the Models tab. |
| `POST /api/orchestrator/monitoring/drift-report` | File a drift report. With `run_id` it snapshots the server's numbers for the model; without, it records only the browser-sent evidence. |
| `POST /api/orchestrator/monitoring/retraining-tickets` | The older "manual" path: tickets from browser-sent sample dicts; with `run_id`, a sample already ticketed for the run is skipped. |

Ticket content for a queued correction: `sample_ref` = sample id, `run_id`, `model_name`,
`model_version` (defect, else feature, model version), `observed_label` = Agent 1's label,
`correct_label` = the operator's `final_result`, and a server-written `reason` that includes the
decision type and the operator's notes. A concurrent double-click is resolved by the unique index
(the loser is reported as already queued).

## 5. Chat tools that read or extend this data

| Tool | Agent | Reads / writes | Roles | Kill switch |
| --- | --- | --- | --- | --- |
| `get_sample` | sample | reads Qdrant | QA, Admin | `SAMPLE_LOOKUP_AGENT_ENABLED` |
| `list_review_cases` | sample | reads Qdrant | QA, Admin | same |
| `get_run_drift` | sample | reads Qdrant + `retraining_tickets` (to mark queued) | QA, Admin | same |
| `draft_retraining_plan` | monitoring | turns open tickets (chat **or** Work-tab) into a `retraining_job` `pending_approval` | QA, Admin | `MONITORING_AGENT_ENABLED` |

`get_sample` answers from the newest run containing the sample id and lists the others in
`other_runs`; local image paths are never returned. The reader is `app/chat/services/run_samples.py`:
**read-only** (no collection creation, no writes), filters by payload, ignores runs that are not
`READY`, and turns an unreachable Qdrant into a refusal the model can relay. `chat` may not import
`workflow`, so it re-states the collection names and payload keys;
`tests/chat/agents/test_sample_agent.py` seeds it with data written by the workflow's own `Repository`,
so a change to what the workflow writes breaks that test.

Chat can **draft** a plan but never approve one: approve / cancel / promote / roll back are Admin-only
routes of the Models tab (`app/modelops/api/models.py`).

## 6. Chat cases, and why a case needs your "yes"

A **Case** is a Postgres row for an image the user inspected in chat and wants to relabel or review;
the Work tab never creates cases. `inspect_image` classifies and reports but **saves nothing** - it
parks the result in `inspection_drafts` (the exact `create_case(...)` arguments, JSONB). The
`create_case` tool takes no model-supplied data and turns the draft into a case.

The confirmation is enforced in code (`app/chat/services/confirmation.py`): a draft (or a relabel /
review proposal) can only be committed in a **later chat turn** than the one that created it, and only
by the same user. `ToolContext.turn_started_at` is when the current turn began; a proposal stamped inside
that turn is refused if committed in it, whatever the model was told. The same rule guards
`confirm_relabel` and `confirm_review`. `create_case` deletes the draft in the same commit that
creates the case, so a second call finds nothing (no duplicates). A new inspection replaces the
user's waiting draft; deleting the conversation cascades to it.

## 7. Failure behaviour

| Situation | Behaviour |
| --- | --- |
| Qdrant unreachable during a run | The run completes from memory; the Review Console works from the in-memory registry until restart. Decisions fall back to `workflow_review_decisions`. |
| Qdrant unreachable for the Drift tab | `run-drift` returns `available:false` + message; the tab shows it. Queueing returns 503. |
| Backend restarted | The Review Console rebuilds its registry from Qdrant (`reviews.ensure_run_loaded`), including reviews already generated. |
| Chat tool cannot reach Qdrant / run not stored | The tool returns an `error` the assistant relays; the chat turn does not fail. |
| Inference service down | `inspect_image` errors with nothing saved; relabel fails closed (it cannot validate the label). |
| Sample stopped at stage 1 | Listed as a correction but not queueable ("No model recorded"). |

## 8. Settings

| Setting (env) | Default | Effect |
| --- | --- | --- |
| `QDRANT_URL` | `http://localhost:6333` | Where runs/reviews/decisions are stored and read. |
| `ADC_REGION_CONFIDENCE_THRESHOLD` / `ADC_DEFECT_CONFIDENCE_THRESHOLD` | 0.70 / 0.70 | Chat inspections: below region threshold no defect model runs; below defect threshold the verdict is *review required*. Also the low-confidence cut-off in run drift. |
| `ORCHESTRATOR_AGENT_ENABLED`, `EXPLAINABILITY_REVIEW_AGENT_ENABLED`, `MODELOPS_ENABLED` | on | Work tab, Agent 2 review, monitoring/Models routes. |
| `ADC_INSPECTION_AGENT_ENABLED`, `RELABEL_AGENT_ENABLED`, `REVIEW_AGENT_ENABLED`, `MONITORING_AGENT_ENABLED`, `SAMPLE_LOOKUP_AGENT_ENABLED` | on | Per-agent chat tool kill switches. |
| `INFERENCE_BASE_URL` | blank | Inference service (classification, versions, jobs). |

The Work tab's own thresholds (default 0.70) are per-run inputs in the UI, not settings.

## 9. Tests

| Area | Where |
| --- | --- |
| Drift/correction maths, normalisation | `tests/shared/test_run_drift.py` |
| Run persistence, restart survival, fallback | `tests/workflow/test_run_store.py`, `test_orchestrator_reviews.py` |
| Drift tab routes: decision -> numbers -> queue, idempotency, all-or-nothing, fail-open, report snapshot | `tests/workflow/test_run_drift_routes.py` |
| Chat reader/tools, `get_run_drift`, and the full chain (workflow ticket -> chat `draft_retraining_plan`) | `tests/chat/agents/test_sample_agent.py` |
| Confirm-before-create, same-turn refusal, end to end through the supervisor | `tests/chat/agents/test_create_case.py` |
| UI: Drift & Retraining refresh after a decision, corrections table, queue | `ui/src/app/work/work.spec.ts`, `work-orchestrator-client.spec.ts` |
| Module boundaries (chat/workflow/modelops independent) | `tests/test_module_boundaries.py`, `tests/chat/test_agent_boundaries.py` |
| Live tool-selection evals (opt-in, costs tokens) | `uv run pytest -m eval -s` (`tests/evals/`) |

## 10. Known limitations

- **Uploaded images are on local disk.** A run and its decisions survive a restart (Qdrant), but the
  golden/defect images do not survive a redeploy, and this blocks multi-task deployment.
- **Work-tab tickets have no `case_id`.** The inference service requires a string `case_id` per
  sample, so `operations._sample_ref` sends `<run_id>:<sample_ref>` for them (a chat ticket sends its
  real case id). The stub trainer ignores it; real retraining will need a way to fetch the flagged
  images, which live in the backend's upload store.
- **Drift is two views.** Chat's `get_drift_summary` counts saved Cases; the Work tab and `get_run_drift`
  count a run's samples. Since inspections no longer create cases automatically, the Case-based
  numbers cover only inspections users chose to keep.
- **The bundled models are weak on the sample data**, so most samples are low-confidence and flagged -
  useful for demonstrating review, not representative of production accuracy.
- Agent 2's review runs in-process; its A2A/MCP scaffolding is present but unwired.
