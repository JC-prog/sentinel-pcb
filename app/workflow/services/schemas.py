"""Request/response schemas for the /api/orchestrator/* routes in app/workflow/api/orchestrator.py."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    samples with no defect model to route to. Samples are grouped by the defect model they belong to
    (the one that classified them, or the one their region guess routes to). Of the `decided`
    (reviewed) samples, the operator's final label `confirmed` the model's prediction, `relabelled`
    it (the model was wrong), or the model couldn't decide (`no_prediction`); `corrected` /
    `correction_rate` - the run's drift - are the wrong ones out of those the model classified."""

    model_name: str | None
    model_version: str | None
    samples: int
    review_required: int
    decided: int
    confirmed: int
    relabelled: int
    no_prediction: int
    corrected: int
    correction_rate: float | None
    agent2_disagreed: int
    low_confidence_rate: float | None
    mean_confidence: float | None


class WorkflowRegionGuessOut(BaseModel):
    label: str
    confidence: float | None


class WorkflowCorrectionOut(BaseModel):
    """A reviewed sample the model didn't get right, with the operator's label (`final_result`): the
    model predicted something else (`outcome` "relabelled") or couldn't decide - no defect model ran
    (`outcome` "no_prediction"). `model_name` is the defect model it belongs to, decided by
    `model_source`: "classified" (that model ran) or "region_guess" (the region model's best guess,
    routed like Agent 1 does). `queueable` - can it be added to training data - when there is one.
    `ticket_status` once added: open (in the training data, no plan yet), acknowledged (in a
    retraining plan) or resolved (retrained)."""

    sample_id: str
    # "defect": the defect label, for a defect model. "region": the part area, for the region model
    # (`final_result` is then the area the operator set and `agent1_label` the area the model picked).
    kind: str = "defect"
    model_name: str | None
    model_version: str | None
    model_source: str | None = None
    region_guess: WorkflowRegionGuessOut | None = None
    region_result: str | None = None  # the region the operator set, when they changed it
    agent1_label: str | None  # the model's prediction; None when no defect model was reached
    machine_label: str | None  # the AOI machine's own call
    agent2_label: str | None
    final_result: str
    selected_source: str | None
    operator_notes: str | None
    outcome: str
    queueable: bool
    queued: bool
    ticket_status: str | None = None


class WorkflowSourceStatsOut(BaseModel):
    chosen: int
    overruled_model: int


class WorkflowOverruledOut(BaseModel):
    """One kind of miss: the model said `model_label`, the operator's choice stood for `chosen_label`."""

    model_label: str
    chosen_label: str
    count: int


class WorkflowDecisionStatsOut(BaseModel):
    """What the operator did in the Explanation Review: decisions per kind (MACHINE / AI / MANUAL), how
    many of each overruled the model, how many flagged samples still wait, and the commonest misses."""

    by_source: dict[str, WorkflowSourceStatsOut]
    pending: int
    top_overruled: list[WorkflowOverruledOut]


class WorkflowRegionByAreaOut(BaseModel):
    region: str
    samples: int
    corrected: int
    mean_confidence: float | None


class WorkflowRegionSwapOut(BaseModel):
    """The region model picked `model_region` and the operator set `chosen_region`, `count` times."""

    model_region: str
    chosen_region: str
    count: int


class WorkflowRegionStatsOut(BaseModel):
    """How the region model (stage 1) did, judged by the part area the operator settled on."""

    reviewed: int
    confirmed: int
    corrected: int
    accuracy: float | None
    mean_confidence: float | None
    stopped_here: int  # flagged samples the region model was too unsure about for a defect model
    by_region: list[WorkflowRegionByAreaOut]
    swaps: list[WorkflowRegionSwapOut]


class WorkflowFunnelOut(BaseModel):
    flagged: int
    defect_ran: int
    reviewed: int
    corrected: int


class WorkflowEvidenceUseOut(BaseModel):
    n: int
    matched: int


class WorkflowAgent2StatsOut(BaseModel):
    """How useful Agent 2 was: of the reviewed samples it reviewed, how often its verdict was the
    operator's final decision, split by what it had to go on."""

    with_review: int
    matched: int
    by_evidence: dict[str, WorkflowEvidenceUseOut]
    with_measurements: WorkflowEvidenceUseOut
    without_measurements: WorkflowEvidenceUseOut


