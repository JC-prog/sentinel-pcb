from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Classification(BaseModel):
    """One model's verdict on one image. Mirrors the inference service's ClassifyResponse
    (inference/src/inference_service/schemas.py)."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    # "<repo_id>@<revision>" of the weights that answered. Empty only against an inference
    # service that predates versioning.
    model_version: str = ""
    username: str
    label: str
    index: int
    confidence: float
    scores: dict[str, float]
    request_id: str


class ModelInfo(BaseModel):
    """One served model, as GET /models (and /models/{name}/activate|rollback) report it."""

    model_config = ConfigDict(protected_namespaces=())

    name: str
    version: str
    previous_version: str | None = None
    loaded_at: datetime
    labels: list[str]
    input_size: tuple[int, int]


class JobSample(BaseModel):
    case_id: str
    observed_label: str | None = None
    expected_label: str | None = None


class JobArtifact(BaseModel):
    repo_id: str
    revision: str


class JobResult(BaseModel):
    simulated: bool
    artifact: JobArtifact
    metrics: dict[str, float] = Field(default_factory=dict)


class RemoteJob(BaseModel):
    """A retraining job as the inference service sees it (its JobResponse). `status` is one of
    queued/running/succeeded/failed/cancelled."""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    client_ref: str
    model: str
    base_version: str
    status: str
    progress: float
    sample_count: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: JobResult | None = None
