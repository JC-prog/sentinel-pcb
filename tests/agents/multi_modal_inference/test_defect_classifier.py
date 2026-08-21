"""Unit tests for the placeholder defect classifier - pure functions, no ONNX model needed."""

from __future__ import annotations

from app.agents.multi_modal_inference.defect_classifier import (
    _DEFAULT_LABEL,
    _FEATURE_TO_DEFECT,
    PlaceholderDefectClassifier,
)
from app.agents.multi_modal_inference.detector import Detection, InferenceResult


def _detection(label: str, confidence: float) -> Detection:
    return Detection(label=label, confidence=confidence, bbox=(0.0, 0.0, 10.0, 10.0))


def test_no_detections_returns_default_label_with_zero_confidence() -> None:
    result = InferenceResult(detections=[], overall_confidence=0.0, model_version="test")
    prediction = PlaceholderDefectClassifier().predict(result)

    assert prediction.label == _DEFAULT_LABEL
    assert prediction.confidence == 0.0


def test_label_derives_from_weakest_detection() -> None:
    result = InferenceResult(
        detections=[
            _detection("MountingHole", 0.95),
            _detection("SolderJoint", 0.40),  # weakest -> maps to "Solder Bridge"
            _detection("Lead", 0.80),
        ],
        overall_confidence=0.40,
        model_version="test",
    )
    prediction = PlaceholderDefectClassifier().predict(result)

    assert prediction.label == _FEATURE_TO_DEFECT["SolderJoint"]


def test_confidence_formula_matches_hand_computed_value() -> None:
    result = InferenceResult(
        detections=[_detection("ComponentBody", 0.9), _detection("Lead", 0.7)],
        overall_confidence=0.7,
        model_version="test",
    )
    prediction = PlaceholderDefectClassifier().predict(result)

    mean_confidence = (0.9 + 0.7) / 2
    spread = 0.9 - 0.7
    expected = mean_confidence * (1 - spread)
    assert prediction.confidence == expected


def test_tight_confidence_spread_yields_higher_derived_confidence_than_wide_spread() -> None:
    tight = InferenceResult(
        detections=[_detection("ComponentBody", 0.85), _detection("Lead", 0.80)],
        overall_confidence=0.80,
        model_version="test",
    )
    wide = InferenceResult(
        detections=[_detection("ComponentBody", 0.95), _detection("Lead", 0.30)],
        overall_confidence=0.30,
        model_version="test",
    )
    classifier = PlaceholderDefectClassifier()

    assert classifier.predict(tight).confidence > classifier.predict(wide).confidence
