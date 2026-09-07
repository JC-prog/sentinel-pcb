"""OnnxClassifier: one loaded ONNX model plus its ModelSpec, exposing a single blocking
predict() call. onnxruntime is CPU-only here (no GPU on Fargate) and its run() is blocking, so
callers should hand it to a worker thread."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from inference_service.manifest import ModelSpec
from inference_service.preprocess import preprocess


@dataclass(frozen=True)
class Prediction:
    label: str
    index: int
    confidence: float
    scores: dict[str, float]


class OnnxClassifier:
    def __init__(self, spec: ModelSpec, session: ort.InferenceSession) -> None:
        self._spec = spec
        self._session = session
        self._input_name = spec.input_name or session.get_inputs()[0].name

    @classmethod
    def load(cls, spec: ModelSpec, model_path: str | Path) -> "OnnxClassifier":
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        return cls(spec, session)

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    def predict(self, image: Image.Image) -> Prediction:
        tensor = preprocess(image, self._spec)
        (raw,) = self._session.run(None, {self._input_name: tensor})
        vector = np.asarray(raw, dtype=np.float32).reshape(-1)

        if vector.shape[0] != len(self._spec.labels):
            raise ValueError(
                f"model {self._spec.name!r} produced {vector.shape[0]} outputs but the manifest "
                f"lists {len(self._spec.labels)} labels"
            )

        probs = _softmax(vector) if self._spec.apply_softmax else vector
        index = int(np.argmax(probs))
        return Prediction(
            label=self._spec.labels[index],
            index=index,
            confidence=float(probs[index]),
            scores={label: float(p) for label, p in zip(self._spec.labels, probs, strict=True)},
        )


def _softmax(vector: np.ndarray) -> np.ndarray:
    exp = np.exp(vector - np.max(vector))
    return np.asarray(exp / exp.sum(), dtype=np.float32)
