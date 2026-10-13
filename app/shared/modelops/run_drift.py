"""How often the model was wrong in one Work-tab run, judged by the operator's final decisions.

In the Review Console's Explanation Review the operator makes the **final decision** for each flagged
sample - *Accept Machine*, *Accept AI* or *Manual*. Whichever they pick, the saved final result *is*
the sample's correct label. This module compares it with what the **model** (the defect classifier)
predicted, sample by sample:

  confirmed      the model predicted the operator's label - the model was right
  relabelled     the model predicted something else - the model was wrong
  no_prediction  the model couldn't decide: the region model was below its confidence threshold, so
                 no defect model ran

*Drift* for the run is the share of the samples the model classified (confirmed + relabelled) that it
got wrong. A "couldn't decide" sample is not drift - the model never answered - and is counted on its
own; with nothing classified, drift is undefined (None) rather than 0% or 100%.

Every reviewed sample the model didn't get right can be added to training data with the operator's
label, for the defect model it belongs to: the one Agent 1 routed it to when it ran, otherwise the one
the region model's best guess routes to (REGION_TO_DEFECT_MODEL - the same routing Agent 1 uses).

The decisions are read exactly as stored - this never changes what the Review Console saves, nor
anything about Agent 1 or Agent 2. The Work tab (workflow) reads the payloads from Qdrant through its
own writer and chat through its own read-only reader; both hand them here, so the maths lives once,
in shared, with no I/O. Inputs are the stored payload dicts, exactly as
app/workflow/services/run_store.py wrote them:

  points   `adc_inspection_results` payloads: {sample_id, sample, inference, final_decision}
  reviews  `adc_agent2_reviews` payloads:     {sample_id, result: {output}, human_decision, ...}

Cases (chat's table) are a different population and are measured by app/chat/services/drift.py.
"""

import re
from collections import Counter, defaultdict
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


def model_prediction(point: dict[str, Any]) -> str | None:
    """The defect model's prediction, or None when the sample never reached a defect model."""

    stage = _stage(point.get("inference") or {}, "defect_classification")
    prediction = stage.get("prediction") if stage else None
    return str(prediction) if prediction else None


# Agent 1's own routing (src/agent1_orchestrator/inference/router.py ROUTES +
# services/model_lifecycle.py service names), re-stated because shared may not import workflow;
# tests/shared/test_run_drift.py checks the two agree.
REGION_TO_DEFECT_MODEL = {
    "Body": "pcb_body_defect",
    "Lead": "pcb_lead_defect",
    "Text": "pcb_text_defect",
}

# The region model's name in the inference service, and the three areas it picks between.
REGION_MODEL = "pcb_region"
REGIONS = ("Body", "Lead", "Text")

CLASSIFIED = "classified"
REGION_GUESS = "region_guess"
# The operator corrected the region (Body / Lead / Text) in the Review Console, so the sample belongs
# to the defect model that region routes to - not the one Agent 1 picked from its own region call.
OPERATOR_REGION = "operator_region"


def region_guess(point: dict[str, Any]) -> dict[str, Any] | None:
    """The region model's answer - its best guess even when it was below the threshold."""

    stage = _stage(point.get("inference") or {}, "feature_classification")
    label = stage.get("prediction") if stage else None
    if not label:
        return None
    confidence = stage.get("confidence") if stage else None
    return {
        "label": str(label),
        "confidence": round(float(confidence), 4) if isinstance(confidence, int | float) else None,
    }


def training_target(
    point: dict[str, Any], region_override: str | None = None
) -> tuple[str | None, str | None]:
    """(the defect model a sample belongs to, how that was decided): the one the operator's own
    region choice routes to when it differs from what ran ("operator_region"), else the model Agent 1
    routed it to ("classified"), else the one its region guess routes to ("region_guess"), else
    (None, None)."""

    routed = sample_model_name(point)
    operator_target = REGION_TO_DEFECT_MODEL.get(region_override) if region_override else None
    if operator_target is not None and operator_target != routed:
        return operator_target, OPERATOR_REGION
    if routed is not None:
        return routed, CLASSIFIED
    guess = region_guess(point)
    target = REGION_TO_DEFECT_MODEL.get(guess["label"]) if guess else None
    return (target, REGION_GUESS) if target else (None, None)


CONFIRMED = "confirmed"
RELABELLED = "relabelled"
NO_PREDICTION = "no_prediction"


def outcome_of(point: dict[str, Any], decision: dict[str, Any]) -> str:
    """confirmed / relabelled / no_prediction - see the module docstring."""

    prediction = model_prediction(point)
    if prediction is None:
        return NO_PREDICTION
    if normalize_defect(prediction) == normalize_defect(str(decision["final_result"])):
        return CONFIRMED
    return RELABELLED


