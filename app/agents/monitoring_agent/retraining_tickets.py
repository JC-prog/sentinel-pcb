"""Repository for RetrainingTicket rows - queued by flag_case_for_retraining (tool.py) when a
QA/Admin reviewer believes a Case's model verdict was wrong. Creating a ticket here only queues
the request; actual model retraining happens on the separate inference server (app/inference/),
never in this app.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RetrainingTicket


async def create_ticket(
    session: AsyncSession, *, case_id: str, flagged_by_user_id: str, reason: str
) -> RetrainingTicket:
    ticket = RetrainingTicket(case_id=case_id, flagged_by_user_id=flagged_by_user_id, reason=reason)
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket
