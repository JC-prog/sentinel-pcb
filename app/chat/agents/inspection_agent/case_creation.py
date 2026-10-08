"""Turning an inspection into a Case - only after the user has said they want one.

`inspect_image` reports an inspection and parks it (drafts.py); this commits it. The confirmation is
enforced here, not left to the model's good manners - see app/chat/services/confirmation.py: a draft
can only be committed in a LATER chat turn than the one that parked it, so the user always has had a
turn to answer. Committing is also what makes it a one-shot: the draft is deleted in the same commit
that creates the Case, so a second call finds nothing to create.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agents.inspection_agent import drafts
from app.chat.agents.inspection_agent.errors import CaseRefused
from app.chat.db.models import Case
from app.chat.services import cases
from app.chat.services.confirmation import ConfirmationProblem, confirmation_problem
from app.shared.config.langfuse import traced

_REFUSALS = {
    ConfirmationProblem.NOTHING_PENDING: (
        "there is no inspection waiting to be saved as a case - inspect the image with "
        "inspect_image first."
    ),
    ConfirmationProblem.PROPOSED_BY_SOMEONE_ELSE: "that inspection belongs to someone else.",
    ConfirmationProblem.SAME_TURN: (
        "the user has not said they want a case yet - ask them whether to create one, and wait for "
        "their answer in their next message."
    ),
}


@traced("case-create")
async def create_from_draft(
    session: AsyncSession, *, conversation_id: str, user_id: str, turn_started_at: datetime
) -> Case:
    draft = await drafts.get_draft(session, conversation_id=conversation_id, user_id=user_id)

    problem = confirmation_problem(
        pending_at=draft.created_at if draft else None,
        pending_by_user_id=draft.user_id if draft else None,
        user_id=user_id,
        turn_started_at=turn_started_at,
    )
    if draft is None or problem is not None:
        raise CaseRefused(_REFUSALS[problem or ConfirmationProblem.NOTHING_PENDING])

    payload = dict(draft.payload)
    await session.delete(draft)  # create_case commits, so the draft goes in the same transaction
    return await cases.create_case(
        session, created_by_user_id=user_id, conversation_id=conversation_id, **payload
    )
