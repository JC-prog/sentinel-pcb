"""The record of chat inspections (app/chat/db/models/inspection_log.py): written when an image is
inspected, read to say how many were, and updated when a person confirms a label was right. All of
it best effort for the inspection itself - a failure here must never cost the user their result.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import Case, InspectionLog, Message
from app.chat.services.cases import REGION_MODEL
from app.shared.config.settings import settings
from app.shared.db.models import RetrainingTicket

logger = logging.getLogger(__name__)


async def record(
    session: AsyncSession,
    *,
    user_id: str,
    conversation_id: str,
    case_fields: dict[str, Any],
) -> InspectionLog | None:
    """Logs one inspection from the fields the pipeline parks for a draft. Never raises."""

    if not settings.inspection_log_enabled:
        return None
    try:
        row = InspectionLog(
            user_id=user_id,
            conversation_id=conversation_id,
            board_id=case_fields.get("board_id"),
            component_ref=case_fields.get("component_ref"),
            image_id=case_fields.get("image_id"),
            region=case_fields.get("region"),
            region_confidence=case_fields.get("region_confidence"),
            region_model_version=case_fields.get("region_model_version"),
            defect_model=case_fields.get("defect_model"),
            defect_model_version=case_fields.get("defect_model_version"),
            defect_label=case_fields.get("defect_label"),
            defect_confidence=case_fields.get("defect_confidence"),
            verdict=str(case_fields.get("status") or "review_required"),
        )
        session.add(row)
        await session.commit()
        return row
    except Exception:
        logger.exception("could not log the inspection for conversation %s", conversation_id)
        await session.rollback()
        return None


async def latest_for(
    session: AsyncSession, *, conversation_id: str, user_id: str
) -> InspectionLog | None:
    row: InspectionLog | None = await session.scalar(
        select(InspectionLog)
        .where(InspectionLog.conversation_id == conversation_id, InspectionLog.user_id == user_id)
        .order_by(InspectionLog.created_at.desc())
        .limit(1)
    )
    return row


class CannotConfirm(Exception):
    """A confirmation that cannot be recorded, with the reason the user should hear."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def confirm_correct(
    session: AsyncSession, *, conversation_id: str, user_id: str
) -> InspectionLog:
    """Records that the person looked at the latest inspection and the model's label was right."""

    row = await latest_for(session, conversation_id=conversation_id, user_id=user_id)
    if row is None:
        raise CannotConfirm(
            "there is no inspection in this conversation to confirm - inspect an image first."
        )
    if not row.defect_label:
        raise CannotConfirm(
            "that inspection has no defect label (the region was too uncertain for a defect "
            "model), so there is no label to confirm."
        )
    if row.verified:
        raise CannotConfirm("that inspection's label was already confirmed as right.")

    # If it became a case and someone corrected the label, it was not right.
    case = await session.scalar(
        select(Case)
        .where(
            Case.conversation_id == conversation_id,
            Case.created_by_user_id == user_id,
            Case.image_id == row.image_id,
        )
        .order_by(Case.created_at.desc())
        .limit(1)
    )
    if case is not None and case.corrected_label is not None:
        raise CannotConfirm(
            f"{case.case_number} was corrected to {case.corrected_label!r}, so its label was not right."
        )

    row.verified = True
    row.verified_by_user_id = user_id
    row.verified_at = datetime.now(UTC)
    await session.commit()
    return row


@dataclass(frozen=True)
class Window:
    inspected: int
    corrected: int
    confirmed_correct: int


async def _window(
    session: AsyncSession, model: str, *, since: datetime, until: datetime
) -> Window:
    in_window = [InspectionLog.created_at >= since, InspectionLog.created_at < until]
    scope: list[ColumnElement[bool]] = (
        [InspectionLog.region.is_not(None)]
        if model == REGION_MODEL
        else [InspectionLog.defect_model == model]
    )
    inspected, confirmed = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(InspectionLog.verified.is_(True)),
            ).where(*scope, *in_window)
        )
    ).one()
    # Corrections that came from chat (a Case id), not the Work tab's runs - the ratio below only
    # makes sense against chat's own inspections.
    corrected = await session.scalar(
        select(func.count()).where(
            RetrainingTicket.model_name == model,
            RetrainingTicket.case_id.is_not(None),
            RetrainingTicket.created_at >= since,
            RetrainingTicket.created_at < until,
        )
    )
    return Window(inspected=inspected, corrected=corrected or 0, confirmed_correct=confirmed)