def correction_of(point: dict[str, Any], review: dict[str, Any] | None) -> dict[str, Any] | None:
    """A reviewed sample the model didn't get right (wrong, or couldn't decide) with the operator's
    label, or None when there is no decision or the model predicted that label. `model_name` is the
    defect model the sample belongs to (`model_source` says how that was decided); `queueable` - can
    it be added to training data - when there is one. `model_version` only when that model actually
    ran on the sample."""

    decision = _human_decision(review)
    if decision is None:
        return None
    outcome = outcome_of(point, decision)
    if outcome == CONFIRMED:
        return None
    region_result = decision.get("region_result")
    region_result = region_result if isinstance(region_result, str) and region_result else None
    target, source = training_target(point, region_result)
    return {
        "sample_id": str(point.get("sample_id")),
        "kind": "defect",
        "model_name": target,
        "model_version": sample_model_version(point) if source == CLASSIFIED else None,
        "model_source": source,
        "region_guess": region_guess(point),
        "region_result": region_result,
        "agent1_label": model_prediction(point),
        "machine_label": (point.get("sample") or {}).get("machine_defect"),
        "agent2_label": _agent2_label(review),
        "final_result": str(decision["final_result"]),
        "selected_source": decision.get("selected_source"),
        "operator_notes": decision.get("operator_notes"),
        "outcome": outcome,
        "queueable": target is not None,
    }


def _region_choice(review: dict[str, Any] | None) -> str | None:
    """The part area the operator settled on in the Review Console, one of REGIONS, else None."""

    decision = _human_decision(review)
    value = decision.get("region_result") if decision else None
    return next((r for r in REGIONS if isinstance(value, str) and r.lower() == value.lower()), None)


