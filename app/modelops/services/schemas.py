"""Response/request shapes for the Models tab's API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class InferenceStatus(BaseModel):
    """Whether the numbers on the page reflect the inference service right now. When it can't be
    reached the tab still renders what the database knows, and says so."""

    configured: bool
    reachable: bool
    error: str | None = None


class ModelVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    model_name: str
    version: str
    repo_id: str
    revision: str
    status: str
    source_job_id: str | None
    first_seen_at: datetime
    activated_at: datetime | None
    activated_by: str | None = None


class ModelOut(BaseModel):
    name: str
    live: ModelVersionOut | None
    previous: ModelVersionOut | None
    versions: list[ModelVersionOut]
    labels: list[str] | None = None  # from the inference service, when reachable
    loaded_at: datetime | None = None
    open_drift_reports: int
    open_tickets: int
    active_jobs: int  # pending approval, approved, queued or running


class ModelsOverview(BaseModel):
    inference: InferenceStatus
    models: list[ModelOut]


class DriftReportOut(BaseModel):
    id: str
    model_name: str
    model_version: str | None
    description: str
    case_numbers: list[str]
    stats: dict[str, Any]
    status: str
    reported_by: str
    created_at: datetime
    resolved_at: datetime | None


class DriftOverview(BaseModel):
    window_days: int
    open_by_model: dict[str, int]
    reported_in_window_by_model: dict[str, int]
    reports: list[DriftReportOut]


class SampleOut(BaseModel):
    case_id: str
    case_number: str | None = None
    ticket_id: str | None = None
    observed_label: str | None = None
    expected_label: str | None = None


class JobOut(BaseModel):
    id: str
    model_name: str
    base_version: str
    status: str
    rationale: str
    sample_count: int
    drift_report_ids: list[str]
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    external_job_id: str | None
    progress: float
    error: str | None
    artifact_repo_id: str | None
    artifact_revision: str | None
    simulated: bool | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobDetailOut(JobOut):
    samples: list[SampleOut]


class TicketCounts(BaseModel):
    open: int
    acknowledged: int
    resolved: int


class QueueOverview(BaseModel):
    inference: InferenceStatus
    tickets: TicketCounts
    jobs: list[JobOut]


class PromoteRequest(BaseModel):
    version: str
