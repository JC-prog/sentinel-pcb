"""Multi-Modal Inference (deterministic service): wraps the trusted ADC model.

See docs/ADC-Design-Doc-Team10-V2.md §7 (interface contract) and §8 (ONNX rationale).
"""

from app.agents.multi_modal_inference.detector import (
    Detection,
    InferenceResult,
    ModelNotAvailableError,
    PCBFeatureDetector,
    get_detector,
)

__all__ = [
    "Detection",
    "InferenceResult",
    "ModelNotAvailableError",
    "PCBFeatureDetector",
    "get_detector",
]
