"""Drift reports: a person (via the chat monitoring agent) saying "this model looks like it has
drifted", with the evidence they pointed at and a snapshot of the numbers at that moment. The
numbers themselves are computed from Cases, which is chat's data - so the *caller* (chat) computes
them and hands them in as `stats`; this module only stores and counts reports.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import DriftReport, DriftReportStatus


async def create_drift_report(
    session: AsyncSession,
    *,
    model_name: str,
    reported_by_user_id: str,
    description: str,
    model_version: str | None = None,
    case_ids: Sequence[str] = (),
    case_numbers: Sequence[str] = (),
    stats: dict[str, Any] | None = None,
) -> DriftReport:
    report = DriftReport(
        model_name=model_name,
        model_version=model_version,
        reported_by_user_id=reported_by_user_id,
        description=description,
        case_ids=list(case_ids),
        case_numbers=list(case_numbers),
        stats=stats or {},
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def get_drift_report(session: AsyncSession, report_id: str) -> DriftReport | None:
    return await session.get(DriftReport, report_id)


async def list_drift_reports(
    session: AsyncSession,
    *,
    model_name: str | None = None,
    status: DriftReportStatus | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[DriftReport]:
    """Newest first."""

    stmt = select(DriftReport).order_by(DriftReport.created_at.desc()).limit(limit)
    if model_name is not None:
        stmt = stmt.where(DriftReport.model_name == model_name)
    if status is not None:
        stmt = stmt.where(DriftReport.status == status)
    if since is not None:
        stmt = stmt.where(DriftReport.created_at >= since)
    return list(await session.scalars(stmt))


async def count_reports_by_model(
    session: AsyncSession, *, status: DriftReportStatus | None = None, since: datetime | None = None
) -> dict[str, int]:
    """model name -> number of reports (optionally only open ones / only since a date)."""

    stmt = select(DriftReport.model_name, func.count()).group_by(DriftReport.model_name)
    if status is not None:
        stmt = stmt.where(DriftReport.status == status)
    if since is not None:
        stmt = stmt.where(DriftReport.created_at >= since)
    return {name: count for name, count in (await session.execute(stmt)).tuples()}


async def resolve_drift_report(session: AsyncSession, report: DriftReport) -> DriftReport:
    report.status = DriftReportStatus.RESOLVED
    report.resolved_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(report)
    return report
