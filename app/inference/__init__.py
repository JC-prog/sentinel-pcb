"""Client for the internal ONNX classification service (inference/, infra/production/inference.tf).

Nothing calls this yet - it's the interface the Explainability & Review Agent will use to get a
fast defect pre-classification before its LLM steps. Configured via settings.inference_base_url.
"""

from app.inference.client import (
    InferenceError,
    InferenceNotConfigured,
    classify,
)
from app.inference.schemas import Classification

__all__ = [
    "Classification",
    "InferenceError",
    "InferenceNotConfigured",
    "classify",
]
