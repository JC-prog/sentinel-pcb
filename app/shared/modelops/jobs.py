"""The retraining queue's rules: drafting a job from open tickets, the status state machine, and
folding the inference service's view of a job back into ours. No HTTP here - callers (the chat
monitoring agent, the Models tab's API) talk to the inference service through
app/shared/inference and hand the result to `apply_remote`.

Nothing that changes a live model or spends compute happens without an Admin: `draft_job` only
ever creates PENDING_APPROVAL, and approval is a separate, explicit step.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import (
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicket,
    RetrainingTicketStatus,
)
from app.shared.inference import RemoteJob
from app.shared.modelops import tickets as ticket_repo
from app.shared.modelops import versions

S = RetrainingJobStatus

_TRANSITIONS: dict[RetrainingJobStatus, frozenset[RetrainingJobStatus]] = {
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.CANCELLED}),
    # The inference service can be observed already running (or finished) the first time we look
    # after submitting, so an approved job may skip ahead.
    S.APPROVED: frozenset({S.QUEUED, S.RUNNING, S.SUCCEEDED, S.FAILED, S.CANCELLED}),
    S.QUEUED: frozenset({S.RUNNING, S.SUCCEEDED, S.FAILED, S.CANCELLED}),
    S.RUNNING: frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.CANCELLED: frozenset(),
}

TERMINAL = frozenset(status for status, targets in _TRANSITIONS.items() if not targets)

# The inference service's own status vocabulary -> ours.
_FROM_REMOTE = {
    "queued": S.QUEUED,
    "running": S.RUNNING,
    "succeeded": S.SUCCEEDED,
    "failed": S.FAILED,
    "cancelled": S.CANCELLED,
}


class InvalidTransition(Exception):
    pass


class NothingToRetrain(Exception):
    """draft_job() found no open tickets for the model."""


class BaseVersionUnknown(Exception):
    """draft_job() couldn't tell which version the retrain starts from."""


def _move(job: RetrainingJob, new: RetrainingJobStatus) -> None:
    current = RetrainingJobStatus(job.status)
    if new not in _TRANSITIONS[current]:
        raise InvalidTransition(f"job {job.id} cannot go from {current.value} to {new.value}")
    job.status = new


async def _job_tickets(session: AsyncSession, job: RetrainingJob) -> list[RetrainingTicket]:
    return list(
        await session.scalars(select(RetrainingTicket).where(RetrainingTicket.job_id == job.id))
    )


async def _release_tickets(session: AsyncSession, job: RetrainingJob) -> None:
    """A job that will never train (cancelled/failed) gives its tickets back, so they can go into
    a new plan instead of being stranded as 'acknowledged' forever."""

    for ticket in await _job_tickets(session, job):
        ticket.status = RetrainingTicketStatus.OPEN
        ticket.job_id = None


async def draft_job(
    session: AsyncSession,
    *,
    model_name: str,
    created_by_user_id: str,
    rationale: str,
    drift_report_ids: Sequence[str] = (),
    base_version: str | None = None,
) -> RetrainingJob:
    """Collects every open ticket for `model_name` into a PENDING_APPROVAL job and marks those
    tickets ACKNOWLEDGED. The samples are copied into the job, so approving it approves exactly
    what was reviewed even if tickets change later. Raises NothingToRetrain / BaseVersionUnknown."""

    open_tickets = await ticket_repo.list_open_tickets(session, model_name)
    if not open_tickets:
        raise NothingToRetrain(model_name)

    if base_version is None:
        live = await versions.get_live_version(session, model_name)
        base_version = live.version if live is not None else None
    if base_version is None:
        base_version = next((t.model_version for t in open_tickets if t.model_version), None)
    if base_version is None:
        raise BaseVersionUnknown(model_name)

    job = RetrainingJob(
        model_name=model_name,
        base_version=base_version,
        rationale=rationale,
        drift_report_ids=list(drift_report_ids),
        created_by_user_id=created_by_user_id,
        samples=[
            {
                "case_id": t.case_id,
                "case_number": t.case_number,
                "ticket_id": t.id,
                "observed_label": t.observed_label,
                "expected_label": t.correct_label,
            }
            for t in open_tickets
        ],
    )
    session.add(job)
    await session.flush()  # assigns job.id for the tickets below
    for ticket in open_tickets:
        ticket.job_id = job.id
        ticket.status = RetrainingTicketStatus.ACKNOWLEDGED
    await session.commit()
    await session.refresh(job)
    return job


