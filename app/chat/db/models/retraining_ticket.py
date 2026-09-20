"""Persistence model for flagged-for-retraining Cases: a QA/Admin user who believes a Case's
model verdict was wrong creates a RetrainingTicket via app/chat/agents/monitoring_agent's
flag_case_for_retraining tool. This only queues the request for engineering to act on - actual
model retraining happens on the separate inference server (app/shared/inference/), never here.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RetrainingTicketStatus(StrEnum):
    """OPEN is the only status this app ever sets - ACKNOWLEDGED/RESOLVED exist for engineering to
    mark manually (e.g. via a future admin endpoint or direct DB access) once they've picked up or
    finished acting on the ticket. Never set programmatically here."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class RetrainingTicket(Base):
    __tablename__ = "retraining_tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    case_id: Mapped[str] = mapped_column(String, ForeignKey("cases.id"), nullable=False, index=True)
    flagged_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False
    )
    # Required, not optional - flag_case_for_retraining rejects an empty reason before this row
    # is ever created, so a bare "I don't like this verdict" flag is never silently accepted.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default=RetrainingTicketStatus.OPEN, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
