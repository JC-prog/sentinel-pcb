"""Client for the internal ONNX classification service (inference/, infra/production/inference.tf).

Used by app/chat/agents/adc_inspection_agent/graph.py for the two-stage PCB defect classifier.
Configured via settings.inference_base_url.
"""

from app.shared.inference.client import (
    InferenceError,
    InferenceNotConfigured,
    classify,
)
from app.shared.inference.schemas import Classification

__all__ = [
    "Classification",
    "InferenceError",
    "InferenceNotConfigured",
    "classify",
]
