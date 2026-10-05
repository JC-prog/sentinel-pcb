"""Routes only - see app/workflow/services/streaming.py for the streaming-run logic,
app/workflow/services/uploads.py for upload storage, and app/workflow/services/monitoring.py for
the drift-report/retraining-ticket routes below. Gated to QA/Admin and
settings.orchestrator_agent_enabled; never registered as a chat Tool, so the chat LLM's
function-calling loop can never reach any of this.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User, UserRole
from app.workflow.services import monitoring, reviews
from app.workflow.services import uploads as orchestrator_uploads
from app.workflow.services.monitoring import UnresolvableSample
from app.workflow.services.schemas import (
    OrchestratorRunRequest,
    OrchestratorStatus,
    OrchestratorUploadRecord,
    WorkflowDriftReportOut,
    WorkflowDriftReportRequest,
    WorkflowQueueCorrectionsOut,
    WorkflowQueueCorrectionsRequest,
    WorkflowRetrainingTicketOut,
    WorkflowRetrainingTicketsRequest,
    WorkflowReviewCaseOut,
    WorkflowReviewDecisionOut,
    WorkflowReviewDecisionRequest,
    WorkflowReviewOut,
    WorkflowReviewRunRequest,
    WorkflowRunDriftOut,
)
from app.workflow.services.streaming import orchestrator_sse

router = APIRouter()


def _require_qa_or_admin(user: User) -> None:
    if UserRole(user.role) not in (UserRole.QA, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="QA or admin role required")


def _require_orchestrator_and_modelops_enabled(user: User) -> None:
    """Guard for the two monitoring routes below - they sit at the intersection of the orchestrator
    and model-operations features, so both switches (not just one) gate them."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    if not settings.modelops_enabled:
        raise HTTPException(status_code=503, detail="model operations are disabled")
    _require_qa_or_admin(user)


