"""RetrainingTicket rows - created by the chat relabel agent's confirm_relabel when a
QA/Admin reviewer believes a Case's model verdict was wrong, or by the Work tab's bulk orchestrator
flagging a dataset sample it has no Case row for. A ticket only records the claim; jobs.py's
draft_job() is what turns open tickets into a retraining request, and doesn't care which origin a
ticket came from.
"""

import logging

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models import (
    RetrainingJob,
    RetrainingTicket,
    RetrainingTicketStatus,
)
from app.shared.modelops import drift as drift_repo

logger = logging.getLogger(__name__)


async def create_ticket(
    session: AsyncSession,
    *,
    flagged_by_user_id: str,
    reason: str,
    case_id: str | None = None,
    sample_ref: str | None = None,
    run_id: str | None = None,
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
        run_id=run_id,
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
    await _notice_drift(session, ticket)
    return ticket


async def _notice_drift(session: AsyncSession, ticket: RetrainingTicket) -> None:
    """A correction may be the one that tips a model version into drift; raise the report for it.
    Never fails the correction: the ticket is already saved, so anything wrong here is logged."""

    try:
        await drift_repo.maybe_raise_report(session, ticket)
    except Exception:
        logger.exception("could not check drift after ticket %s", ticket.id)
        await session.rollback()
        # The rollback expires the ticket; reload it so the caller can still read it (it is saved).
        await session.refresh(ticket)


async def sample_refs_for_run(session: AsyncSession, run_id: str) -> dict[str, str]:
    """The samples of a Work-tab run that already have a ticket -> that ticket's status (open:
    added to training data, not in a plan yet; acknowledged: in a retraining plan; resolved:
    retrained)."""

    rows = await session.execute(
        select(RetrainingTicket.sample_ref, RetrainingTicket.status).where(
            RetrainingTicket.run_id == run_id, RetrainingTicket.sample_ref.is_not(None)
        )
    )
    return {ref: status for ref, status in rows.tuples() if ref is not None}


async def tickets_for_run(session: AsyncSession, run_id: str) -> dict[tuple[str, str | None], str]:
    """The same, keyed by (sample, model): one sample can carry a defect ticket and a region ticket,
    each with its own status."""

    rows = await session.execute(
        select(
            RetrainingTicket.sample_ref, RetrainingTicket.model_name, RetrainingTicket.status
        ).where(RetrainingTicket.run_id == run_id, RetrainingTicket.sample_ref.is_not(None))
    )
    return {(ref, model): status for ref, model, status in rows.tuples() if ref is not None}


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
    session: AsyncSession,
    *,
    status: RetrainingTicketStatus | None = None,
    run_id: str | None = None,
) -> dict[str, int]:
    """model name -> ticket count (tickets with no model recorded are grouped under ""), optionally
    only the tickets of one Work-tab run."""

    # literal_column, not a "" bind parameter: Postgres won't treat two separately-bound copies of
    # the same coalesce() as one expression, so the SELECT and GROUP BY would disagree.
    model = func.coalesce(RetrainingTicket.model_name, literal_column("''"))
    stmt = select(model, func.count()).group_by(model)
    if status is not None:
        stmt = stmt.where(RetrainingTicket.status == status)
    if run_id is not None:
        stmt = stmt.where(RetrainingTicket.run_id == run_id)
    return {name: count for name, count in (await session.execute(stmt)).tuples()}


async def list_flagged_by(
    session: AsyncSession, user_id: str, *, limit: int = 10
) -> list[tuple[RetrainingTicket, str | None]]:
    """The corrections a user confirmed (newest first), each with the status of the retraining job
    it is part of - None while it is not in any plan."""

    rows = await session.execute(
        select(RetrainingTicket, RetrainingJob.status)
        .outerjoin(RetrainingJob, RetrainingJob.id == RetrainingTicket.job_id)
        .where(RetrainingTicket.flagged_by_user_id == user_id)
        .order_by(RetrainingTicket.created_at.desc())
        .limit(limit)
    )
    return [(ticket, job_status) for ticket, job_status in rows.tuples()]


async def withdraw_ticket(session: AsyncSession, ticket: RetrainingTicket) -> None:
    """Removes a ticket that is not in any plan yet. The caller checks that; a ticket already
    drafted into a job is that job's record and is released by cancelling the job instead."""

    if ticket.job_id is not None or ticket.status != RetrainingTicketStatus.OPEN:
        raise ValueError("only an open ticket outside any plan can be withdrawn")
    await session.delete(ticket)
    await session.commit()
