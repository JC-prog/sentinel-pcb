"""RetrainingTicket rows - created by the chat monitoring agent's flag_case_for_retraining when a
QA/Admin reviewer believes a Case's model verdict was wrong, or by the Work tab's bulk orchestrator
flagging a dataset sample it has no Case row for. A ticket only records the claim; jobs.py's
draft_job() is what turns open tickets into a retraining request, and doesn't care which origin a
ticket came from.
"""

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import RetrainingTicket, RetrainingTicketStatus


async def create_ticket(
    session: AsyncSession,
    *,
    flagged_by_user_id: str,
    reason: str,
    case_id: str | None = None,
    sample_ref: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    observed_label: str | None = None,
    correct_label: str | None = None,
    case_number: str | None = None,
) -> RetrainingTicket:
    """Exactly one of `case_id` (chat) / `sample_ref` (workflow) must identify what was flagged -
    also enforced by a DB CHECK constraint, but validated here first for a clear error rather than
    a raw IntegrityError."""

    if case_id is None and sample_ref is None:
        raise ValueError("create_ticket requires case_id or sample_ref")

    ticket = RetrainingTicket(
        case_id=case_id,
        case_number=case_number,
        sample_ref=sample_ref,
        flagged_by_user_id=flagged_by_user_id,
        reason=reason,
        model_name=model_name,
        model_version=model_version,
        observed_label=observed_label,
        correct_label=correct_label,
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def list_open_tickets(session: AsyncSession, model_name: str) -> list[RetrainingTicket]:
    """Tickets for `model_name` not yet part of any job, oldest first."""

    result = await session.scalars(
        select(RetrainingTicket)
        .where(
            RetrainingTicket.model_name == model_name,
            RetrainingTicket.status == RetrainingTicketStatus.OPEN,
            RetrainingTicket.job_id.is_(None),
        )
        .order_by(RetrainingTicket.created_at)
    )
    return list(result)


async def count_tickets(
    session: AsyncSession, *, status: RetrainingTicketStatus | None = None
) -> dict[str, int]:
    """model name -> ticket count (tickets with no model recorded are grouped under "")."""

    # literal_column, not a "" bind parameter: Postgres won't treat two separately-bound copies of
    # the same coalesce() as one expression, so the SELECT and GROUP BY would disagree.
    model = func.coalesce(RetrainingTicket.model_name, literal_column("''"))
    stmt = select(model, func.count()).group_by(model)
    if status is not None:
        stmt = stmt.where(RetrainingTicket.status == status)
    return {name: count for name, count in (await session.execute(stmt)).tuples()}
