"""Drift reports: a person (via the chat monitoring agent) saying "this model looks like it has
drifted", with the evidence they pointed at and a snapshot of the numbers at that moment. The
numbers themselves are computed from Cases, which is chat's data - so the *caller* (chat) computes
them and hands them in as `stats`; this module only stores and counts reports.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import (
    DriftReport,
    DriftReportStatus,
    RetrainingTicket,
)
from app.shared.modelops.corrections import correction_trend

logger = logging.getLogger(__name__)


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


def _same_version(column: Any, version: str | None) -> Any:
    return column.is_(None) if version is None else column == version


async def maybe_raise_report(session: AsyncSession, ticket: RetrainingTicket) -> DriftReport | None:
    """Raises (or refreshes) a drift report when `ticket`'s model version has collected enough
    corrections lately. Called after every ticket is created - from chat's relabel and from the
    Work tab - so a correction can notice what no one filed by hand.

    One OPEN auto-raised report per model version: while it is open, further corrections update its
    counts and evidence instead of making another. Resolving it lets the next burst raise a new
    one. Reports a person filed by hand are not touched. Returns the report that was created or
    refreshed, or None when below the threshold / disabled / the ticket has no model.

    Best effort - the caller must not fail a correction because this did: see create_ticket."""

    threshold = settings.drift_auto_report_threshold
    if threshold <= 0 or not ticket.model_name:
        return None
    days = max(1, settings.drift_auto_report_window_days)
    since = datetime.now(UTC) - timedelta(days=days)

    recent = list(
        await session.scalars(
            select(RetrainingTicket)
            .where(
                RetrainingTicket.model_name == ticket.model_name,
                _same_version(RetrainingTicket.model_version, ticket.model_version),
                RetrainingTicket.created_at >= since,
            )
            .order_by(RetrainingTicket.created_at)
        )
    )
    if len(recent) < threshold:
        return None

    case_numbers = [t.case_number for t in recent if t.case_number]
    case_ids = [t.case_id for t in recent if t.case_id]
    sample_refs = [f"{t.run_id}:{t.sample_ref}" if t.run_id else t.sample_ref for t in recent if t.sample_ref]
    version = ticket.model_version or "unknown version"
    description = (
        f"{len(recent)} corrections to {ticket.model_name} ({version}) in the last {days} days."
    )
    # The model's correction trend (same shape as a report filed by hand), plus what is specific to
    # this one: it was raised automatically, and how many corrections this model version has.
    stats: dict[str, Any] = {
        **await correction_trend(session, ticket.model_name, days=days),
        "auto": True,
        "model_version": ticket.model_version,
        "version_corrections": len(recent),
        "threshold": threshold,
        "sample_refs": sample_refs,
    }

    open_reports = await session.scalars(
        select(DriftReport).where(
            DriftReport.model_name == ticket.model_name,
            _same_version(DriftReport.model_version, ticket.model_version),
            DriftReport.status == DriftReportStatus.OPEN,
        )
    )
    existing = next((r for r in open_reports if (r.stats or {}).get("auto")), None)
    if existing is not None:
        existing.description = description
        existing.case_ids = case_ids
        existing.case_numbers = case_numbers
        existing.stats = stats
        await session.commit()
        await session.refresh(existing)
        return existing

    return await create_drift_report(
        session,
        model_name=ticket.model_name,
        model_version=ticket.model_version,
        reported_by_user_id=ticket.flagged_by_user_id,
        description=description,
        case_ids=case_ids,
        case_numbers=case_numbers,
        stats=stats,
    )
