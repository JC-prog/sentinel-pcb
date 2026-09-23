"""The Admin actions behind the Models tab: approving a retraining job (and sending it to the
inference service), retrying a send, cancelling, promoting a model version and rolling back.

Each raises OperationError with the HTTP status the route should answer with, so the routes stay
thin. Nothing here is reachable from the chat tools - those can only draft a plan; the actions that
spend compute or change which model is live exist only behind these Admin-only routes.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import inference
from app.shared.db.models import (
    ModelVersion,
    ModelVersionStatus,
    RetrainingJob,
    RetrainingJobStatus,
    User,
)
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import versions as version_repo

logger = logging.getLogger(__name__)

_FINISHED = frozenset({"succeeded", "failed", "cancelled"})


class OperationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _from_inference(exc: Exception, action: str) -> OperationError:
    """A refusal from the inference service (e.g. "nothing to roll back", "file doesn't fit the
    model") keeps its meaning; anything else - unreachable, misconfigured, a 5xx - is a bad
    gateway."""

    if isinstance(exc, inference.InferenceNotConfigured):
        return OperationError(503, "the inference service is not configured")
    status = exc.status_code if isinstance(exc, inference.InferenceError) else None
    if status in (404, 409, 422):
        return OperationError(status, f"{action}: {exc}")
    return OperationError(502, f"{action}: {exc}")


# ------------------------------------------------------------------------------- retraining jobs


async def approve_and_submit(
    session: AsyncSession, job: RetrainingJob, *, admin: User
) -> RetrainingJob:
    """PENDING_APPROVAL -> APPROVED, then send it to the inference service. The approval stands
    even if the send fails (the service is down, say): the job stays APPROVED with the reason in
    `error`, and `submit` retries it - re-sending is safe, the service dedupes on our job id."""

    if RetrainingJobStatus(job.status) is not RetrainingJobStatus.PENDING_APPROVAL:
        raise OperationError(409, f"job is {job.status}; only a pending job can be approved")
    await job_repo.approve_job(session, job, approved_by_user_id=admin.id)
    return await _send(session, job)


async def submit(session: AsyncSession, job: RetrainingJob) -> RetrainingJob:
    """Retry sending an approved job that never reached the inference service."""

    if RetrainingJobStatus(job.status) is not RetrainingJobStatus.APPROVED:
        raise OperationError(409, f"job is {job.status}; only an approved, unsent job can be sent")
    return await _send(session, job)


async def _send(session: AsyncSession, job: RetrainingJob) -> RetrainingJob:
    samples = [
        inference.JobSample(
            case_id=s["case_id"],
            observed_label=s.get("observed_label"),
            expected_label=s.get("expected_label"),
        )
        for s in job.samples
    ]
    try:
        remote = await inference.submit_job(
            model=job.model_name,
            client_ref=job.id,
            samples=samples,
            base_version=job.base_version,
            notes=job.rationale,
        )
    except (inference.InferenceError, inference.InferenceNotConfigured) as exc:
        logger.warning("could not send retraining job %s: %s", job.id, exc)
        job.error = f"approved, but not sent to the inference service: {exc}"
        await session.commit()
        await session.refresh(job)
        return job

    job.error = None
    await job_repo.mark_submitted(session, job, external_job_id=remote.id)
    return await job_repo.apply_remote(session, job, remote)


async def cancel(session: AsyncSession, job: RetrainingJob) -> RetrainingJob:
    """Cancel a job in any non-terminal state. If the inference service has it queued or running
    it is asked to stop first; if that can't be done (unreachable) the job is left alone rather
    than cancelled on paper while still training."""

    status = RetrainingJobStatus(job.status)
    if status in job_repo.TERMINAL:
        raise OperationError(409, f"job is already {status.value}")

    if job.external_job_id and status in (RetrainingJobStatus.QUEUED, RetrainingJobStatus.RUNNING):
        try:
            await inference.cancel_job(job.external_job_id)
        except inference.InferenceNotFound:
            pass  # it doesn't know the job any more - nothing left to stop
        except (inference.InferenceError, inference.InferenceNotConfigured) as exc:
            # A 409 means it already finished on its side: take that as the truth instead.
            try:
                remote = await inference.get_job(job.external_job_id)
            except (inference.InferenceError, inference.InferenceNotConfigured):
                raise _from_inference(exc, "could not cancel on the inference service") from exc
            if remote.status in _FINISHED:
                return await job_repo.apply_remote(session, job, remote)
            raise _from_inference(exc, "could not cancel on the inference service") from exc

    return await job_repo.cancel_job(session, job)


# ----------------------------------------------------------------------------------- model versions


async def _record_activation(
    session: AsyncSession, info: inference.ModelInfo, admin: User
) -> ModelVersion:
    await version_repo.sync_versions(session, [info])
    live = await version_repo.get_live_version(session, info.name)
    if live is None:  # sync_versions just made info.version live - this can't happen
        raise OperationError(500, f"no live version recorded for {info.name}")
    live.activated_by_user_id = admin.id
    await session.commit()
    await session.refresh(live)
    return live


async def promote(
    session: AsyncSession, model_name: str, version: str, *, admin: User
) -> ModelVersion:
    """Make a known non-live version (a candidate a retrain produced, the previous one, a retired
    one) the live model. The inference service downloads, loads and smoke-tests it before swapping,
    so a bad one leaves the current model serving."""

    result = await session.scalars(
        select(ModelVersion).where(
            ModelVersion.model_name == model_name, ModelVersion.version == version
        )
    )
    row = result.first()
    if row is None:
        raise OperationError(404, f"{model_name} has no version {version!r}")
    if row.status == ModelVersionStatus.LIVE:
        raise OperationError(409, f"{version} is already live")

    try:
        info = await inference.activate_model(
            model_name, repo_id=row.repo_id, revision=row.revision
        )
    except (inference.InferenceError, inference.InferenceNotConfigured) as exc:
        raise _from_inference(exc, f"could not activate {version}") from exc
    return await _record_activation(session, info, admin)


async def rollback(session: AsyncSession, model_name: str, *, admin: User) -> ModelVersion:
    """Swap back to what the model served before its last activation."""

    try:
        info = await inference.rollback_model(model_name)
    except (inference.InferenceError, inference.InferenceNotConfigured) as exc:
        raise _from_inference(exc, f"could not roll {model_name} back") from exc
    return await _record_activation(session, info, admin)
