"""The monitoring agent's work, free of any chat or LLM concern: summarise a model's drift, file a
drift report, draft a retraining plan, and the Admin overview. Reads and writes the shared
model-operations tables (app/shared/modelops/); the drift numbers come from Cases
(app/chat/services/drift.py).

Nothing here retrains anything or changes which model is live. The furthest a request goes is a
RetrainingJob in PENDING_APPROVAL - an Admin approves it (and promotes the result) in the Models tab.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.services.cases import get_case_by_sequence_number, parse_case_number
from app.chat.services.drift import drift_summary
from app.shared.db.models import (
    DriftReport,
    DriftReportStatus,
    ModelVersionStatus,
    RetrainingJob,
    RetrainingJobStatus,
    RetrainingTicketStatus,
)
from app.shared.modelops import drift as drift_repo
from app.shared.modelops import jobs as job_repo
from app.shared.modelops import tickets as ticket_repo
from app.shared.modelops import versions as version_repo

DEFAULT_WINDOW_DAYS = 7

# Jobs still on their way somewhere - what the Admin overview calls the retraining queue.
_ACTIVE_JOB_STATUSES = [
    RetrainingJobStatus.PENDING_APPROVAL,
    RetrainingJobStatus.APPROVED,
    RetrainingJobStatus.QUEUED,
    RetrainingJobStatus.RUNNING,
]


class PlanRefused(Exception):
    """A retraining plan that cannot be drafted, with the reason the user should hear."""


@dataclass(frozen=True)
class FiledReport:
    report: DriftReport
    evidence_cases: int
    unknown_case_numbers: list[str]
    summary: dict[str, Any]
    open_reports_for_model: int


@dataclass(frozen=True)
class DraftedPlan:
    job: RetrainingJob
    drift_reports_linked: int


async def drift_overview(session: AsyncSession, model: str, *, days: int) -> dict[str, Any]:
    """The drift summary plus what the user will want next to it: the live version and how many
    reports and tickets are already open for this model."""

    summary = await drift_summary(session, model, days=days)
    live = await version_repo.get_live_version(session, model)
    open_reports = await drift_repo.count_reports_by_model(session, status=DriftReportStatus.OPEN)
    open_tickets = await ticket_repo.count_tickets(session, status=RetrainingTicketStatus.OPEN)
    return {
        **summary,
        "live_version": live.version if live else None,
        "open_drift_reports": open_reports.get(model, 0),
        "open_retraining_tickets": open_tickets.get(model, 0),
    }


async def file_drift_report(
    session: AsyncSession,
    *,
    model: str,
    description: str,
    case_numbers: list[str],
    days: int,
    user_id: str,
) -> FiledReport:
    """Records the report with a snapshot of the current drift numbers and whichever of the named
    cases exist; names that match no case are reported back rather than failing the report."""

    case_ids: list[str] = []
    known_numbers: list[str] = []
    unknown: list[str] = []
    for number in case_numbers:
        sequence_number = parse_case_number(number)
        case = (
            await get_case_by_sequence_number(session, sequence_number)
            if sequence_number is not None
            else None
        )
        if case is None:
            unknown.append(number)
        else:
            case_ids.append(case.id)
            known_numbers.append(case.case_number)

    summary = await drift_summary(session, model, days=days)
    live = await version_repo.get_live_version(session, model)
    report = await drift_repo.create_drift_report(
        session,
        model_name=model,
        reported_by_user_id=user_id,
        description=description,
        model_version=live.version if live else None,
        case_ids=case_ids,
        case_numbers=known_numbers,
        stats=summary,
    )
    open_reports = await drift_repo.count_reports_by_model(session, status=DriftReportStatus.OPEN)
    return FiledReport(
        report=report,
        evidence_cases=len(case_ids),
        unknown_case_numbers=unknown,
        summary=summary,
        open_reports_for_model=open_reports.get(model, 0),
    )


async def draft_plan(
    session: AsyncSession, *, model: str, rationale: str, user_id: str
) -> DraftedPlan:
    """Turns the model's open tickets into a RetrainingJob awaiting Admin approval, linking its
    open drift reports. Writes a rationale from the drift signals when the user gave none."""

    open_reports = await drift_repo.list_drift_reports(
        session, model_name=model, status=DriftReportStatus.OPEN
    )
    if not rationale:
        summary = await drift_summary(session, model, days=DEFAULT_WINDOW_DAYS)
        rationale = default_rationale(model, len(open_reports), summary["signals"])

    try:
        job = await job_repo.draft_job(
            session,
            model_name=model,
            created_by_user_id=user_id,
            rationale=rationale,
            drift_report_ids=[r.id for r in open_reports],
        )
    except job_repo.NothingToRetrain as exc:
        raise PlanRefused(
            f"no open retraining tickets for {model} - correct the cases it got wrong first "
            "(relabel_case, then confirm_relabel)"
        ) from exc
    except job_repo.BaseVersionUnknown as exc:
        raise PlanRefused(
            f"cannot tell which version of {model} to retrain from - no live version is recorded "
            "yet. Open the Models tab so it syncs with the inference service, then try again."
        ) from exc
    return DraftedPlan(job=job, drift_reports_linked=len(open_reports))


def default_rationale(model: str, drift_reports: int, signals: list[str]) -> str:
    parts = [f"Retrain {model} on cases reviewers flagged as misclassified."]
    if drift_reports:
        parts.append(f"{drift_reports} open drift report(s) point at it.")
    if signals:
        parts.append("Recent indicators: " + "; ".join(signals) + ".")
    return " ".join(parts)


async def status_overview(session: AsyncSession) -> dict[str, Any]:
    live = [
        v for v in await version_repo.list_versions(session) if v.status == ModelVersionStatus.LIVE
    ]
    queue = await job_repo.list_jobs(session, statuses=_ACTIVE_JOB_STATUSES)
    overview: dict[str, Any] = {
        "live_models": {v.model_name: v.version for v in live},
        "open_drift_reports": await drift_repo.count_reports_by_model(
            session, status=DriftReportStatus.OPEN
        ),
        "open_retraining_tickets": await ticket_repo.count_tickets(
            session, status=RetrainingTicketStatus.OPEN
        ),
        "retraining_queue": [
            {
                "job_id": j.id,
                "model": j.model_name,
                "status": j.status,
                "samples": len(j.samples),
                "progress": j.progress,
            }
            for j in queue
        ],
    }
    if not live:
        overview["note"] = (
            "No model versions are recorded yet - the Models tab syncs them from the inference "
            "service."
        )
    return overview
