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
# modelops are independent feature modules (tests/test_module_boundaries.py), and orchestrator runs
# are never persisted server-side (see app/workflow/services/streaming.py) - there's no run id for
# these requests to reference, so the browser sends back the actual selected result dict(s) from
# the SSE `result` event it already received. That data is only as trustworthy as the requesting
# QA/Admin session; there's no server-side run record to check it against.


class WorkflowDriftReportRequest(BaseModel):
    model_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    model_version: str | None = None
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
    tickets: list[WorkflowRetrainingTicketItem] = Field(min_length=1)


class WorkflowRetrainingTicketOut(BaseModel):
    id: str
    sample_ref: str | None
    model_name: str | None
    status: str


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