class WorkflowRunDriftOut(BaseModel):
    run_id: str
    # False when the stored run could not be read (Qdrant unreachable); `message` says so.
    available: bool
    message: str | None = None
    totals: dict[str, int]
    # Share of reviewed samples the model did not get right; None before anything is reviewed.
    drift_rate: float | None = None
    models: list[WorkflowModelDriftOut]
    corrections: list[WorkflowCorrectionOut]
    decisions: WorkflowDecisionStatsOut
    region: WorkflowRegionStatsOut | None = None
    funnel: WorkflowFunnelOut | None = None
    agent2: WorkflowAgent2StatsOut | None = None
    # Open (not yet planned) retraining tickets per model, whatever their origin - what a plan drafted
    # now would contain. Models with none are left out.
    open_tickets: dict[str, int] = Field(default_factory=dict)
    # The same, counting only tickets added from this run.
    run_open_tickets: dict[str, int] = Field(default_factory=dict)


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


class WorkflowCorrectionRef(BaseModel):
    """One correction to send: the sample and the model it is a correction for. A sample can carry
    one for a defect model and one for the region model."""

    sample_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)


class WorkflowQueueCorrectionsRequest(BaseModel):
    run_id: str = Field(min_length=1)
    # `items` names the model of each correction. A bare `sample_ids` list (the older request) means
    # each sample's defect correction.
    items: list[WorkflowCorrectionRef] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _something_selected(self) -> "WorkflowQueueCorrectionsRequest":
        if not self.items and not self.sample_ids:
            raise ValueError("select at least one correction")
        return self


class WorkflowQueueCorrectionsOut(BaseModel):
    created: list[WorkflowRetrainingTicketOut]
    already_queued: list[str]


# --- Agent 2 review + human-in-the-loop conflict resolution -------------------------------------


class WorkflowReviewRunRequest(BaseModel):
    run_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)


class WorkflowPrecedentOut(BaseModel):
    """An IPC clause Agent 2 weighed for a sample."""

    ipc_clause: str
    standard: str
    rule: str


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
    # Where the visual evidence came from: "vision_model", "fallback" (a fixed sentence, the model was
    # unavailable) or "skipped" (the crop could not be found).
    visual_evidence_source: str = "fallback"
    # Whether the AOI record carried a physical reading (laser height / ICT) the review could use.
    telemetry_available: bool = False
    precedents: list[WorkflowPrecedentOut] = Field(default_factory=list)
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
    # The part area (Body / Lead / Text) when the operator corrected the region model. None means
    # they left it as the model had it.
    region_result: Literal["Body", "Lead", "Text"] | None = None


class WorkflowReviewDecisionOut(BaseModel):
    run_id: str
    sample_id: str
    selected_source: str
    final_result: str
    machine_result: str | None
    ai_result: str | None
    operator_notes: str | None
    region_result: str | None = None
    decided_by_user_id: str


class WorkflowReviewCaseOut(BaseModel):
    """One row of the Work tab's Review Console: a REVIEW_REQUIRED sample of a run, Agent 1's call,
    Agent 2's review once it ran (`review`) and the operator's decision once saved (`decision`)."""

    run_id: str
    sample_id: str
    board_id: str
    component_ref: str
    feature_type: str
    # Stage 1 (the region model): its label and confidence. None when the stored run has no stage 1.
    region_label: str | None = None
    region_confidence: float | None = None
    # False when the region model was too unsure and no defect model ran (agent1_verdict is then
    # the AOI machine's own call and agent1_confidence has no defect model behind it).
    defect_model_ran: bool = True
    # Machine = the label from the uploaded folder; AI = Agent 1's defect model prediction (None when
    # no defect model ran). `agent1_verdict` is the baseline Agent 2 audits: AI, else Machine.
    machine_verdict: str | None = None
    ai_verdict: str | None = None
    agent1_verdict: str
    agent1_confidence: float
    has_golden_image: bool
    has_defect_image: bool
    review: WorkflowReviewOut | None
    decision: WorkflowReviewDecisionOut | None


class WorkflowRunSummaryOut(BaseModel):
    """One stored run in the Work tab's run history: when it finished and where its samples ended
    up. `decided` counts flagged samples the operator has already made a final call on."""

    run_id: str
    saved_at_utc: str
    status: str
    input_samples: int
    preparation_ready: int
    preparation_failed: int
    verification_passed: int
    verification_failed: int
    inference_attempted: int
    inference_completed: int
    accepted: int
    review_required: int
    inference_aborted: int
    decided: int


class WorkflowRunListOut(BaseModel):
    runs: list[WorkflowRunSummaryOut]
    # False when the run store could not be read, so an empty list is not mistaken for "no runs".
    available: bool = True
