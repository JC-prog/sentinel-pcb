"""Model-operations tables: which model versions exist and which is live, drift reports, the
retraining queue, and the tickets that feed it. Shared (not chat-owned) because two independent
sides use them - the chat monitoring agent creates tickets, drift reports and draft jobs, and the
Models tab (app/modelops/) reviews and acts on them - and chat and modelops may not import each
other.

`RetrainingTicket.case_id` references the chat-owned `cases` table by foreign key *string* only
(the same way chat's models reference `users`); anything that runs create_all must have imported
chat's models too, as app/main.py, alembic/env.py and tests/conftest.py already do.

The app's Postgres is the source of truth for all of this. The inference service holds no durable
state - it reports which versions are loaded and executes jobs, and the backend reconciles.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class ModelVersionStatus(StrEnum):
    LIVE = "live"  # what the inference service is serving right now (at most one per model)
    PREVIOUS = "previous"  # what it served before - the rollback target
    CANDIDATE = "candidate"  # produced by a succeeded retraining job, not yet activated
    RETIRED = "retired"


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_name", "version", name="uq_model_versions_name_version"),
        # The invariant the Models tab relies on ("which model is live right now"): a database-level
        # guarantee of at most one live version per model, not just a convention in the code.
        Index(
            "uq_model_versions_one_live",
            "model_name",
            unique=True,
            postgresql_where=text("status = 'live'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    model_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # "<repo_id>@<revision>", exactly as the inference service reports it.
    version: Mapped[str] = mapped_column(String, nullable=False)
    repo_id: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Set for candidates a retraining job produced; None for versions that were simply found
    # already loaded (e.g. the ones baked into the image).
    source_job_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("retraining_jobs.id"), nullable=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_by_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )


class DriftReportStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class DriftReport(Base):
    __tablename__ = "drift_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    model_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The version the reporter believes drifted; None if they didn't say / it wasn't known.
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    reported_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Case numbers/ids the reporter pointed at as evidence, and a snapshot of the numbers
    # (override rate, low-confidence rate, ...) computed when the report was filed - a snapshot,
    # not a live query, so the report still says what was true when it was made.
    case_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # The same cases as "CASE-000123" - copied at filing time because the numbers live in chat's
    # table, which this module (and the Models tab) can't read.
    case_numbers: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default=DriftReportStatus.OPEN, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetrainingJobStatus(StrEnum):
    """PENDING_APPROVAL (drafted, e.g. by the chat agent) -> APPROVED (an Admin said yes; not yet
    accepted by the inference service) -> QUEUED -> RUNNING -> SUCCEEDED | FAILED, with CANCELLED
    reachable from every non-terminal state. See app/shared/modelops/jobs.py for the transitions."""

    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RetrainingJob(Base):
    __tablename__ = "retraining_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    model_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The version the retrain starts from ("<repo_id>@<revision>").
    base_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default=RetrainingJobStatus.PENDING_APPROVAL, nullable=False, index=True
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    # [{case_id, case_number, ticket_id, observed_label, expected_label}] - what the trainer is asked to learn
    # from. Frozen at draft time so approving a job approves exactly what was reviewed.
    samples: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    drift_report_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    created_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The inference service's id for this job once it has accepted it (our own id is sent as its
    # `client_ref`, which makes a retried submit idempotent).
    external_job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Filled on success: where the new weights are, and whether they're real. `simulated` is true
    # for the stub trainer, so a UI can never present a no-op run as an improved model.
    artifact_repo_id: Mapped[str | None] = mapped_column(String, nullable=True)
    artifact_revision: Mapped[str | None] = mapped_column(String, nullable=True)
    simulated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetrainingTicketStatus(StrEnum):
    """OPEN: flagged, not yet part of any job. ACKNOWLEDGED: included in a drafted/active job.
    RESOLVED: that job succeeded. A cancelled or failed job releases its tickets back to OPEN."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class RetrainingTicket(Base):
    """A QA/Admin reviewer's claim that a Case's model verdict was wrong - created by the chat
    monitoring agent's flag_case_for_retraining tool. Creating one only records the claim; a
    RetrainingJob (drafted from open tickets, approved by an Admin) is what actually asks for a
    retrain."""

    __tablename__ = "retraining_tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_id)
    case_id: Mapped[str] = mapped_column(String, ForeignKey("cases.id"), nullable=False, index=True)
    # "CASE-000123", copied at flag time (see DriftReport.case_numbers for why).
    case_number: Mapped[str | None] = mapped_column(String, nullable=True)
    flagged_by_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False
    )
    # Required, not optional - flag_case_for_retraining rejects an empty reason before this row
    # is ever created, so a bare "I don't like this verdict" flag is never silently accepted.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Which model/version made the call and what it said, copied from the Case at flag time (the
    # case row alone can't say this once its model has been retrained), plus what the reviewer says
    # it should have been. Nullable: tickets that predate versioning, or that don't name a label.
    model_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_label: Mapped[str | None] = mapped_column(String, nullable=True)
    correct_label: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, default=RetrainingTicketStatus.OPEN, nullable=False, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("retraining_jobs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
