"""Drift indicators for a model, computed from the Cases it classified. Cases are chat's data, so
this lives here (the shared modelops code stores drift *reports* but can't read Cases); the
numbers are handed to it as a snapshot when a report is filed.

These are indicators, not a verdict: they compare the recent window with the window before it
(same length) and flag the metrics that moved. The signals a reviewer can act on are the human
ones - how often QA overrode the model's REVIEW_REQUIRED calls as false positives, and how many
cases had to go to review at all - alongside the classifier's own confidence.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, CaseStatus
from app.chat.services.cases import REGION_MODEL
from app.shared.config.settings import settings
from app.shared.db.models import RetrainingTicket

# Below this many cases in the recent window the rates are noise, and no signal is raised.
MIN_CASES_FOR_SIGNALS = 10

# How far a metric must move against the previous window to be called out.
_RATE_JUMP = 0.15
_CONFIDENCE_DROP = 0.10


@dataclass(frozen=True)
class WindowStats:
    total: int
    needs_review: int  # everything the pipeline did not simply ACCEPT
    approved: int  # reviewer confirmed the flagged defect
    overridden: int  # reviewer reversed it as a false positive
    low_confidence: int
    mean_confidence: float | None

    @property
    def resolved(self) -> int:
        return self.approved + self.overridden

    @property
    def review_rate(self) -> float | None:
        return self.needs_review / self.total if self.total else None

    @property
    def override_rate(self) -> float | None:
        """Of the cases a human has ruled on, the share they overrode - None until any have been."""

        return self.overridden / self.resolved if self.resolved else None

    @property
    def low_confidence_rate(self) -> float | None:
        return self.low_confidence / self.total if self.total else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cases": self.total,
            "needs_review": self.needs_review,
            "approved": self.approved,
            "overridden": self.overridden,
            "review_rate": _round(self.review_rate),
            "override_rate": _round(self.override_rate),
            "low_confidence_rate": _round(self.low_confidence_rate),
            "mean_confidence": _round(self.mean_confidence),
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _model_columns(
    model_name: str,
) -> tuple[ColumnElement[bool], Any, Any, float]:
    """(which cases this model classified, its version column, its confidence column, the
    confidence below which its verdict is called uncertain)."""

    if model_name == REGION_MODEL:
        return (
            Case.region.is_not(None),
            Case.region_model_version,
            Case.region_confidence,
            settings.adc_region_confidence_threshold,
        )
    return (
        Case.defect_model == model_name,
        Case.defect_model_version,
        Case.defect_confidence,
        settings.adc_defect_confidence_threshold,
    )


def _windowed(
    stmt: Select[Any],
    *,
    classified_by: ColumnElement[bool],
    since: datetime,
    until: datetime,
    version_col: Any,
    model_version: str | None,
) -> Select[Any]:
    stmt = stmt.where(classified_by, Case.created_at >= since, Case.created_at < until)
    if model_version is not None:
        stmt = stmt.where(version_col == model_version)
    return stmt


async def window_stats(
    session: AsyncSession,
    model_name: str,
    *,
    since: datetime,
    until: datetime,
    model_version: str | None = None,
) -> WindowStats:
    classified_by, version_col, confidence_col, low_below = _model_columns(model_name)
    stmt = _windowed(
        select(
            func.count(),
            func.count().filter(Case.status != CaseStatus.ACCEPTED),
            func.count().filter(Case.status == CaseStatus.APPROVED),
            func.count().filter(Case.status == CaseStatus.OVERRIDDEN),
            func.count().filter(confidence_col < low_below),
            func.avg(confidence_col),
        ),
        classified_by=classified_by,
        since=since,
        until=until,
        version_col=version_col,
        model_version=model_version,
    )
    total, needs_review, approved, overridden, low, mean = (await session.execute(stmt)).one()
    return WindowStats(
        total=total,
        needs_review=needs_review,
        approved=approved,
        overridden=overridden,
        low_confidence=low,
        mean_confidence=float(mean) if mean is not None else None,
    )


async def _versions_seen(
    session: AsyncSession, model_name: str, *, since: datetime, until: datetime
) -> list[str]:
    classified_by, version_col, _, _ = _model_columns(model_name)
    stmt = _windowed(
        select(version_col).distinct(),
        classified_by=classified_by,
        since=since,
        until=until,
        version_col=version_col,
        model_version=None,
    )
    return sorted(v for v in (await session.scalars(stmt)) if v)


def _signals(recent: WindowStats, previous: WindowStats) -> list[str]:
    """Human-readable notes on what moved against the previous window. Empty when there isn't
    enough recent data to say anything, or nothing moved enough."""

    if recent.total < MIN_CASES_FOR_SIGNALS:
        return []

    signals: list[str] = []
    for label, now, before in (
        ("override rate (false positives caught by reviewers)", recent.override_rate, previous.override_rate),
        ("share of cases needing review", recent.review_rate, previous.review_rate),
        ("share of low-confidence verdicts", recent.low_confidence_rate, previous.low_confidence_rate),
    ):
        if now is not None and before is not None and now - before >= _RATE_JUMP:
            signals.append(f"{label} rose from {before:.0%} to {now:.0%}")
    if (
        recent.mean_confidence is not None
        and previous.mean_confidence is not None
        and previous.mean_confidence - recent.mean_confidence >= _CONFIDENCE_DROP
    ):
        signals.append(
            f"mean confidence fell from {previous.mean_confidence:.2f} to {recent.mean_confidence:.2f}"
        )
    return signals


async def drift_summary(
    session: AsyncSession, model_name: str, *, days: int = 7, now: datetime | None = None
) -> dict[str, Any]:
    """The last `days` days against the `days` before them, per model version, plus how many
    retraining tickets were flagged in the recent window."""

    now = now or datetime.now(UTC)
    recent_start = now - timedelta(days=days)
    previous_start = recent_start - timedelta(days=days)
    # `until` is exclusive; nudge past `now` so a case created this instant is counted.
    recent_end = now + timedelta(seconds=1)

    recent = await window_stats(session, model_name, since=recent_start, until=recent_end)
    previous = await window_stats(session, model_name, since=previous_start, until=recent_start)
    by_version = {
        version: (
            await window_stats(
                session,
                model_name,
                since=recent_start,
                until=recent_end,
                model_version=version,
            )
        ).to_dict()
        for version in await _versions_seen(
            session, model_name, since=recent_start, until=recent_end
        )
    }
    flagged = await session.scalar(
        select(func.count()).where(
            RetrainingTicket.model_name == model_name, RetrainingTicket.created_at >= recent_start
        )
    )

    return {
        "model": model_name,
        "window_days": days,
        "recent": recent.to_dict(),
        "previous": previous.to_dict(),
        "by_version": by_version,
        "tickets_flagged_in_window": flagged or 0,
        "enough_data": recent.total >= MIN_CASES_FOR_SIGNALS,
        "signals": _signals(recent, previous),
    }
