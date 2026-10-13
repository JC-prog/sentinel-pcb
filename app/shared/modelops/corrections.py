"""How often a model's calls are being corrected, per model version: what chat's "is the model
drifting?" answer and a filed drift report's snapshot are made of.

It counts retraining tickets - one per confirmed correction, from chat's relabel or the Work tab's
queued corrections - so both origins are in one number, and it is the same signal that raises a
drift report on its own (drift.maybe_raise_report).

It is a count of corrections, not an error rate. People correct the labels they are shown, so there
is no denominator of "everything the model classified" here; a run's own rate (right and wrong over
what it classified) is app/shared/modelops/run_drift.py. The summary says so, so nobody reads a
count as an accuracy.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config.settings import settings
from app.shared.db.models import RetrainingTicket

UNKNOWN_VERSION = "unknown version"

# A change this large (and at least this many corrections) is worth calling out.
_MIN_RISE = 3
_RISE_FACTOR = 2

BASIS = (
    "Counts corrections people confirmed. It is not the model's error rate: only labels someone "
    "looked at and corrected are counted. For a run's rate use the run's drift."
)


async def _counts(
    session: AsyncSession, model_name: str, *, since: datetime, until: datetime
) -> dict[str, int]:
    version = func.coalesce(RetrainingTicket.model_version, UNKNOWN_VERSION)
    rows = await session.execute(
        select(version, func.count())
        .where(
            RetrainingTicket.model_name == model_name,
            RetrainingTicket.created_at >= since,
            RetrainingTicket.created_at < until,
        )
        .group_by(version)
    )
    return {name: count for name, count in rows.tuples()}


def _signals(
    recent: int, previous: int, days: int, recent_by_version: dict[str, int]
) -> list[str]:
    signals: list[str] = []
    if previous == 0 and recent >= _MIN_RISE:
        signals.append(f"{recent} corrections in the last {days} days, none in the {days} before")
    elif previous > 0 and recent >= previous * _RISE_FACTOR and recent - previous >= _MIN_RISE:
        signals.append(f"corrections rose from {previous} to {recent} against the previous {days} days")

    threshold = settings.drift_auto_report_threshold
    if threshold > 0:
        for version, count in sorted(recent_by_version.items(), key=lambda kv: -kv[1]):
            if count >= threshold:
                signals.append(
                    f"{version} has {count} corrections in the last {days} days "
                    f"(a drift report is raised at {threshold})"
                )
    return signals


async def correction_trend(
    session: AsyncSession, model_name: str, *, days: int = 7, now: datetime | None = None
) -> dict[str, Any]:
    """The last `days` days of corrections for a model against the `days` before them, with the
    split by model version and the automatic-report threshold, in the shape chat reads and a drift
    report stores."""

    now = now or datetime.now(UTC)
    recent_start = now - timedelta(days=days)
    previous_start = recent_start - timedelta(days=days)
    # `until` is exclusive; nudge past `now` so a correction made this instant is counted.
    recent_by_version = await _counts(
        session, model_name, since=recent_start, until=now + timedelta(seconds=1)
    )
    previous_by_version = await _counts(
        session, model_name, since=previous_start, until=recent_start
    )
    recent = sum(recent_by_version.values())
    previous = sum(previous_by_version.values())
    versions = sorted(set(recent_by_version) | set(previous_by_version))

    return {
        "model": model_name,
        "window_days": days,
        "corrections": {"recent": recent, "previous": previous},
        "by_version": {
            v: {"recent": recent_by_version.get(v, 0), "previous": previous_by_version.get(v, 0)}
            for v in versions
        },
        "auto_report": {
            "enabled": settings.drift_auto_report_threshold > 0,
            "threshold": settings.drift_auto_report_threshold,
            "window_days": settings.drift_auto_report_window_days,
        },
        "enough_data": recent > 0,
        "signals": _signals(recent, previous, days, recent_by_version),
        "basis": BASIS,
    }