def region_correction_of(
    point: dict[str, Any], review: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The operator set a different part area than the region model picked: a correction for the
    *region* model, with the operator's area as the label. None when they agreed (or never looked)."""

    decision = _human_decision(review)
    chosen = _region_choice(review)
    guess = region_guess(point)
    if decision is None or chosen is None or guess is None:
        return None
    if guess["label"].lower() == chosen.lower():
        return None
    stage = _stage(point.get("inference") or {}, "feature_classification") or {}
    version = stage.get("model_version")
    return {
        "sample_id": str(point.get("sample_id")),
        "kind": "region",
        "model_name": REGION_MODEL,
        "model_version": version if isinstance(version, str) and version else None,
        "model_source": CLASSIFIED,
        "region_guess": guess,
        "region_result": chosen,
        "agent1_label": guess["label"],  # what the region model said
        "machine_label": (point.get("sample") or {}).get("machine_defect"),
        "agent2_label": _agent2_label(review),
        "final_result": chosen,
        "selected_source": decision.get("selected_source"),
        "operator_notes": decision.get("operator_notes"),
        "outcome": RELABELLED,
        "queueable": True,
    }


def corrections_of(point: dict[str, Any], review: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every correction one reviewed sample carries: the defect label and/or the part area."""

    found = [correction_of(point, review), region_correction_of(point, review)]
    return [c for c in found if c is not None]


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


@dataclass
class _ModelStats:
    model_name: str | None
    model_version: str | None = None
    samples: int = 0
    review_required: int = 0
    decided: int = 0
    confirmed: int = 0
    relabelled: int = 0
    no_prediction: int = 0
    agent2_disagreed: int = 0
    with_confidence: int = 0
    low_confidence: int = 0
    confidence_sum: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "samples": self.samples,
            "review_required": self.review_required,
            "decided": self.decided,
            "confirmed": self.confirmed,
            "relabelled": self.relabelled,
            "no_prediction": self.no_prediction,
            # drift: of the samples the model classified, the ones it got wrong
            "corrected": self.relabelled,
            "correction_rate": _rate(self.relabelled, self.confirmed + self.relabelled),
            "agent2_disagreed": self.agent2_disagreed,
            "low_confidence_rate": _rate(self.low_confidence, self.with_confidence),
            "mean_confidence": (
                round(self.confidence_sum / self.with_confidence, 3) if self.with_confidence else None
            ),
        }


SOURCES = ("MACHINE", "AI", "MANUAL")
_TOP_OVERRULED = 5


def _decision_stats(
    decided_by_source: dict[str, list[bool]], mistakes: Counter[tuple[str, str]], pending: int
) -> dict[str, Any]:
    """What the operator did in the Explanation Review: how many final decisions of each kind and
    how many of those the model got wrong (couldn't-decide samples are not counted as wrong), what is
    still pending, and the model's commonest wrong predictions (model said -> operator's label)."""

    by_source: dict[str, dict[str, int]] = {}
    for source in (*SOURCES, *sorted(set(decided_by_source) - set(SOURCES))):
        outcomes = decided_by_source.get(source, [])
        by_source[source] = {"chosen": len(outcomes), "overruled_model": sum(outcomes)}
    top = sorted(mistakes.items(), key=lambda item: (-item[1], item[0]))[:_TOP_OVERRULED]
    return {
        "by_source": by_source,
        "pending": pending,
        "top_overruled": [
            {"model_label": model_label, "chosen_label": chosen_label, "count": count}
            for (model_label, chosen_label), count in top
        ],
    }


def summarize_run(points: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-model drift for one run (grouped by the defect model that classified each sample - a
    sample no defect model ran on has its own group, `model_name` None, so no model is credited with
    samples it never answered), the samples the model didn't get right, and a breakdown of the
    operator's final decisions."""

    by_sample = {str(r.get("sample_id")): r for r in reviews}
    groups: dict[str | None, _ModelStats] = defaultdict(lambda: _ModelStats(None))
    corrections: list[dict[str, Any]] = []
    decided_by_source: dict[str, list[bool]] = defaultdict(list)
    mistakes: Counter[tuple[str, str]] = Counter()
    threshold = settings.adc_defect_confidence_threshold
    corrected_samples: set[str] = set()
    region_reviewed = region_confirmed = 0
    region_swaps: Counter[tuple[str, str]] = Counter()
    region_corrected_by: Counter[str] = Counter()
    region_samples: Counter[str] = Counter()
    region_confidence_sum: dict[str, float] = defaultdict(float)
    region_confidence_n: Counter[str] = Counter()
    flagged = defect_ran = stopped_at_region = 0

    for point in sorted(points, key=lambda p: str(p.get("sample_id"))):
        review = by_sample.get(str(point.get("sample_id")))
        name = sample_model_name(point)
        stats = groups[name]
        stats.model_name = name
        if name is not None:
            stats.model_version = stats.model_version or sample_model_version(point)
        stats.samples += 1
        stage_one = region_guess(point)
        if stage_one is not None:
            region_samples[stage_one["label"]] += 1
            if stage_one["confidence"] is not None:
                region_confidence_sum[stage_one["label"]] += stage_one["confidence"]
                region_confidence_n[stage_one["label"]] += 1
        if point.get("final_decision") == REVIEW_REQUIRED:
            stats.review_required += 1
            flagged += 1
            if name is not None:
                defect_ran += 1
            else:
                stopped_at_region += 1
        confidence = sample_confidence(point)
        if confidence is not None:
            stats.with_confidence += 1
            stats.confidence_sum += confidence
            if confidence < threshold:
                stats.low_confidence += 1
        agent2 = _agent2_label(review)
        if agent2 is not None and normalize_defect(agent2) != normalize_defect(agent1_label(point)):
            stats.agent2_disagreed += 1

        decision = _human_decision(review)
        if decision is None:
            continue
        stats.decided += 1
        outcome = outcome_of(point, decision)
        if outcome == CONFIRMED:
            stats.confirmed += 1
        elif outcome == RELABELLED:
            stats.relabelled += 1
            said = normalize_defect(model_prediction(point))
            mistakes[(said, normalize_defect(str(decision["final_result"])))] += 1
        else:
            stats.no_prediction += 1
        decided_by_source[str(decision.get("selected_source") or "UNKNOWN")].append(
            outcome == RELABELLED
        )
        found = corrections_of(point, review)
        corrections.extend(found)
        if found:
            corrected_samples.add(str(point.get("sample_id")))

        chosen_region = _region_choice(review)
        guess = region_guess(point)
        if chosen_region is not None and guess is not None:
            region_reviewed += 1
            if guess["label"].lower() == chosen_region.lower():
                region_confirmed += 1
            else:
                region_swaps[(guess["label"], chosen_region)] += 1
                region_corrected_by[guess["label"]] += 1

    models = sorted(groups.values(), key=lambda s: (s.model_name is None, s.model_name or ""))
    totals = {
        "samples": sum(m.samples for m in models),
        "review_required": sum(m.review_required for m in models),
        "decided": sum(m.decided for m in models),
        "confirmed": sum(m.confirmed for m in models),
        "relabelled": sum(m.relabelled for m in models),
        "no_prediction": sum(m.no_prediction for m in models),
    }
    totals["corrected"] = totals["relabelled"]
    all_conf_n = sum(region_confidence_n.values())
    region = {
        "reviewed": region_reviewed,
        "confirmed": region_confirmed,
        "corrected": region_reviewed - region_confirmed,
        "accuracy": _rate(region_confirmed, region_reviewed),
        "mean_confidence": (
            round(sum(region_confidence_sum.values()) / all_conf_n, 3) if all_conf_n else None
        ),
        "stopped_here": stopped_at_region,
        "by_region": [
            {
                "region": area,
                "samples": region_samples.get(area, 0),
                "corrected": region_corrected_by.get(area, 0),
                "mean_confidence": (
                    round(region_confidence_sum[area] / region_confidence_n[area], 3)
                    if region_confidence_n.get(area)
                    else None
                ),
            }
            for area in sorted(set(region_samples) | set(region_corrected_by))
        ],
        "swaps": [
            {"model_region": said, "chosen_region": chosen, "count": count}
            for (said, chosen), count in sorted(
                region_swaps.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }
    funnel = {
        "flagged": flagged,
        "defect_ran": defect_ran,
        "reviewed": totals["decided"],
        "corrected": len(corrected_samples),
    }
    return {
        "totals": totals,
        # of the samples the model classified, the share it got wrong; None if it classified none
        "drift_rate": _rate(totals["relabelled"], totals["confirmed"] + totals["relabelled"]),
        "models": [m.to_dict() for m in models],
        "region": region,
        "funnel": funnel,
        "corrections": corrections,
        "decisions": _decision_stats(
            decided_by_source, mistakes, max(totals["review_required"] - totals["decided"], 0)
        ),
    }
