"""An image inspection waiting for the user to say whether it should become a Case.

`inspect_image` classifies the attached image and reports the result, but saves nothing: a Case is
for an inspection the user wants to keep working on (relabel it, review it), so they are asked
first. What the inspection found waits here - as exactly the keyword arguments
`app/chat/services/cases.create_case` takes - until a later chat turn confirms (`create_case`
tool), an inspection in a later turn replaces it, or the conversation is deleted. One per
(conversation, user, image_number): the images inspected in one turn each wait here, numbered as
they were attached, and the next turn's inspections replace them all. It is a row, not memory, so
the backend stays stateless per request.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CaseDraft(Base):
    __tablename__ = "inspection_drafts"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "user_id",
            "image_number",
            name="uq_inspection_drafts_conversation_user_image",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    # Which attached image this was, counting from 1, in the message that attached it.
    image_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # create_case's keyword arguments, minus created_by_user_id / conversation_id (the columns above).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    # A relabel proposed on this inspection before it is a Case (the same two-step shape as the
    # Case's own `pending_*`; a draft is per user, so there is no pending-by column). Confirming it
    # in a later turn creates the Case with the correction and its ticket in one commit. A new
    # inspection replaces the draft and clears this.
    pending_label: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Which model the pending relabel corrects: the defect model, or pcb_region.
    pending_model: Mapped[str | None] = mapped_column(String, nullable=True)
    # An approve/override proposed on this inspection before it is a Case (the review agent).
    # Confirming it creates the Case already resolved.
    pending_resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_resolution_note: Mapped[str | None] = mapped_column(String, nullable=True)
    pending_resolution_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
