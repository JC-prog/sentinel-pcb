"""ONNX wrapper around the trusted ADC model (Multi-Modal Inference service).

The model (https://huggingface.co/JcProg/PCBInspect-AI, YOLOv12-Medium, exported to ONNX with
NMS baked in - see docs/ADC-Design-Doc-Team10-V2.md §7 and §8's ONNX rationale) detects
structural PCB features - MountingHole, ComponentBody, SolderJoint, Lead - not defects directly.
It is not a defect classifier; it is the "trusted model" the rest of the pipeline is built
around. Downstream (Explainability & Review's Measurement Evidence Tool, once built) is where
detected feature geometry gets compared against spec to decide genuine-defect vs. false-flag.

For this build pass, `overall_confidence` (the minimum confidence across all detections, or 0.0
if nothing was detected) is what `app.agents.orchestrator` thresholds on to decide
auto-accept vs. escalate. That mapping is a placeholder pending a real spec - see the module
docstring in `app.agents.orchestrator`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image
from pydantic import BaseModel

from app.settings import settings


class Detection(BaseModel):
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]
    """(x1, y1, x2, y2) in original image pixel coordinates."""


class InferenceResult(BaseModel):
    detections: list[Detection]
    overall_confidence: float
    """min(detection confidences), or 0.0 if no detection cleared the confidence floor."""
    model_version: str


class ModelNotAvailableError(RuntimeError):
    """Raised when the ONNX model/labels files aren't present at the configured path."""


def _labels_path(model_path: Path) -> Path:
    return model_path.with_suffix("").with_suffix(".labels.json")


class PCBFeatureDetector:
    """Loads the ONNX model once and serves `predict()` calls against it."""

    def __init__(self, model_path: Path, input_size: int) -> None:
        labels_path = _labels_path(model_path)
        if not model_path.exists() or not labels_path.exists():
            raise ModelNotAvailableError(
                f"ADC model not found at {model_path} (+ {labels_path.name}). "
                "See docs/ADC-Design-Doc-Team10-V2.md §8 for the export steps from "
                "https://huggingface.co/JcProg/PCBInspect-AI."
            )

        self.input_size = input_size
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

        raw_labels: dict[str, str] = json.loads(labels_path.read_text())
        self.labels: dict[int, str] = {int(k): v for k, v in raw_labels.items()}
        self.model_version = model_path.name

    def _letterbox(self, image: Image.Image) -> tuple[np.ndarray, float, float, float]:
        """Resize+pad to a square input, preserving aspect ratio (standard YOLO preprocessing).

        Returns (input_tensor, scale_ratio, pad_x, pad_y) - the ratio/padding are needed to map
        detection boxes in the 640x640 letterboxed space back to original image coordinates.
        """

        w0, h0 = image.size
        r = min(self.input_size / w0, self.input_size / h0)
        new_w, new_h = round(w0 * r), round(h0 * r)
        resized = image.convert("RGB").resize((new_w, new_h), Image.Resampling.BILINEAR)

        canvas = Image.new("RGB", (self.input_size, self.input_size), (114, 114, 114))
        pad_x, pad_y = (self.input_size - new_w) / 2, (self.input_size - new_h) / 2
        canvas.paste(resized, (round(pad_x), round(pad_y)))

        array = np.asarray(canvas, dtype=np.float32) / 255.0
        tensor = array.transpose(2, 0, 1)[np.newaxis, ...]  # HWC -> NCHW
        return tensor, r, pad_x, pad_y

    def predict(self, image: Image.Image, conf_threshold: float | None = None) -> InferenceResult:
        threshold = (
            conf_threshold if conf_threshold is not None else settings.adc_detection_confidence
        )

        tensor, ratio, pad_x, pad_y = self._letterbox(image)
        (raw,) = self.session.run(None, {self.input_name: tensor})  # shape (1, 300, 6)

        detections: list[Detection] = []
        for x1, y1, x2, y2, conf, cls_id in raw[0]:
            if conf < threshold:
                continue
            detections.append(
                Detection(
                    label=self.labels.get(int(cls_id), f"class_{int(cls_id)}"),
                    confidence=float(conf),
                    bbox=(
                        (float(x1) - pad_x) / ratio,
                        (float(y1) - pad_y) / ratio,
                        (float(x2) - pad_x) / ratio,
                        (float(y2) - pad_y) / ratio,
                    ),
                )
            )

        overall_confidence = min((d.confidence for d in detections), default=0.0)
        return InferenceResult(
            detections=detections,
            overall_confidence=overall_confidence,
            model_version=self.model_version,
        )


@lru_cache(maxsize=1)
def get_detector() -> PCBFeatureDetector:
    """Process-wide singleton - loading the ONNX session is expensive, do it once."""

    return PCBFeatureDetector(Path(settings.adc_model_path), settings.adc_input_size)
