"""Resolver sub-agent: which Case is the user correcting. The numbered case, or - when they said
"it" / "that case" - the latest in this conversation; and only a case the defect model actually
classified, since a correction is of that model's label."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.relabel_agent.errors import RelabelRefused
from app.chat.db.models import Case
from app.chat.services.cases import resolve_case_ref


async def resolve_case(session: AsyncSession, case_number: str | None, conversation_id: str) -> Case:
    case, error = await resolve_case_ref(session, case_number, conversation_id)
    if case is None:
        raise RelabelRefused(error or "case not found")

    if not case.defect_model or not case.defect_label:
        raise RelabelRefused(
            f"{case.case_number} has no defect classification to correct - the region was too "
            "uncertain for a defect model to run."
        )
    return case
