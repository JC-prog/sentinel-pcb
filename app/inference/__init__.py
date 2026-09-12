"""Client for the internal ONNX classification service (inference/, infra/production/inference.tf).

Used by app/agents/adc_inspection_agent/graph.py for the two-stage PCB defect classifier.
Configured via settings.inference_base_url.
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
