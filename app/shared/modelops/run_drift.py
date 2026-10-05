"""Drift and operator corrections for one Work-tab run, computed from what the run stored.

A run's samples carry the model's calls (Agent 1) and, once reviewed, Agent 2's verdict and the
operator's final decision. Where the operator's final result differs from Agent 1's label, the
operator *corrected* the model - the signal drift watches and the raw material for retraining
tickets. The Work tab (workflow) reads those payloads from Qdrant through its own writer and chat
through its own read-only reader; both hand them here, so the maths lives once, in shared, with no
I/O. Inputs are the stored payload dicts, exactly as app/workflow/services/run_store.py wrote them:

  points   `adc_inspection_results` payloads: {sample_id, sample, inference, final_decision}
  reviews  `adc_agent2_reviews` payloads:     {sample_id, result: {output}, human_decision, ...}

Cases (chat's table) are a different population and are measured by app/chat/services/drift.py.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.shared.config.settings import settings

REVIEW_REQUIRED = "REVIEW_REQUIRED"

_CANONICAL_DEFECTS = (
    "missing part",
    "shifted",
    "foreign material",
    "tombstone",
    "solder insufficient",
    "wrong part",
    "no defect",
)


def normalize_defect(label: str | None) -> str:
    """'MissingPart' / 'WrongPart_13' / 'Shift' / 'Golden' -> the canonical lower-case IPC class,
    the same comparison the source project's ui.py makes before deciding whether two calls agree."""

    if not label:
        return "no defect"
    cleaned = label.strip().split("_")[0]
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", cleaned).lower().replace("-", " ")
    spaced = " ".join(spaced.split())
    # The AOI datasets spell it "SolderInsuffcient"; Agent 1's own pipeline corrects it the same way.
    spaced = spaced.replace("insuffcient", "insufficient")
    if spaced == "golden":
        return "no defect"
    squashed = spaced.replace(" ", "")
    for canonical in _CANONICAL_DEFECTS:
        if canonical.replace(" ", "") == squashed:
            return canonical
    # The body model's "Shift" is a prefix of the canonical "shifted".
    for canonical in _CANONICAL_DEFECTS:
        if len(squashed) >= 4 and canonical.replace(" ", "").startswith(squashed):
            return canonical
    return spaced


def _stage(inference: dict[str, Any], key: str) -> dict[str, Any] | None:
    """A classifier's answer - top level for a completed sample, nested under "details" for one
    that stopped at the first stage."""

    stage = inference.get(key)
    if not isinstance(stage, dict):
        details = inference.get("details")
        stage = details.get(key) if isinstance(details, dict) else None
    return stage if isinstance(stage, dict) else None


def sample_model_name(point: dict[str, Any]) -> str | None:
    """The real inference-service model (e.g. "pcb_body_defect") - only known once the sample
    reached stage-2 routing; None for one that stopped at stage 1."""

    routing = (point.get("inference") or {}).get("routing")
    name = routing.get("service_model") if isinstance(routing, dict) else None
    return name if isinstance(name, str) and name else None


def sample_model_version(point: dict[str, Any]) -> str | None:
    inference = point.get("inference") or {}
    for key in ("defect_classification", "feature_classification"):
        stage = _stage(inference, key)
        version = stage.get("model_version") if stage else None
        if isinstance(version, str) and version:
            return version
    return None


def sample_confidence(point: dict[str, Any]) -> float | None:
    stage = _stage(point.get("inference") or {}, "defect_classification")
    value = stage.get("confidence") if stage else None
    return float(value) if isinstance(value, int | float) else None


def agent1_label(point: dict[str, Any]) -> str | None:
    """What Agent 1 called it: the defect model's prediction, else the dataset's own machine
    defect (the baseline Agent 2 audits for a sample that stopped at feature classification)."""

    stage = _stage(point.get("inference") or {}, "defect_classification")
    prediction = stage.get("prediction") if stage else None
    if prediction:
        return str(prediction)
    machine = (point.get("sample") or {}).get("machine_defect")
    return str(machine) if machine else None


