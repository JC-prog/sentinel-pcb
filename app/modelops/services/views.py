"""Read models for the Models tab: joins records into the shapes the UI shows (usernames instead of
user ids, per-model roll-ups), refreshing from the inference service first where that's cheap."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modelops.services import reconcile
from app.modelops.services.schemas import (
    DriftOverview,
    DriftReportOut,
    JobDetailOut,
    JobOut,
    ModelOut,
    ModelsOverview,
    ModelVersionOut,
    QueueOverview,
    SampleOut,
    TicketCounts,
)
from app.shared.db.models import (
    DriftReport,
    DriftReportStatus,
    ModelVersion,
    ModelVersionStatus,
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicketStatus,
    User,
)
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import tickets as ticket_repo
from app.shared.modelops import versions as version_repo

_IN_PROGRESS = (
    RetrainingJobStatus.PENDING_APPROVAL,
    RetrainingJobStatus.APPROVED,
    RetrainingJobStatus.QUEUED,
    RetrainingJobStatus.RUNNING,
)


async def usernames(session: AsyncSession, user_ids: Iterable[str | None]) -> dict[str, str]:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = await session.execute(select(User.id, User.username).where(User.id.in_(ids)))
    return {user_id: username for user_id, username in rows.tuples()}


def _version_out(version: ModelVersion, names: dict[str, str]) -> ModelVersionOut:
    out = ModelVersionOut.model_validate(version)
    out.activated_by = names.get(version.activated_by_user_id or "")
    return out


async def models_overview(session: AsyncSession) -> ModelsOverview:
    """Syncs with the inference service first (it is the authority on what is live), so opening the
    tab is what keeps the registry current. If the service can't be reached the overview is built
    from what the database last knew, and `inference` says so."""

    inference_status, infos = await reconcile.sync_versions(session)
    if inference_status.reachable:
        await reconcile.refresh_active_jobs(session)
    live_info = {info.name: info for info in infos}

    versions = await version_repo.list_versions(session)
    names = await usernames(session, (v.activated_by_user_id for v in versions))
    open_drift = await drift_repo.count_reports_by_model(session, status=DriftReportStatus.OPEN)
    open_tickets = await ticket_repo.count_tickets(session, status=RetrainingTicketStatus.OPEN)
    active_jobs: dict[str, int] = {}
    for job in await job_repo.list_jobs(session, statuses=_IN_PROGRESS, limit=500):
        active_jobs[job.model_name] = active_jobs.get(job.model_name, 0) + 1

    by_model: dict[str, list[ModelVersion]] = {}
    for version in versions:
        by_model.setdefault(version.model_name, []).append(version)

    models = []
    for name in sorted(by_model):
        rows = by_model[name]
        live = next((v for v in rows if v.status == ModelVersionStatus.LIVE), None)
        previous = next((v for v in rows if v.status == ModelVersionStatus.PREVIOUS), None)
        info = live_info.get(name)
        models.append(
            ModelOut(
                name=name,
                live=_version_out(live, names) if live else None,
                previous=_version_out(previous, names) if previous else None,
                versions=[_version_out(v, names) for v in rows],
                labels=info.labels if info else None,
                loaded_at=info.loaded_at if info else None,
                open_drift_reports=open_drift.get(name, 0),
                open_tickets=open_tickets.get(name, 0),
                active_jobs=active_jobs.get(name, 0),
            )
        )
    return ModelsOverview(inference=inference_status, models=models)


async def drift_overview(
    session: AsyncSession,
    *,
    days: int,
    model: str | None,
    status: DriftReportStatus | None,
    limit: int,
) -> DriftOverview:
    since = datetime.now(UTC) - timedelta(days=days)
    reports = await drift_repo.list_drift_reports(
        session, model_name=model, status=status, limit=limit
    )
    names = await usernames(session, (r.reported_by_user_id for r in reports))
    return DriftOverview(
        window_days=days,
        open_by_model=await drift_repo.count_reports_by_model(
            session, status=DriftReportStatus.OPEN
        ),
        reported_in_window_by_model=await drift_repo.count_reports_by_model(session, since=since),
        reports=[drift_report_out(r, names) for r in reports],
    )


def drift_report_out(report: DriftReport, names: dict[str, str]) -> DriftReportOut:
    return DriftReportOut(
        id=report.id,
        model_name=report.model_name,
        model_version=report.model_version,
        description=report.description,
        case_numbers=list(report.case_numbers),
        stats=report.stats,
        status=report.status,
        reported_by=names.get(report.reported_by_user_id, "unknown"),
        created_at=report.created_at,
        resolved_at=report.resolved_at,
    )


async def job_out(session: AsyncSession, jobs: list[RetrainingJob]) -> list[JobOut]:
    names = await usernames(
        session, [u for j in jobs for u in (j.created_by_user_id, j.approved_by_user_id)]
    )
    return [_job_out(j, names) for j in jobs]


def _job_out(job: RetrainingJob, names: dict[str, str]) -> JobOut:
    return JobOut(
        id=job.id,
        model_name=job.model_name,
        base_version=job.base_version,
        status=job.status,
        rationale=job.rationale,
        sample_count=len(job.samples),
        drift_report_ids=list(job.drift_report_ids),
        created_by=names.get(job.created_by_user_id, "unknown"),
        approved_by=names.get(job.approved_by_user_id or ""),
        approved_at=job.approved_at,
        external_job_id=job.external_job_id,
        progress=job.progress,
        error=job.error,
        artifact_repo_id=job.artifact_repo_id,
        artifact_revision=job.artifact_revision,
        simulated=job.simulated,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


async def job_detail(session: AsyncSession, job: RetrainingJob) -> JobDetailOut:
    (summary,) = await job_out(session, [job])
    return JobDetailOut(**summary.model_dump(), samples=[SampleOut(**s) for s in job.samples])


async def queue_overview(
    session: AsyncSession,
    *,
    statuses: list[RetrainingJobStatus] | None,
    model: str | None,
    limit: int,
) -> QueueOverview:
    inference_status, _ = await reconcile.sync_versions(session)
    if inference_status.reachable:
        await reconcile.refresh_active_jobs(session)

    jobs = await job_repo.list_jobs(session, statuses=statuses, model_name=model, limit=limit)
    counts = {
        status: sum((await ticket_repo.count_tickets(session, status=status)).values())
        for status in RetrainingTicketStatus
    }
    return QueueOverview(
        inference=inference_status,
        tickets=TicketCounts(
            open=counts[RetrainingTicketStatus.OPEN],
            acknowledged=counts[RetrainingTicketStatus.ACKNOWLEDGED],
            resolved=counts[RetrainingTicketStatus.RESOLVED],
        ),
        jobs=await job_out(session, jobs),
    )
