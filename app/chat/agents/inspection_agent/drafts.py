"""The inspection waiting for the user's yes: parked by the pipeline, turned into a Case by
`create_case` (see app/chat/db/models/case_draft.py). Only writes - the rules for when a draft may
be committed are in case_creation.py."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.db.models import CaseDraft


async def get_draft(session: AsyncSession, *, conversation_id: str, user_id: str) -> CaseDraft | None:
    result = await session.scalars(
        select(CaseDraft).where(
            CaseDraft.conversation_id == conversation_id, CaseDraft.user_id == user_id
        )
    )
    return result.first()


async def save_draft(
    session: AsyncSession, *, conversation_id: str, user_id: str, payload: dict[str, Any]
) -> CaseDraft:
    """Parks `payload` as this user's pending inspection in the conversation, replacing the one a
    previous inspection left. Its `created_at` is when this turn parked it - the moment the
    confirmation rule counts a later turn from."""

    draft = await get_draft(session, conversation_id=conversation_id, user_id=user_id)
    if draft is None:
        draft = CaseDraft(conversation_id=conversation_id, user_id=user_id, payload=payload)
        session.add(draft)
    else:
        draft.payload = payload
        draft.created_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(draft)
    return draft
