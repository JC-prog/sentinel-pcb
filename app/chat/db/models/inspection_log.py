"""One row per image inspection run in chat - the record of what was inspected, kept whether or not
anyone acts on it. Without it the only thing known about chat inspections is the mistakes (a Case is
made when someone corrects a label), so no rate can be formed: "how many did we inspect?" and "how
often was the model right?" have no denominator.

It holds the verdict and the model versions that answered, never the image. `verified` is a person
saying the model's label was right - the positive half of the feedback that corrections alone cannot
give.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InspectionLog(Base):
    __tablename__ = "inspection_log"
    __table_args__ = (
        Index("ix_inspection_log_defect_model_created_at", "defect_model", "created_at"),
        Index("ix_inspection_log_conversation_user", "conversation_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False, index=True)
    # Not a foreign key: deleting a conversation must not erase what was inspected.
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    board_id: Mapped[str | None] = mapped_column(String, nullable=True)
    component_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    image_id: Mapped[str | None] = mapped_column(String, nullable=True)

    region: Mapped[str | None] = mapped_column(String, nullable=True)
    region_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_model: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_label: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String, nullable=False)

    # A person said the model's label was right. False until someone does.
    verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    verified_by_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
