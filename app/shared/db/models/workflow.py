"""Work-tab review decisions: the operator's final call on a bulk-run sample where Agent 1 (the
two-stage ONNX classifier) and Agent 2 (the explainability review) disagree, or Agent 1 alone wasn't
confident. Shared (not workflow-owned) for the same reason as modelops.py: every feature module's
tables live under app/shared/db/models/ so alembic/env.py and tests/conftest.py register them
through one import.

`run_id` is minted per streamed run (app/workflow/services/streaming.py) and `sample_id` is the
dataset CSV's SampleID - neither references another table, since orchestrator runs themselves are
never persisted. One row per (run_id, sample_id); a later decision replaces the earlier one.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class WorkflowReviewDecision(Base):
    __tablename__ = "workflow_review_decisions"
    __table_args__ = (
        UniqueConstraint("run_id", "sample_id", name="uq_workflow_review_decisions_run_sample"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sample_id: Mapped[str] = mapped_column(String, nullable=False)
    # "MACHINE" (Agent 1), "AI" (Agent 2) or "MANUAL" (operator override).
    selected_source: Mapped[str] = mapped_column(String, nullable=False)
    final_result: Mapped[str] = mapped_column(String, nullable=False)
    machine_result: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_result: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
