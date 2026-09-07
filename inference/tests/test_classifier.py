from pathlib import Path

import pytest
from PIL import Image

from inference_service.classifier import OnnxClassifier
from inference_service.manifest import ModelSpec
from tests._tiny_onnx import build_tiny_onnx


def test_prediction_scores_are_a_distribution_over_manifest_labels(tmp_path: Path) -> None:
    model_path = tmp_path / "m.onnx"
    build_tiny_onnx(model_path, num_classes=3)
    spec = ModelSpec(name="m", repo_id="r", labels=["a", "b", "c"], input_size=(16, 16))
    classifier = OnnxClassifier.load(spec, model_path)

    prediction = classifier.predict(Image.new("RGB", (30, 30), (10, 200, 50)))

    assert prediction.label in spec.labels
    assert prediction.index == spec.labels.index(prediction.label)
    assert set(prediction.scores) == set(spec.labels)
    assert abs(sum(prediction.scores.values()) - 1.0) < 1e-5


def test_label_count_mismatch_is_reported(tmp_path: Path) -> None:
    model_path = tmp_path / "m.onnx"
    build_tiny_onnx(model_path, num_classes=3)
    spec = ModelSpec(name="m", repo_id="r", labels=["only", "two"], input_size=(16, 16))
    classifier = OnnxClassifier.load(spec, model_path)

    with pytest.raises(ValueError, match="3 outputs but the manifest lists 2"):
        classifier.predict(Image.new("RGB", (16, 16), (0, 0, 0)))


def test_apply_softmax_false_keeps_raw_outputs(tmp_path: Path) -> None:
    model_path = tmp_path / "m.onnx"
    build_tiny_onnx(model_path, num_classes=3)
    raw_spec = ModelSpec(
        name="m", repo_id="r", labels=["a", "b", "c"], input_size=(16, 16), apply_softmax=False
    )
    soft_spec = raw_spec.model_copy(update={"apply_softmax": True})
    image = Image.new("RGB", (16, 16), (123, 222, 64))

    raw = OnnxClassifier.load(raw_spec, model_path).predict(image)
    softened = OnnxClassifier.load(soft_spec, model_path).predict(image)

    # softmax is monotonic, so the winning label is unchanged, but the score vectors differ and
    # only the softmaxed one is a normalized distribution.
    assert raw.label == softened.label
    assert raw.scores != softened.scores
    assert abs(sum(softened.scores.values()) - 1.0) < 1e-5
