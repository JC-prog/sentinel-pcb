"""The Models tab's routes. Route declarations only - reading is in app/modelops/services/views.py,
the Admin actions in services/operations.py, syncing with the inference service in
services/reconcile.py. Reads are open to QA and Admin; every action that spends compute or changes
which model is live is Admin-only. None of this is a chat Tool.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.modelops.services import operations, views
from app.modelops.services.operations import OperationError
from app.modelops.services.schemas import (
    DriftOverview,
    DriftReportOut,
    JobDetailOut,
    ModelsOverview,
    ModelVersionOut,
    PromoteRequest,
    QueueOverview,
)
from app.shared.auth import get_current_user
from app.shared.auth.dependencies import SessionDep
from app.shared.config.settings import settings
from app.shared.db import User, UserRole
from app.shared.db.models import DriftReportStatus, RetrainingJob, RetrainingJobStatus
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo

router = APIRouter(prefix="/api/models", tags=["models"])


async def _qa_or_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not settings.modelops_enabled:
        raise HTTPException(status_code=503, detail="model operations are disabled")
    if UserRole(user.role) not in (UserRole.QA, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="QA or admin role required")
    return user


async def _admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not settings.modelops_enabled:
        raise HTTPException(status_code=503, detail="model operations are disabled")
    if UserRole(user.role) != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


QaOrAdmin = Annotated[User, Depends(_qa_or_admin)]
Admin = Annotated[User, Depends(_admin)]


def _http(exc: OperationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _load_job(session: SessionDep, job_id: str) -> RetrainingJob:
    job = await job_repo.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="retraining job not found")
    return job


# -------------------------------------------------------------------------------------- reads


@router.get("")
async def models_overview(_: QaOrAdmin, session: SessionDep) -> ModelsOverview:
    """Every model with its live version and history, plus open drift reports/tickets and jobs in
    progress. Syncs with the inference service first; `inference` reports whether that worked."""

    return await views.models_overview(session)


@router.get("/drift")
async def drift_overview(
    _: QaOrAdmin,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    model: str | None = None,
    status: DriftReportStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DriftOverview:
    return await views.drift_overview(session, days=days, model=model, status=status, limit=limit)


@router.get("/retraining/queue")
async def retraining_queue(
    _: QaOrAdmin,
    session: SessionDep,
    status: Annotated[list[RetrainingJobStatus] | None, Query()] = None,
    model: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> QueueOverview:
    """The retraining jobs, newest first, with ticket counts. Refreshes queued/running jobs from
    the inference service first."""

    return await views.queue_overview(session, statuses=status, model=model, limit=limit)


@router.get("/retraining/jobs/{job_id}")
async def retraining_job(job_id: str, _: QaOrAdmin, session: SessionDep) -> JobDetailOut:
    return await views.job_detail(session, await _load_job(session, job_id))


# ---------------------------------------------------------------------------------- job actions


@router.post("/retraining/jobs/{job_id}/approve")
async def approve_job(job_id: str, admin: Admin, session: SessionDep) -> JobDetailOut:
    """Approves a drafted plan and sends it to the inference service. If the send fails the plan
    stays approved with the reason in `error`; POST .../submit retries it."""

    job = await _load_job(session, job_id)
    try:
        job = await operations.approve_and_submit(session, job, admin=admin)
    except OperationError as exc:
        raise _http(exc) from exc
    return await views.job_detail(session, job)


@router.post("/retraining/jobs/{job_id}/submit")
async def submit_job(job_id: str, _: Admin, session: SessionDep) -> JobDetailOut:
    job = await _load_job(session, job_id)
    try:
        job = await operations.submit(session, job)
    except OperationError as exc:
        raise _http(exc) from exc
    return await views.job_detail(session, job)


@router.post("/retraining/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str, user: Annotated[User, Depends(_qa_or_admin)], session: SessionDep
) -> JobDetailOut:
    """An Admin can cancel any unfinished job; whoever drafted a plan can withdraw it while it is
    still awaiting approval."""

    job = await _load_job(session, job_id)
    is_admin = UserRole(user.role) is UserRole.ADMIN
    withdrawing_own_draft = (
        job.created_by_user_id == user.id
        and RetrainingJobStatus(job.status) is RetrainingJobStatus.PENDING_APPROVAL
    )
    if not (is_admin or withdrawing_own_draft):
        raise HTTPException(
            status_code=403,
            detail="only an admin can cancel this job (you can withdraw your own pending plan)",
        )
    try:
        job = await operations.cancel(session, job)
    except OperationError as exc:
        raise _http(exc) from exc
    return await views.job_detail(session, job)


# --------------------------------------------------------------------------- drift report actions


@router.post("/drift/{report_id}/resolve")
async def resolve_drift_report(report_id: str, _: Admin, session: SessionDep) -> DriftReportOut:
    report = await drift_repo.get_drift_report(session, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="drift report not found")
    if report.status == DriftReportStatus.RESOLVED:
        raise HTTPException(status_code=409, detail="drift report is already resolved")
    report = await drift_repo.resolve_drift_report(session, report)
    names = await views.usernames(session, [report.reported_by_user_id])
    return views.drift_report_out(report, names)


# ------------------------------------------------------------------------------- version actions


@router.post("/{model_name}/promote")
async def promote_version(
    model_name: str, body: PromoteRequest, admin: Admin, session: SessionDep
) -> ModelVersionOut:
    """Makes a known non-live version the live model. The inference service loads and smoke-tests
    it before swapping; on any failure the current model keeps serving."""

    try:
        version = await operations.promote(session, model_name, body.version, admin=admin)
    except OperationError as exc:
        raise _http(exc) from exc
    return ModelVersionOut.model_validate(version)


@router.post("/{model_name}/rollback")
async def rollback_model(model_name: str, admin: Admin, session: SessionDep) -> ModelVersionOut:
    try:
        version = await operations.rollback(session, model_name, admin=admin)
    except OperationError as exc:
        raise _http(exc) from exc
    return ModelVersionOut.model_validate(version)
