"""Placeholder defect classifier - a deterministic stand-in for a real, trained defect model.

No accuracy claim whatsoever. Its only job is to produce a `DefectPrediction` that (a) is
image-derived and reproducible, not random, and (b) can genuinely diverge from the feature
detector's own confidence, so "feature vs. defect disagreement" (app.agents.orchestrator.runner)
is a real signal to exercise rather than always trivially zero.

See docs/MODEL-TRAINING.md for how to train and swap in a real model - doing so only requires
changing this file; nothing downstream depends on how the confidence number is produced, only on
the `DefectPrediction{label, confidence, basis}` contract.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel

from app.agents.multi_modal_inference.detector import InferenceResult

# Maps the *weakest* (lowest-confidence) structural feature detection in an image to the defect
# class most physically associated with that structural site. Reuses planning/docs/SCENARIOS.md's
# four illustrative labels verbatim rather than inventing new ones.
_FEATURE_TO_DEFECT: dict[str, str] = {
    "SolderJoint": "Solder Bridge",
    "Lead": "Misalignment",
    "ComponentBody": "Tombstone",
    "MountingHole": "Foreign Material",
}
_DEFAULT_LABEL = "Foreign Material"  # fallback when there are no detections at all


class DefectPrediction(BaseModel):
    label: str
    confidence: float
    basis: str
    """Human-readable justification for this prediction - feeds Explainability & Review's
    templated narrative directly."""


class PlaceholderDefectClassifier:
    """Deterministic, image-derived, no real model behind it - see module docstring."""

    def predict(self, inference: InferenceResult) -> DefectPrediction:
        if not inference.detections:
            return DefectPrediction(
                label=_DEFAULT_LABEL,
                confidence=0.0,
                basis="no structural features detected",
            )

        confidences = [d.confidence for d in inference.detections]
        weakest = min(inference.detections, key=lambda d: d.confidence)
        label = _FEATURE_TO_DEFECT.get(weakest.label, _DEFAULT_LABEL)

        mean_confidence = sum(confidences) / len(confidences)
        spread = max(confidences) - min(confidences)
        confidence = mean_confidence * (1 - spread)

        return DefectPrediction(
            label=label,
            confidence=confidence,
            basis=(
                f"weakest feature detection was '{weakest.label}' (confidence "
                f"{weakest.confidence:.2f}); derived confidence = mean({mean_confidence:.2f}) * "
                f"(1 - spread({spread:.2f}))"
            ),
        )


@lru_cache(maxsize=1)
def get_defect_classifier() -> PlaceholderDefectClassifier:
    """Process-wide singleton, mirroring `detector.get_detector()`'s pattern - even though this
    placeholder holds no weights to load, keeping the call shape stable is what makes swapping in
    a real model later a one-file change.
    """

    return PlaceholderDefectClassifier()
