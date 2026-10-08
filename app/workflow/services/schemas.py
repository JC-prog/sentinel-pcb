"""Request/response schemas for the /api/orchestrator/* routes in app/workflow/api/orchestrator.py."""

from typing import Any

from pydantic import BaseModel, Field


class OrchestratorUploadRecord(BaseModel):
    id: str


class OrchestratorStatus(BaseModel):
    """GET /api/orchestrator/status - lets the Work tab mirror the source tkinter app's "OpenAI
    key detected" indicator next to its Use real LLM Planner checkbox, without exposing the key
    itself."""

    llm_configured: bool


class OrchestratorRunRequest(BaseModel):
    """Per-run controls, deliberately not settings.py fields - mirrors the source project's
    tkinter UI, where thresholds and LLM planner options are per-run controls, not server config."""

    mode: str  # "prepare" | "prepare_verify" | "run_full"
    dataset_id: str
    xml_id: str
    image_root_id: str | None = None
    feature_threshold: float = 0.70
    defect_threshold: float = 0.70
    use_llm: bool = False
    llm_model: str | None = None
    llm_fallback: bool = True


# --- Monitoring: filing a drift report / retraining tickets from a finished run's results -------
#
# Deliberately duplicated rather than importing app.modelops.services.schemas: workflow and
# modelops are independent feature modules (tests/test_module_boundaries.py). The two "manual" forms
# (drift-report, retraining-tickets) take the selected result dict(s) the browser received in the
# run's SSE `result` event, so that data is only as trustworthy as the QA/Admin session sending it.
# The Drift & Retraining tab's own routes (run-drift, run-retraining-tickets) instead read the run
# the server stored in Qdrant (app/workflow/services/run_store.py) and take only ids.


class WorkflowDriftReportRequest(BaseModel):
    model_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    model_version: str | None = None
    # The run being reported on. When given, the report snapshots the server's own numbers for the
    # model (computed from the stored run) instead of relying on `samples` alone.
    run_id: str | None = None
    # The selected per-sample result dicts, kept as evidence only (case_ids/case_numbers on the
    # DriftReport stay empty - those mean chat Case identifiers, not workflow samples).
    samples: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowDriftReportOut(BaseModel):
    id: str
    model_name: str
    model_version: str | None
    status: str


class WorkflowRetrainingTicketItem(BaseModel):
    sample: dict[str, Any]
    reason: str = Field(min_length=1)
    correct_label: str | None = None


class WorkflowRetrainingTicketsRequest(BaseModel):
    # With a run id, a sample already flagged for that run is skipped instead of flagged twice.
    run_id: str | None = None
    tickets: list[WorkflowRetrainingTicketItem] = Field(min_length=1)


class WorkflowRetrainingTicketOut(BaseModel):
    id: str
    sample_ref: str | None
    model_name: str | None
    status: str


class WorkflowModelDriftOut(BaseModel):
    """One model's numbers for a run (app/shared/modelops/run_drift.py). `model_name` is None for
    samples that stopped at feature classification and so reached no defect model."""

    model_name: str | None
    model_version: str | None
    samples: int
    review_required: int
    decided: int
    corrected: int
    correction_rate: float | None
    agent2_disagreed: int
    low_confidence_rate: float | None
    mean_confidence: float | None


class WorkflowCorrectionOut(BaseModel):
    """A sample where the operator's final result differs from Agent 1's label - the raw material
    for a retraining ticket. `queueable` is False when no model was recorded to file it against."""

    sample_id: str
    model_name: str | None
    model_version: str | None
    agent1_label: str | None
    final_result: str
    selected_source: str | None
    operator_notes: str | None
    queueable: bool
    queued: bool


class WorkflowRunDriftOut(BaseModel):
    run_id: str
    # False when the stored run could not be read (Qdrant unreachable); `message` says so.
    available: bool
    message: str | None = None
    totals: dict[str, int]
    models: list[WorkflowModelDriftOut]
    corrections: list[WorkflowCorrectionOut]
    # Open (not yet planned) retraining tickets per model, whatever their origin - what a plan drafted
    # now would contain. Models with none are left out.
    open_tickets: dict[str, int] = Field(default_factory=dict)


class WorkflowRetrainingPlanRequest(BaseModel):
    model_name: str = Field(min_length=1)
    rationale: str | None = None


class WorkflowRetrainingPlanOut(BaseModel):
    """A drafted plan: a retraining job awaiting an Admin's approval in the Models tab."""

    job_id: str
    model_name: str
    status: str
    base_version: str
    sample_count: int
    drift_reports_linked: int


class WorkflowQueueCorrectionsRequest(BaseModel):
    run_id: str = Field(min_length=1)
    sample_ids: list[str] = Field(min_length=1)


class WorkflowQueueCorrectionsOut(BaseModel):
    created: list[WorkflowRetrainingTicketOut]
    already_queued: list[str]


# --- Agent 2 review + human-in-the-loop conflict resolution -------------------------------------


class WorkflowReviewRunRequest(BaseModel):
    run_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)


class WorkflowReviewOut(BaseModel):
    """One Agent 2 review of a REVIEW_REQUIRED sample, next to Agent 1's verdict. `conflict` is
    True when the two normalized verdicts differ (or Agent 2's own self-check failed) - the case the
    operator must resolve; otherwise the Work tab auto-accepts the consensus."""

    run_id: str
    sample_id: str
    agent1_verdict: str
    agent2_verdict: str
    conflict: bool
    diagnosis: str
    confidence: float
    self_check_passed: bool
    contradiction_detected: bool
    ipc_citations: list[str]
    visual_evidence: str
    errors: list[str]


class WorkflowReviewDecisionRequest(BaseModel):
    run_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    selected_source: str  # "MACHINE" | "AI" | "MANUAL"
    final_result: str = Field(min_length=1)
    machine_result: str | None = None
    ai_result: str | None = None
    ai_diagnosis: str | None = None
    operator_notes: str | None = None


class WorkflowReviewDecisionOut(BaseModel):
    run_id: str
    sample_id: str
    selected_source: str
    final_result: str
    machine_result: str | None
    ai_result: str | None
    operator_notes: str | None
    decided_by_user_id: str


class WorkflowReviewCaseOut(BaseModel):
    """One row of the Work tab's Review Console: a REVIEW_REQUIRED sample of a run, Agent 1's call,
    Agent 2's review once it ran (`review`) and the operator's decision once saved (`decision`)."""

    run_id: str
    sample_id: str
    board_id: str
    component_ref: str
    feature_type: str
    agent1_verdict: str
    agent1_confidence: float
    has_golden_image: bool
    has_defect_image: bool
    review: WorkflowReviewOut | None
    decision: WorkflowReviewDecisionOut | None
