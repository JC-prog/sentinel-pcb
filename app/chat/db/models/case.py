"""Persistence model for the case-management workflow: app/chat/agents/inspection_agent/ creates a
Case per flagged image (verdict REVIEW_REQUIRED or ACCEPTED); QA/Admin later resolve a
REVIEW_REQUIRED case via review_case (app/chat/agents/inspection_agent/tools.py), moving it to
APPROVED or OVERRIDDEN.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Identity, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CaseStatus(StrEnum):
    """ACCEPTED/REVIEW_REQUIRED are pipeline verdicts, set once by inspection_agent/graph.py's
    persist_case node and never changed after. APPROVED/OVERRIDDEN are terminal, human-set states
    from review_case - APPROVED confirms the flagged defect stands, OVERRIDDEN reverses a
    REVIEW_REQUIRED call (treated as a false positive). Only a REVIEW_REQUIRED case can transition
    to APPROVED/OVERRIDDEN - see app/chat/agents/inspection_agent/repository.py's resolve_case().
    """

    ACCEPTED = "accepted"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    OVERRIDDEN = "overridden"


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (Index("ix_cases_status_created_at", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Human-friendly identifier, e.g. "CASE-000123" - see the case_number property below and
    # repository.parse_case_number() for the inverse. A Postgres-generated identity column, not
    # app-generated, so it's race-safe under concurrent case creation without any app-level
    # locking.
    sequence_number: Mapped[int] = mapped_column(
        Integer, Identity(always=True), unique=True, nullable=False, index=True
    )

    created_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False)

    # Identifying fields - same names as case_agent/schemas.py's
    # ExplainabilityReviewRequest (board_id, component_ref); package/feature are additional,
    # needed to key the golden-image bank unambiguously.
    board_id: Mapped[str] = mapped_column(String, nullable=False)
    component_ref: Mapped[str] = mapped_column(String, nullable=False)
    package: Mapped[str | None] = mapped_column(String, nullable=True)
    feature: Mapped[str | None] = mapped_column(String, nullable=True)
    issue_symptom: Mapped[str | None] = mapped_column(String, nullable=True)

    # Uploaded artifacts - stored filenames (app.chat.uploads.service convention), not blob data.
    image_id: Mapped[str] = mapped_column(String, nullable=False)
    inspection_xml_id: Mapped[str | None] = mapped_column(String, nullable=True)
    golden_image_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("golden_images.id"), nullable=True
    )

    # Pipeline output - mirrors inspection_agent's AdcInspectionState fields worth persisting.
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    region_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    defect_model: Mapped[str | None] = mapped_column(String, nullable=True)
    # Exactly which weights answered ("<repo_id>@<revision>", from the inference service's
    # /classify) - so a verdict can be attributed to a model version, and drift measured per
    # version. None for cases created before versioning, or when the service didn't report one.
    region_model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_label: Mapped[str | None] = mapped_column(String, nullable=True)
    defect_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    defect_scores: Mapped[dict[str, float]] = mapped_column(JSONB, default=dict, nullable=False)
    # None means no inspection XML was supplied for this case - never faked to a pass or fail.
    measurement_validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Legacy: filled by the automatic hand-off to the explainability review that inspection used
    # to make on REVIEW_REQUIRED. That hand-off is gone (agents don't call each other), so new
    # cases leave this None; old rows keep whatever diagnosis they were given.
    explainability_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    observations: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    status: Mapped[str] = mapped_column(
        String, default=CaseStatus.REVIEW_REQUIRED, nullable=False, index=True
    )
    resolved_by_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    @property
    def case_number(self) -> str:
        """"CASE-000123" - derived from sequence_number rather than stored, so the display
        format can change later without a migration. See repository.parse_case_number() for the
        inverse."""

        return f"CASE-{self.sequence_number:06d}"
