"""Client for the internal ONNX classification service (inference/, infra/production/inference.tf).

Used by both feature modules: app/chat/agents/inspection_agent/graph.py (one image at a
time) and app/workflow/agents/orchestrator_agent/services/ (whole datasets) for the two-stage PCB
defect classifier, and by the model-operations code (app/shared/modelops/, app/modelops/) to list
versions, hot-swap them, and queue retraining jobs.
Configured via settings.inference_base_url.
"""

from app.shared.inference.client import (
    InferenceError,
    InferenceNotConfigured,
    InferenceNotFound,
    activate_model,
    cancel_job,
    classify,
    get_job,
    list_models,
    rollback_model,
    submit_job,
)
from app.shared.inference.schemas import (
    Classification,
    JobArtifact,
    JobResult,
    JobSample,
    ModelInfo,
    RemoteJob,
)

__all__ = [
    "Classification",
    "InferenceError",
    "InferenceNotConfigured",
    "InferenceNotFound",
    "JobArtifact",
    "JobResult",
    "JobSample",
    "ModelInfo",
    "RemoteJob",
    "activate_model",
    "cancel_job",
    "classify",
    "get_job",
    "list_models",
    "rollback_model",
    "submit_job",
]