async def chat_activity(
    session: AsyncSession, model: str, *, days: int, now: datetime | None = None
) -> dict[str, Any]:
    """Chat's own inspections of a model against the window before: how many, how many a person
    corrected, how many a person confirmed were right. The share corrected is only given when there
    is something to divide by, and is the share of what chat inspected - not of the Work tab's runs."""

    now = now or datetime.now(UTC)
    recent_start = now - timedelta(days=days)
    previous_start = recent_start - timedelta(days=days)
    recent = await _window(
        session, model, since=recent_start, until=now + timedelta(seconds=1)
    )
    previous = await _window(session, model, since=previous_start, until=recent_start)
    share = recent.corrected / recent.inspected if recent.inspected else None
    return {
        "enabled": settings.inspection_log_enabled,
        "recent": {
            "inspected": recent.inspected,
            "corrected": recent.corrected,
            "confirmed_correct": recent.confirmed_correct,
        },
        "previous": {
            "inspected": previous.inspected,
            "corrected": previous.corrected,
            "confirmed_correct": previous.confirmed_correct,
        },
        "correction_share": round(share, 3) if share is not None else None,
        "note": (
            "Chat inspections only (the Work tab's runs are not counted here). A person has to "
            "correct or confirm a result for it to count either way, so the share is a floor."
        ),
    }


async def component_history(
    session: AsyncSession, *, board_id: str, component_ref: str, limit: int = 10
) -> dict[str, Any]:
    """What chat has inspected for one board and component: how many times, what the model said and
    how often a person confirmed or corrected it. Case-insensitive on both names."""

    where = [
        func.lower(InspectionLog.board_id) == board_id.strip().lower(),
        func.lower(InspectionLog.component_ref) == component_ref.strip().lower(),
    ]
    total = await session.scalar(select(func.count()).where(*where)) or 0
    confirmed = (
        await session.scalar(
            select(func.count()).where(*where, InspectionLog.verified.is_(True))
        )
        or 0
    )
    rows = list(
        await session.scalars(
            select(InspectionLog).where(*where).order_by(InspectionLog.created_at.desc()).limit(limit)
        )
    )
    labels = await session.execute(
        select(InspectionLog.defect_label, func.count())
        .where(*where)
        .group_by(InspectionLog.defect_label)
        .order_by(func.count().desc())
    )
    return {
        "inspected": total,
        "confirmed_right": confirmed,
        "model_labels": {(label or "not classified"): n for label, n in labels.tuples()},
        "recent": [
            {
                "when": r.created_at.isoformat(),
                "verdict": r.verdict,
                "region": r.region,
                "model": r.defect_model,
                "model_version": r.defect_model_version,
                "model_label": r.defect_label,
                "confidence": r.defect_confidence,
                "confirmed_right": r.verified,
            }
            for r in rows
        ],
    }


async def activity(
    session: AsyncSession, *, since: datetime, until: datetime | None = None
) -> dict[str, Any]:
    """What happened in chat over a period: inspections (by verdict and by model), how many a person
    confirmed right or corrected, cases created, and how replies were rated. Counts only."""

    until = until or datetime.now(UTC) + timedelta(seconds=1)
    window = [InspectionLog.created_at >= since, InspectionLog.created_at < until]
    inspected = await session.scalar(select(func.count()).where(*window)) or 0
    by_verdict = await session.execute(
        select(InspectionLog.verdict, func.count()).where(*window).group_by(InspectionLog.verdict)
    )
    by_model = await session.execute(
        select(InspectionLog.defect_model, func.count())
        .where(*window)
        .group_by(InspectionLog.defect_model)
    )
    confirmed = (
        await session.scalar(
            select(func.count()).where(*window, InspectionLog.verified.is_(True))
        )
        or 0
    )
    cases_made = (
        await session.scalar(
            select(func.count()).where(Case.created_at >= since, Case.created_at < until)
        )
        or 0
    )
    cases_corrected = (
        await session.scalar(
            select(func.count()).where(
                Case.corrected_at.is_not(None), Case.corrected_at >= since, Case.corrected_at < until
            )
        )
        or 0
    )
    ratings = await session.execute(
        select(Message.feedback, func.count())
        .where(
            Message.feedback.is_not(None),
            Message.role == "assistant",
            Message.created_at >= since,
            Message.created_at < until,
        )
        .group_by(Message.feedback)
    )
    return {
        "inspected": inspected,
        "by_verdict": {verdict: n for verdict, n in by_verdict.tuples()},
        "by_model": {(model or "no defect model ran"): n for model, n in by_model.tuples()},
        "confirmed_right": confirmed,
        "cases_created": cases_made,
        "cases_corrected": cases_corrected,
        "replies_rated": {value: n for value, n in ratings.tuples()},
    }
