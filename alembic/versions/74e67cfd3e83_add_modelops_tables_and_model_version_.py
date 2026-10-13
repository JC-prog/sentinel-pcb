"""add modelops tables and model version stamps

Adds model_versions, drift_reports and retraining_jobs; extends retraining_tickets (which now
lives in app/shared/db/models/modelops.py - same table) with the model/version/labels it was
flagged against and the job it was drafted into; and stamps cases with the model version that made
each classification.

Revision ID: 74e67cfd3e83
Revises: ea8ea3053c3c
Create Date: 2026-09-20 23:12:51.668861

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "74e67cfd3e83"
down_revision: str | Sequence[str] | None = "ea8ea3053c3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "drift_reports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("reported_by_user_id", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("case_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["reported_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_drift_reports_model_name"), "drift_reports", ["model_name"], unique=False
    )
    op.create_index(op.f("ix_drift_reports_status"), "drift_reports", ["status"], unique=False)

    op.create_table(
        "retraining_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("base_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("samples", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("drift_report_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("approved_by_user_id", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_job_id", sa.String(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("artifact_repo_id", sa.String(), nullable=True),
        sa.Column("artifact_revision", sa.String(), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_retraining_jobs_external_job_id"),
        "retraining_jobs",
        ["external_job_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_retraining_jobs_model_name"), "retraining_jobs", ["model_name"], unique=False
    )
    op.create_index(op.f("ix_retraining_jobs_status"), "retraining_jobs", ["status"], unique=False)

    op.create_table(
        "model_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("repo_id", sa.String(), nullable=False),
        sa.Column("revision", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_job_id", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["activated_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["retraining_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_name", "version", name="uq_model_versions_name_version"),
    )
    op.create_index(
        op.f("ix_model_versions_model_name"), "model_versions", ["model_name"], unique=False
    )
    op.create_index(op.f("ix_model_versions_status"), "model_versions", ["status"], unique=False)
    op.create_index(
        "uq_model_versions_one_live",
        "model_versions",
        ["model_name"],
        unique=True,
        postgresql_where=sa.text("status = 'live'"),
    )

    op.add_column("cases", sa.Column("region_model_version", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("defect_model_version", sa.String(), nullable=True))

    op.add_column("retraining_tickets", sa.Column("model_name", sa.String(), nullable=True))
    op.add_column("retraining_tickets", sa.Column("model_version", sa.String(), nullable=True))
    op.add_column("retraining_tickets", sa.Column("observed_label", sa.String(), nullable=True))
    op.add_column("retraining_tickets", sa.Column("correct_label", sa.String(), nullable=True))
    op.add_column("retraining_tickets", sa.Column("job_id", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_retraining_tickets_job_id"), "retraining_tickets", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_retraining_tickets_model_name"),
        "retraining_tickets",
        ["model_name"],
        unique=False,
    )
    op.create_foreign_key(
        "retraining_tickets_job_id_fkey", "retraining_tickets", "retraining_jobs", ["job_id"], ["id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("retraining_tickets_job_id_fkey", "retraining_tickets", type_="foreignkey")
    op.drop_index(op.f("ix_retraining_tickets_model_name"), table_name="retraining_tickets")
    op.drop_index(op.f("ix_retraining_tickets_job_id"), table_name="retraining_tickets")
    op.drop_column("retraining_tickets", "job_id")
    op.drop_column("retraining_tickets", "correct_label")
    op.drop_column("retraining_tickets", "observed_label")
    op.drop_column("retraining_tickets", "model_version")
    op.drop_column("retraining_tickets", "model_name")

    op.drop_column("cases", "defect_model_version")
    op.drop_column("cases", "region_model_version")

    op.drop_index(
        "uq_model_versions_one_live",
        table_name="model_versions",
        postgresql_where=sa.text("status = 'live'"),
    )
    op.drop_index(op.f("ix_model_versions_status"), table_name="model_versions")
    op.drop_index(op.f("ix_model_versions_model_name"), table_name="model_versions")
    op.drop_table("model_versions")

    op.drop_index(op.f("ix_retraining_jobs_status"), table_name="retraining_jobs")
    op.drop_index(op.f("ix_retraining_jobs_model_name"), table_name="retraining_jobs")
    op.drop_index(op.f("ix_retraining_jobs_external_job_id"), table_name="retraining_jobs")
    op.drop_table("retraining_jobs")

    op.drop_index(op.f("ix_drift_reports_status"), table_name="drift_reports")
    op.drop_index(op.f("ix_drift_reports_model_name"), table_name="drift_reports")
    op.drop_table("drift_reports")