def _human_decision(review: dict[str, Any] | None) -> dict[str, Any] | None:
    decision = review.get("human_decision") if review else None
    return decision if isinstance(decision, dict) and decision.get("final_result") else None


def _agent2_label(review: dict[str, Any] | None) -> str | None:
    result = review.get("result") if review else None
    output = result.get("output") if isinstance(result, dict) else None
    label = output.get("predicted_defect") if isinstance(output, dict) else None
    return str(label) if label else None


def correction_of(point: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any] | None:
    """The operator's correction of this sample, or None when there is no decision or the operator
    agreed with Agent 1. `queueable` is False when no model was recorded to file a ticket against."""

    decision = _human_decision(review)
    if decision is None:
        return None
    label = agent1_label(point)
    if normalize_defect(decision["final_result"]) == normalize_defect(label):
        return None
    model_name = sample_model_name(point)
    return {
        "sample_id": str(point.get("sample_id")),
        "model_name": model_name,
        "model_version": sample_model_version(point),
        "agent1_label": label,
        "final_result": str(decision["final_result"]),
        "selected_source": decision.get("selected_source"),
        "operator_notes": decision.get("operator_notes"),
        "queueable": model_name is not None,
    }


@dataclass
class _ModelStats:
    model_name: str | None
    model_version: str | None = None
    samples: int = 0
    review_required: int = 0
    decided: int = 0
    corrected: int = 0
    agent2_disagreed: int = 0
    with_confidence: int = 0
    low_confidence: int = 0
    confidence_sum: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        def rate(part: int, whole: int) -> float | None:
            return round(part / whole, 3) if whole else None

        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "samples": self.samples,
            "review_required": self.review_required,
            "decided": self.decided,
            "corrected": self.corrected,
            "correction_rate": rate(self.corrected, self.decided),
            "agent2_disagreed": self.agent2_disagreed,
            "low_confidence_rate": rate(self.low_confidence, self.with_confidence),
            "mean_confidence": (
                round(self.confidence_sum / self.with_confidence, 3) if self.with_confidence else None
            ),
        }


def summarize_run(points: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-model drift numbers and the operator's corrections for one run."""

    by_sample = {str(r.get("sample_id")): r for r in reviews}
    groups: dict[str | None, _ModelStats] = defaultdict(lambda: _ModelStats(None))
    corrections: list[dict[str, Any]] = []
    threshold = settings.adc_defect_confidence_threshold

    for point in sorted(points, key=lambda p: str(p.get("sample_id"))):
        review = by_sample.get(str(point.get("sample_id")))
        name = sample_model_name(point)
        stats = groups[name]
        stats.model_name = name
        stats.model_version = stats.model_version or sample_model_version(point)
        stats.samples += 1
        if point.get("final_decision") == REVIEW_REQUIRED:
            stats.review_required += 1
        confidence = sample_confidence(point)
        if confidence is not None:
            stats.with_confidence += 1
            stats.confidence_sum += confidence
            if confidence < threshold:
                stats.low_confidence += 1
        agent2 = _agent2_label(review)
        if agent2 is not None and normalize_defect(agent2) != normalize_defect(agent1_label(point)):
            stats.agent2_disagreed += 1
        if _human_decision(review) is not None:
            stats.decided += 1
        correction = correction_of(point, review)
        if correction is not None:
            stats.corrected += 1
            corrections.append(correction)

    models = sorted(groups.values(), key=lambda s: (s.model_name is None, s.model_name or ""))
    return {
        "totals": {
            "samples": sum(m.samples for m in models),
            "review_required": sum(m.review_required for m in models),
            "decided": sum(m.decided for m in models),
            "corrected": sum(m.corrected for m in models),
        },
        "models": [m.to_dict() for m in models],
        "corrections": corrections,
    }