async def get_job(session: AsyncSession, job_id: str) -> RetrainingJob | None:
    return await session.get(RetrainingJob, job_id)


async def list_jobs(
    session: AsyncSession,
    *,
    statuses: Sequence[RetrainingJobStatus] | None = None,
    model_name: str | None = None,
    limit: int = 50,
) -> list[RetrainingJob]:
    """Newest first."""

    stmt = select(RetrainingJob).order_by(RetrainingJob.created_at.desc()).limit(limit)
    if statuses is not None:
        stmt = stmt.where(RetrainingJob.status.in_([s.value for s in statuses]))
    if model_name is not None:
        stmt = stmt.where(RetrainingJob.model_name == model_name)
    return list(await session.scalars(stmt))


async def approve_job(
    session: AsyncSession, job: RetrainingJob, *, approved_by_user_id: str
) -> RetrainingJob:
    """PENDING_APPROVAL -> APPROVED. Recording the approval is separate from submitting to the
    inference service, so a submit that fails leaves an approved job that can simply be retried."""

    _move(job, S.APPROVED)
    job.approved_by_user_id = approved_by_user_id
    job.approved_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(job)
    return job


async def mark_submitted(
    session: AsyncSession, job: RetrainingJob, *, external_job_id: str
) -> RetrainingJob:
    """The inference service accepted the job. Safe to call again for a job already past APPROVED
    (a retried submit returns the same remote job) - it just records the id."""

    job.external_job_id = external_job_id
    if RetrainingJobStatus(job.status) is S.APPROVED:
        _move(job, S.QUEUED)
    await session.commit()
    await session.refresh(job)
    return job


async def cancel_job(session: AsyncSession, job: RetrainingJob) -> RetrainingJob:
    """Local half of a cancel (any non-terminal job); asking the inference service to stop a
    queued/running one is the caller's job."""

    _move(job, S.CANCELLED)
    job.finished_at = datetime.now(UTC)
    await _release_tickets(session, job)
    await session.commit()
    await session.refresh(job)
    return job


async def apply_remote(
    session: AsyncSession, job: RetrainingJob, remote: RemoteJob
) -> RetrainingJob:
    """Folds the inference service's report on `job` into our record. A remote status that would be
    a step backwards, or that is unchanged, only refreshes progress. On success the result's
    artifact is registered as a candidate model version and the job's tickets are RESOLVED; a
    failed or cancelled job releases its tickets."""

    target = _FROM_REMOTE.get(remote.status)
    if target is None:
        return job

    current = RetrainingJobStatus(job.status)
    job.external_job_id = job.external_job_id or remote.id
    job.progress = remote.progress
    if target is not current and target in _TRANSITIONS[current]:
        _move(job, target)
        job.started_at = job.started_at or remote.started_at
        if target in TERMINAL:
            job.finished_at = remote.finished_at or datetime.now(UTC)

        if target is S.SUCCEEDED:
            job.progress = 1.0
            await _on_succeeded(session, job, remote)
        elif target is S.FAILED:
            job.error = remote.error or "retraining failed"
            await _release_tickets(session, job)
        elif target is S.CANCELLED:
            await _release_tickets(session, job)

    await session.commit()
    await session.refresh(job)
    return job


async def _on_succeeded(session: AsyncSession, job: RetrainingJob, remote: RemoteJob) -> None:
    if remote.result is not None:
        job.artifact_repo_id = remote.result.artifact.repo_id
        job.artifact_revision = remote.result.artifact.revision
        job.simulated = remote.result.simulated
        await versions.record_candidate(
            session,
            model_name=job.model_name,
            version=f"{job.artifact_repo_id}@{job.artifact_revision}",
            source_job_id=job.id,
        )
    for ticket in await _job_tickets(session, job):
        ticket.status = RetrainingTicketStatus.RESOLVED


async def mark_lost(session: AsyncSession, job: RetrainingJob, reason: str) -> RetrainingJob:
    """The inference service no longer knows this job (it restarted - jobs live in its memory
    only). A non-terminal job can't be resumed, so it fails and its tickets are released for a
    fresh plan."""

    if RetrainingJobStatus(job.status) in TERMINAL:
        return job
    _move(job, S.FAILED)
    job.error = reason
    job.finished_at = datetime.now(UTC)
    await _release_tickets(session, job)
    await session.commit()
    await session.refresh(job)
    return job