@router.get("/api/orchestrator/status")
async def orchestrator_status(
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorStatus:
    """Lets the Work tab mirror the source tkinter app's "OpenAI key detected" indicator next to
    its Use real LLM Planner checkbox, without exposing the key itself."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    return OrchestratorStatus(llm_configured=bool(settings.orchestrator_openai_api_key))


@router.post("/api/orchestrator/uploads/dataset")
async def orchestrator_upload_dataset(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="file must be a CSV")
    upload_id = await orchestrator_uploads.save_dataset_csv(file)
    return OrchestratorUploadRecord(id=upload_id)


@router.post("/api/orchestrator/uploads/xml")
async def orchestrator_upload_xml(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    """Separate from POST /api/uploads/xml, which is only for inspect_image - keeps the two agents'
    upload domains decoupled."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(status_code=422, detail="file must be an XML document")
    upload_id = await orchestrator_uploads.save_inspection_xml(file)
    return OrchestratorUploadRecord(id=upload_id)


@router.post("/api/orchestrator/uploads/image-root")
async def orchestrator_upload_image_root(
    files: Annotated[list[UploadFile], File()],
    relative_paths: Annotated[list[str], Form()],
    user: Annotated[User, Depends(get_current_user)],
) -> OrchestratorUploadRecord:
    """`relative_paths[i]` is `files[i]`'s webkitRelativePath from the Work tab's
    <input webkitdirectory multiple> folder picker - see app/workflow/services/uploads.py."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)
    if len(files) != len(relative_paths):
        raise HTTPException(status_code=422, detail="files and relative_paths must match in length")
    try:
        upload_id = await orchestrator_uploads.save_image_root_files(files, relative_paths)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OrchestratorUploadRecord(id=upload_id)


@router.post("/api/orchestrator/run/stream")
async def orchestrator_run(
    request: OrchestratorRunRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    _require_qa_or_admin(user)

    return StreamingResponse(
        orchestrator_sse(request, user.username),
        media_type="text/event-stream",
    )


@router.post("/api/orchestrator/monitoring/drift-report")
async def orchestrator_report_drift(
    request: WorkflowDriftReportRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> WorkflowDriftReportOut:
    """Files a drift report for a finished bulk run's model, into the same app/shared/modelops/
    tables the chat monitoring agent and Models tab use. `request.samples` is evidence only - see
    monitoring.py's module docstring for why this trusts whatever the browser sends."""

    _require_orchestrator_and_modelops_enabled(user)
    return await monitoring.file_drift_report(session, request=request, user=user)


@router.post("/api/orchestrator/monitoring/retraining-tickets")
async def orchestrator_flag_samples_for_retraining(
    request: WorkflowRetrainingTicketsRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> list[WorkflowRetrainingTicketOut]:
    """Queues one retraining ticket per selected sample. All-or-nothing: a sample that never
    reached stage-2 routing has no real model name to file against, so the whole request is
    rejected rather than silently dropping it - see monitoring.UnresolvableSample."""

    _require_orchestrator_and_modelops_enabled(user)
    try:
        return await monitoring.flag_samples_for_retraining(session, request=request, user=user)
    except UnresolvableSample as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/orchestrator/monitoring/run-drift")
async def orchestrator_run_drift(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> WorkflowRunDriftOut:
    """The Drift & Retraining tab's numbers for a run: per-model drift and the operator's
    corrections, computed server-side from the stored run (Qdrant) so they follow every decision
    saved in the Explanation Review."""

    _require_orchestrator_and_modelops_enabled(user)
    try:
        return await monitoring.run_drift_summary(session, run_id=run_id)
    except monitoring.UnknownRun as exc:
        raise HTTPException(status_code=404, detail=f"run {run_id} is not stored") from exc


@router.post("/api/orchestrator/monitoring/run-retraining-tickets")
async def orchestrator_queue_corrections(
    request: WorkflowQueueCorrectionsRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> WorkflowQueueCorrectionsOut:
    """Queues a retraining ticket for each selected sample the operator corrected, built on the
    server from the stored sample and decision. All-or-nothing on the selection; samples already
    queued for the run are reported, not duplicated."""

    _require_orchestrator_and_modelops_enabled(user)
    try:
        return await monitoring.queue_corrections(session, request=request, user=user)
    except monitoring.UnknownRun as exc:
        raise HTTPException(status_code=404, detail=f"run {request.run_id} is not stored") from exc
    except monitoring.StoreUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="the stored run could not be read - try again shortly"
        ) from exc
    except (monitoring.NotACorrection, UnresolvableSample) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _require_review_enabled(user: User) -> None:
    """Agent 2 reviews and decisions sit on top of a finished orchestrator run, so both the
    orchestrator and the explainability-review switches gate them."""

    if not settings.orchestrator_agent_enabled:
        raise HTTPException(status_code=503, detail="orchestrator agent is disabled")
    if not settings.explainability_review_agent_enabled:
        raise HTTPException(status_code=503, detail="explainability review agent is disabled")
    _require_qa_or_admin(user)


@router.post("/api/orchestrator/reviews/run")
async def orchestrator_run_review(
    request: WorkflowReviewRunRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> WorkflowReviewOut:
    """Runs Agent 2 on one REVIEW_REQUIRED sample of a finished run and returns its verdict next
    to Agent 1's. Never registered as a chat Tool, same as the rest of this file."""

    _require_review_enabled(user)
    try:
        return await reviews.run_review(request.run_id, request.sample_id)
    except reviews.UnknownReviewCase as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No review input for sample {request.sample_id} - the run is unknown or the "
                "backend restarted since it finished. Rerun the dataset to review it."
            ),
        ) from exc


@router.put("/api/orchestrator/reviews/decision")
async def orchestrator_save_review_decision(
    request: WorkflowReviewDecisionRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> WorkflowReviewDecisionOut:
    _require_review_enabled(user)
    if request.selected_source not in reviews.SELECTED_SOURCES:
        raise HTTPException(
            status_code=422, detail="selected_source must be MACHINE, AI, or MANUAL"
        )
    if not request.final_result.strip():
        raise HTTPException(status_code=422, detail="final_result is required")
    return await reviews.save_decision(session, request=request, user=user)


@router.get("/api/orchestrator/reviews/decisions")
async def orchestrator_list_review_decisions(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> list[WorkflowReviewDecisionOut]:
    _require_review_enabled(user)
    return await reviews.list_decisions(session, run_id=run_id)


@router.get("/api/orchestrator/reviews/cases")
async def orchestrator_list_review_cases(
    run_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: SessionDep,
) -> list[WorkflowReviewCaseOut]:
    """The Review Console's list: every REVIEW_REQUIRED sample of a run with its Agent 2 review and
    operator decision, where they exist."""

    _require_review_enabled(user)
    return await reviews.list_cases(session, run_id=run_id)


@router.get("/api/orchestrator/reviews/image")
async def orchestrator_review_image(
    run_id: str,
    sample_id: str,
    kind: str,
    user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    """The golden or defect crop of a registered review case. The file is looked up in the
    server-side registry by (run_id, sample_id), never from a client-supplied path."""

    _require_review_enabled(user)
    if kind not in {"golden", "defect"}:
        raise HTTPException(status_code=422, detail="kind must be golden or defect")
    await reviews.ensure_run_loaded(run_id)
    path = reviews.image_path(run_id, sample_id, kind)
    if path is None:
        raise HTTPException(status_code=404, detail="image not available")
    return FileResponse(path)
