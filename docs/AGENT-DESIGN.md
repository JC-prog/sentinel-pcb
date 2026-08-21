# Agent Design — SentinelPCB (as-built)

*What actually exists in `app/` today, not the target design. For the full 8-component vision
this is a subset of, see `planning/docs/PRD.md`, `AGENT-CONCEPTS.md`, `SCENARIOS.md`, and
`DATA-MODEL.md`. This document now covers Scenario 1/2's state machine including a real (if
placeholder) defect classifier, a templated Explainability & Review report backed by RAG, and an
extended Continuous Monitoring & Drift covering agent/policy health — not the multi-component
fan-out (Scenarios 4/5) or Model Lifecycle.*

---

## 1. Classification, quick recap

Per `planning/docs/AGENT-CONCEPTS.md` §4, two independent questions decide what a component is:

1. Does it ever reason with an LLM to decide what happens next?
2. Does it own a branching disposition that directly causes the next state transition, or does
   it just answer a question / perform a side effect for whichever agent called it?

|                          | Owns a disposition | Doesn't |
|---|---|---|
| **Reasons (LLM)** | Agent | (rare — folded into an agent's turn) |
| **Never reasons** | Deterministic subagent | Tool |

## 2. What's actually implemented

| Component | Classification (target) | Reasons today? | Real branching today? | File |
|---|---|---|---|---|
| Orchestrator | Agent | **No** — deterministic confidence + disagreement check | Yes (accept / escalate / rejected_quality) | `app/agents/orchestrator/` |
| Dataset Preparation | Deterministic subagent | No | **No** — always succeeds | `app/agents/dataset_preparation/` |
| Dataset Quality | Deterministic subagent | No | Yes (pass / fail + `failed_checks`) | `app/agents/dataset_quality/` |
| Multi-Modal Inference | Deterministic subagent | No | Yes (feature detections + a placeholder defect classification) | `app/agents/multi_modal_inference/` |
| Case Context | **Resolved: a tool**, not a subagent (see §4) | No | No — answers a retrieval question, owns no disposition | `app/agents/explainability_review/case_context.py` |
| Explainability & Review | Agent (templated, no live LLM) | **No** — deterministic template | Yes (routes to `HUMAN_REVIEW`) | `app/agents/explainability_review/` |
| Continuous Monitoring & Drift | Agent (partial), extended to agent-health too | No | No — reports metrics, proposes nothing yet | `app/agents/continuous_monitoring_drift/` |
| Model Lifecycle | Deterministic subagent | — | — | empty stub |

The honest summary: **four working deterministic subagents/tools**, an **Orchestrator and an
Explainability & Review that are both coded as reasoning agents but have no LLM in them at all
(deliberately — see §4)**, a **real observability surface**, and **one component (Model
Lifecycle) that still doesn't exist** beyond a package name.

---

## 3. The persisted workflow lifecycle

One image submitted = one `Workflow` row, moving through a fixed sequence. Every arrow below is a
row update + an append-only `AuditEvent` (each carrying the *actual* responsible actor, not a
blanket `"orchestrator"` — a bug fixed alongside this state machine), committed before the next
phase starts — see `app/agents/orchestrator/runner.py`.

```
RECEIVED ──► PREPARING ──► QUALITY_CHECK ─┬─(fails)──► REJECTED_QUALITY                      [terminal]
 (orchestrator)  (dataset_preparation)     │(dataset_quality)
                                           └─(passes)──► INFERENCE
                                                           (multi_modal_inference: feature
                                                            detection, then defect classification)
                                                              │
                                                              ▼
                                                        POLICY_DECISION (orchestrator)
                                                    ┌──────────────┴──────────────┐
                                            (accept)│                             │(escalate)
                                                    ▼                             ▼
                                                ACCEPTED ──► LEARNING_QUEUE   EXPLANATION
                                              (orchestrator)   [terminal]  (explainability_review)
                                                                               │
                                                                               ▼
                                                                          HUMAN_REVIEW [terminal]
```

- `RECEIVED`: written synchronously, inside the `POST /workflows` request, before the response
  goes out (`create_workflow()`). Everything from `PREPARING` onward runs afterward, in a FastAPI
  `BackgroundTask` (`continue_workflow()`).
- `PREPARING` → `prepare_sample()` (Dataset Preparation). Pure in-memory assembly, no I/O, no way
  to fail today.
- `QUALITY_CHECK` → `validate_sample()` (Dataset Quality). Rule-based: size ceiling, PIL
  decodability, minimum resolution (64px), format allowlist (PNG/JPEG/BMP/TIFF).
- `INFERENCE` → two model calls, both logged as intra-phase `AuditEvent`s (no status change
  between them, since both are Multi-Modal Inference's own job):
  - `get_detector().predict()` — the real ONNX model (YOLOv12-Medium export), detects structural
    features (`MountingHole`, `ComponentBody`, `SolderJoint`, `Lead`) — **not** defect classes.
    Result stored as `Workflow.feature_confidence` (= min(detection confidences), or `0.0`).
  - `get_defect_classifier().predict()` — a **deterministic placeholder** (see §4), *not* a
    trained model. Result stored as `Workflow.overall_confidence` / `Workflow.defect_label`.
- `POLICY_DECISION` (Orchestrator, its own phase now — previously bundled into `INFERENCE`, which
  is what motivated splitting it out): escalates if *either* `defect_confidence <
  auto_accept_confidence` (`app/policies/thresholds.py`, default `0.95`) *or* the feature/defect
  confidence gap exceeds `confidence_disagreement_delta` (default `0.15`) — model disagreement is
  now its own legitimate escalation trigger, not just low confidence. The resulting proposal still
  goes through `app/policies/allowlist.py`'s hard allowlist (`validate_action()`) — the
  propose-then-validate pattern from `AGENT-CONCEPTS.md` §6.
- `ACCEPTED` → `LEARNING_QUEUE` (automatic, same background-task pass): the workflow's summary is
  embedded (`app/services/embeddings.py`) and stored on `Workflow.embedding`, so it becomes a
  retrievable precedent for a *future* escalated case's report.
- `EXPLANATION` → `draft_explanation()` (Explainability & Review, see §4) builds a templated report
  and persists it as an `Explanation` row before handing off to `HUMAN_REVIEW`.

A `demo_phase_delay_seconds` setting (default `0`, off) can pace these transitions artificially so
they're visible in the Tracing UI instead of completing in well under 100ms.

---

## 4. Per-component notes

### Orchestrator (`app/agents/orchestrator/`)

- `__init__.py::run_workflow()` — the original synchronous, DB-free reference implementation,
  kept in sync with `runner.py`'s decision logic (same two-model check, same disagreement
  formula) so the two "parallel implementations of the same pipeline" never drift.
- `runner.py::create_workflow()` / `continue_workflow()` — the persisted path `app/main.py`
  actually calls.
- Still no LLM call anywhere in either version — the confidence + disagreement check is entirely
  deterministic. This is deliberate for this pass, not a placeholder: the decision to build
  Continuous Monitoring & Drift's agent-health metrics *before* turning on any live LLM reasoning
  anywhere in the system (Orchestrator or Explainability & Review) was made explicitly, so there's
  something watching before anything genuinely agentic exists to watch.

### Dataset Preparation (`app/agents/dataset_preparation/`)

Unchanged — `prepare_sample()` always succeeds, `reference_image` is hardcoded to `None`. Still a
pass-through; see the previous revision of this doc for the fuller explanation.

### Dataset Quality (`app/agents/dataset_quality/`)

Unchanged — `validate_sample()` is the quality gate, `REJECTED_QUALITY` short-circuit confirmed by
`tests/test_workflows.py::test_submit_workflow_rejects_bad_image`.

### Multi-Modal Inference (`app/agents/multi_modal_inference/`)

- `detector.py::PCBFeatureDetector` — unchanged, the real ONNX feature detector.
- `defect_classifier.py::PlaceholderDefectClassifier` (**new**) — a deterministic stand-in for a
  real, trained defect model. No accuracy claim. Label: the *weakest* feature detection's label,
  mapped to the physically-associated defect class (`SolderJoint→"Solder Bridge"`,
  `Lead→"Misalignment"`, `ComponentBody→"Tombstone"`, `MountingHole→"Foreign Material"` —
  `planning/docs/SCENARIOS.md`'s own illustrative labels). Confidence:
  `mean(confidences) × (1 − spread)`, deliberately *not* a copy of the feature detector's own
  confidence, so feature/defect disagreement is a real, exercisable signal. See
  `docs/MODEL-TRAINING.md` for how to replace this with a real trained model — a one-file change.

### Case Context (`app/agents/explainability_review/case_context.py`)

**The previously-open PRD §5/§11 question is now resolved**: Case Context is a **tool**
Explainability & Review calls, not a standalone pipeline stage — its result never gates a
transition on its own, it only feeds into the report Explainability builds. RAG over two sources,
both embedded with the same local model (`app/services/embeddings.py`,
`sentence-transformers/all-MiniLM-L6-v2`, pgvector cosine-distance search):

- `RemediationDoc` — a hand-authored synthetic corpus (`scripts/seed_remediation_docs.py`, 16
  documents across the four illustrative defect labels), not derived from real manufacturer
  documentation.
- `Workflow.embedding` — our own accumulating history of auto-accepted cases. Thin today (a new
  system has few precedents); grows as more workflows reach `LEARNING_QUEUE`.

The embedding provider is abstracted (`EmbeddingProvider` protocol) specifically so a hosted
provider — and eventually an admin panel to choose one — is a future config change, not a rewrite.
Only the local provider is implemented today.

### Explainability & Review (`app/agents/explainability_review/__init__.py`)

No longer an empty stub. `draft_explanation()` builds a templated report — claims constructed
directly from already-computed evidence (feature detections, the defect classification, the
Orchestrator's own escalation rationale), a Case Context section, and a recommendation. No live
LLM call. `_grounding_check()` is a real function that's always a pass-through today (every claim
is grounded by construction) — kept as its own step specifically so swapping in a real LLM +
a real grounding check later is "replace this function's body," not "add a missing step." Does
not go through `validate_action`/`ProposedAction` — drafting a report isn't an action with
consequences; the Orchestrator already decided to escalate before this runs.

### Continuous Monitoring & Drift (`app/agents/continuous_monitoring_drift/__init__.py`)

No longer an empty stub, and now doing more than the original PRD scoped: model metrics (decision
distribution, confidence averages, feature/defect disagreement rate) **plus agent/policy health**
(allowlist rejection count) that the original design never specified a mechanism for. Computed
on-demand (`GET /observability/metrics`), not on a schedule — no cron/Redis exists in this stack,
and there's no ground-truth-verified drift signal yet since `HUMAN_REVIEW` has no reviewer-action
endpoint. Alerts are a dashboard-only threshold-breach flag; no outbound notification channel
(email/webhook) exists or is built.

### Model Lifecycle

Still an empty stub. No design decision implemented.

---

## 5. Known gaps against the target design

- **`HUMAN_REVIEW` is still a dead end.** No reviewer-action endpoint exists, so an escalated
  workflow has no way to actually get resolved via the UI, which also means it can never reach
  `LEARNING_QUEUE` — only auto-accepted workflows currently contribute to the precedent corpus,
  even though human-corrected cases would arguably be the more valuable training signal.
- **`hitl_escalation_confidence` (0.80) is still defined but unused.** A two-outcome system
  (accept/escalate) doesn't need a third confidence tier yet.
- **True per-slice drift detection doesn't exist** — Continuous Monitoring & Drift reports
  current-state metrics, not degradation over time against a baseline, since there's no
  verified-outcome feed to compare against.
- **No multi-component fan-out.** One AOI event still maps to exactly one `Workflow`; the
  `ComponentSubCase` model and per-board rollup from Scenarios 4/5 don't exist.
- **`sentence-transformers` pulls in `torch`** — a genuinely heavier dependency than anything else
  in this stack. Necessary for local embeddings; worth knowing before assuming this is a light
  install.
- **No audit-trail retention policy**, per `DATA-MODEL.md` §6's open question — `AuditEvent` rows
  accumulate indefinitely today.

## Cross-references

`app/agents/orchestrator/runner.py` (the mechanism), `app/agents/explainability_review/` (report +
RAG), `app/agents/continuous_monitoring_drift/` (observability), `app/policies/` (thresholds +
allowlist), `docs/MODEL-TRAINING.md` (replacing the placeholder defect classifier),
`planning/docs/PRD.md` §6-§7 (the target this is a subset of), `planning/docs/AGENT-CONCEPTS.md`
§4/§6 (the classification test and propose-then-validate pattern this follows).
