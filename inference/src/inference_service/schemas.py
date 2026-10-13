from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    # "<repo_id>@<revision>" of the weights currently served for this name.
    version: str
    # What this name served before its last activation (the rollback target), if anything.
    previous_version: str | None = None
    loaded_at: datetime
    labels: list[str]
    input_size: tuple[int, int]


class HealthResponse(BaseModel):
    status: str
    models: list[str]


class ClassifyResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str
    # Which weights produced this verdict - lets callers attribute a result (and later, drift) to
    # a specific version rather than just a model name.
    model_version: str
    username: str
    label: str
    index: int
    confidence: float
    scores: dict[str, float]
    request_id: str


class ActivateRequest(BaseModel):
    """A new version of an already-served model. Labels and preprocessing are inherited from the
    version being replaced, so it must be a drop-in with the same output classes."""

    repo_id: str = Field(min_length=1)
    revision: str = Field(min_length=1)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSample(BaseModel):
    """One flagged case in a retraining set. The image itself stays in the backend's upload
    store; a trainer that needs pixels will need a transport for them (out of scope for the
    stub)."""

    case_id: str
    observed_label: str | None = None  # what the model said
    expected_label: str | None = None  # what the reviewer says it should have been


class JobRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model: str
    # The caller's own id for this job (the backend's RetrainingJob id). Resubmitting the same
    # client_ref returns the existing job instead of queueing a duplicate, so a retry after a
    # timeout is safe.
    client_ref: str = Field(min_length=1)
    # Defaults to whatever version is live for `model` when the job is accepted.
    base_version: str | None = None
    samples: list[JobSample] = Field(min_length=1)
    notes: str | None = None


class JobArtifact(BaseModel):
    repo_id: str
    revision: str


class JobResult(BaseModel):
    # True for the stub trainer: nothing was trained, `artifact` is just the base version.
    simulated: bool
    artifact: JobArtifact
    metrics: dict[str, float] = Field(default_factory=dict)


class JobResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: str
    client_ref: str
    model: str
    base_version: str
    status: JobState
    progress: float
    sample_count: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: JobResult | None = None
