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

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

EMBEDDING_DIM = 384  # sentence-transformers/all-MiniLM-L6-v2's output dimension


class WorkflowStatus(StrEnum):
    """Mirrors `planning/docs/DATA-MODEL.md` §2, trimmed to the single-component states this
    build pass actually reaches. Stored as a plain string column (not a native Postgres enum) so
    adding a state later is a code change, not a schema migration.

    POLICY_DECISION is the Orchestrator's own phase, separate from INFERENCE (which now only
    covers the two model calls - feature detection, then defect classification). ACCEPTED (not
    COMPLETED - renamed since it no longer rests there, see below) always advances automatically
    to LEARNING_QUEUE. EXPLANATION sits between POLICY_DECISION and HUMAN_REVIEW on the escalate
    branch, matching planning/docs/SCENARIOS.md Scenario 2.
    """

    RECEIVED = "RECEIVED"
    PREPARING = "PREPARING"
    QUALITY_CHECK = "QUALITY_CHECK"
    INFERENCE = "INFERENCE"
    POLICY_DECISION = "POLICY_DECISION"
    ACCEPTED = "ACCEPTED"
    LEARNING_QUEUE = "LEARNING_QUEUE"
    EXPLANATION = "EXPLANATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    REJECTED_QUALITY = "REJECTED_QUALITY"

    @property
    def is_terminal(self) -> bool:
        """No reviewer-action endpoint exists yet, so HUMAN_REVIEW is treated as terminal for
        this pass too - see the plan's "Notable choices" section. ACCEPTED is deliberately not
        terminal - it always advances to LEARNING_QUEUE in the same pass.
        """
        return self in (
            WorkflowStatus.LEARNING_QUEUE,
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
    # Now the *defect* classifier's confidence (app.agents.multi_modal_inference.defect_classifier)
    # - what actually gates auto-accept/escalate. feature_confidence below holds what this field
    # used to mean (the feature detector's own confidence), kept distinct so the two can disagree.
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    defect_label: Mapped[str | None] = mapped_column(String, nullable=True)
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    detections: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)

    # Populated once this workflow reaches LEARNING_QUEUE (see runner.py) - a summary-text
    # embedding used by Case Context retrieval (app.agents.explainability_review.case_context)
    # to find similar past cases for a *future* escalated workflow's report.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="workflow", order_by="AuditEvent.created_at", cascade="all, delete-orphan"
    )
    explanation: Mapped[Explanation | None] = relationship(
        back_populates="workflow", uselist=False, cascade="all, delete-orphan"
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


class Explanation(Base):
    """Explainability & Review's report for an escalated workflow - trimmed from
    `planning/docs/DATA-MODEL.md`'s `Explanation` entity (no `explanationId`, the DB PK covers
    it; no `subCaseId`, this build has no `ComponentSubCase`). One-to-one with `Workflow`, only
    ever populated on the escalate branch (`POLICY_DECISION -> EXPLANATION -> HUMAN_REVIEW`).
    """

    __tablename__ = "explanations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id"), nullable=False, unique=True
    )

    claims: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    stripped_claims: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    grounded_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recommendation: Mapped[str] = mapped_column(String, nullable=False)

    # Case Context RAG results (app.agents.explainability_review.case_context) - both lists of
    # plain dicts (workflow/doc id, title or board/component, similarity score), not FKs, since
    # a retrieved precedent's own row can outlive or predate this one independently.
    retrieved_precedents: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    retrieved_guidance: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, default=list, nullable=False
    )

    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    workflow: Mapped[Workflow] = relationship(back_populates="explanation")


class RemediationDoc(Base):
    """The synthetic remediation-guidance corpus Case Context retrieves from (the other half
    of the RAG, alongside `Workflow.embedding` precedents) - seeded by
    `scripts/seed_remediation_docs.py`, hand-authored, not derived from any real documentation.
    """

    __tablename__ = "remediation_docs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    defect_label: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
