"""Persistence models for the workflow lifecycle.

Scope: one image = one workflow (no `ComponentSubCase` fan-out yet - see
`planning/docs/SCENARIOS.md` Scenarios 4/5 for the multi-component design this deliberately
doesn't implement in this pass). Trimmed from `planning/docs/DATA-MODEL.md`'s `Workflow` /
`AuditEvent` entities to what this scope actually needs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class WorkflowStatus(StrEnum):
    """Mirrors `planning/docs/DATA-MODEL.md` §2, trimmed to the single-component states this
    build pass actually reaches. Stored as a plain string column (not a native Postgres enum) so
    adding a state later is a code change, not a schema migration.
    """

    RECEIVED = "RECEIVED"
    PREPARING = "PREPARING"
    QUALITY_CHECK = "QUALITY_CHECK"
    INFERENCE = "INFERENCE"
    COMPLETED = "COMPLETED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REJECTED_QUALITY = "REJECTED_QUALITY"

    @property
    def is_terminal(self) -> bool:
        """No reviewer-action endpoint exists yet, so HUMAN_REVIEW is treated as terminal for
        this pass too - see the plan's "Notable choices" section.
        """
        return self in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.HUMAN_REVIEW,
            WorkflowStatus.REJECTED_QUALITY,
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String, default=WorkflowStatus.RECEIVED, nullable=False)

    # Replaces the frontend's previously-hardcoded metadata: '{}' with real, queryable fields.
    board_id: Mapped[str | None] = mapped_column(String, nullable=True)
    component_id: Mapped[str | None] = mapped_column(String, nullable=True)
    recipe_id: Mapped[str | None] = mapped_column(String, nullable=True)

    image_filename: Mapped[str] = mapped_column(String, nullable=False)
    image_path: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)

    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    detections: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="workflow", order_by="AuditEvent.created_at", cascade="all, delete-orphan"
    )


class AuditEvent(Base):
    """Append-only trail of every state transition a workflow goes through - what the Tracing
    SSE endpoint (`GET /workflows/{id}/trace`) reads.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), nullable=False)

    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    workflow: Mapped[Workflow] = relationship(back_populates="audit_events")
