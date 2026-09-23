"""Bringing our records in line with the inference service, which is the authority on what is
loaded and what its jobs are doing. Both are best-effort by design: an unreachable service must
never make the Models tab error out - it shows what the database last knew, flagged as stale.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.modelops.services.schemas import InferenceStatus
from app.shared import inference
from app.shared.db.models import RetrainingJobStatus
from app.shared.inference import ModelInfo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import versions as version_repo

logger = logging.getLogger(__name__)

# Jobs the inference service should currently know about. APPROVED (not yet accepted) has no
# external id to ask about, and PENDING_APPROVAL hasn't been sent at all.
_ACTIVE = (RetrainingJobStatus.QUEUED, RetrainingJobStatus.RUNNING)


async def sync_versions(session: AsyncSession) -> tuple[InferenceStatus, list[ModelInfo]]:
    """Records what the inference service says it is serving. Returns its status and the live
    model infos (empty when it can't be reached)."""

    try:
        infos = await inference.list_models()
    except inference.InferenceNotConfigured:
        return InferenceStatus(configured=False, reachable=False), []
    except inference.InferenceError as exc:
        logger.warning("could not list models from the inference service: %s", exc)
        return InferenceStatus(configured=True, reachable=False, error=str(exc)), []

    await version_repo.sync_versions(session, infos)
    return InferenceStatus(configured=True, reachable=True), infos


async def refresh_active_jobs(session: AsyncSession) -> None:
    """Polls the inference service for every job we believe is queued or running and folds the
    answer in. A job it no longer knows (it restarted; jobs are held in its memory) can't be
    resumed, so it is failed and its tickets released. A transient error leaves the job as it
    was to be looked at again next time."""

    for job in await job_repo.list_jobs(session, statuses=_ACTIVE, limit=200):
        if not job.external_job_id:
            continue
        try:
            remote = await inference.get_job(job.external_job_id)
        except inference.InferenceNotFound:
            await job_repo.mark_lost(
                session, job, "the inference service no longer knows this job (it was restarted?)"
            )
        except (inference.InferenceError, inference.InferenceNotConfigured) as exc:
            logger.warning("could not refresh retraining job %s: %s", job.id, exc)
        else:
            await job_repo.apply_remote(session, job, remote)
