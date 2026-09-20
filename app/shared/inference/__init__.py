"""Client for the internal ONNX classification service (inference/, infra/production/inference.tf).

Used by both feature modules: app/chat/agents/adc_inspection_agent/graph.py (one image at a
time) and app/workflow/agents/orchestrator_agent/services/ (whole datasets) for the two-stage PCB
defect classifier.
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
