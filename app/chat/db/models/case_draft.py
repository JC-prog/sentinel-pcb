"""An image inspection waiting for the user to say whether it should become a Case.

`inspect_image` classifies the attached image and reports the result, but saves nothing: a Case is
for an inspection the user wants to keep working on (relabel it, review it), so they are asked
first. What the inspection found waits here - as exactly the keyword arguments
`app/chat/services/cases.create_case` takes - until a later chat turn confirms (`create_case`
tool), the next inspection replaces it, or the conversation is deleted. One per (conversation,
user); it is a row, not memory, so the backend stays stateless per request.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CaseDraft(Base):
    __tablename__ = "inspection_drafts"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_inspection_drafts_conversation_user"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    # create_case's keyword arguments, minus created_by_user_id / conversation_id (the columns above).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
