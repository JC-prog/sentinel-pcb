"""app/shared/modelops/run_drift.py: drift numbers and operator corrections for one Work-tab run,
computed from the stored sample and review payloads (pure - no I/O)."""

from typing import Any

import pytest

from app.shared.config.settings import settings
from app.shared.modelops import run_drift
from app.shared.modelops.run_drift import normalize_defect, summarize_run


def _point(
    sample_id: str,
    *,
    label: str | None = "MissingPart",
    confidence: float = 0.9,
    decision: str = "REVIEW_REQUIRED",
    model: str | None = "pcb_body_defect",
    machine_defect: str = "WrongPart_13",
) -> dict[str, Any]:
    """A stored sample point. `model=None` makes a sample that stopped at feature classification
    (its stage-1 answer nested under inference.details, no routing)."""

    if model is None:
        inference: dict[str, Any] = {
            "sample_id": sample_id,
            "status": "FEATURE_CLASSIFICATION_UNCERTAIN",
            "final_decision": decision,
            "details": {"feature_classification": {"prediction": "Text", "confidence": 0.3}},
        }
    else:
        inference = {
            "sample_id": sample_id,
            "final_decision": decision,
            "feature_classification": {"prediction": "Body", "confidence": 0.95},
            "routing": {"selected_model": "body", "service_model": model},
            "defect_classification": {
                "prediction": label,
                "confidence": confidence,
                "model_version": "JcProg/body@v2",
            },
        }
    return {
        "run_id": "run-a",
        "sample_id": sample_id,
        "sample": {"sample_id": sample_id, "machine_defect": machine_defect},
        "inference": inference,
        "final_decision": decision,
    }


def _decision(sample_id: str, final: str, source: str = "MANUAL", notes: str | None = None) -> dict[str, Any]:
    return {
        "run_id": "run-a",
        "sample_id": sample_id,
        "human_decision": {"selected_source": source, "final_result": final, "operator_notes": notes},
        "review_status": "COMPLETED",
    }


def _agent2(sample_id: str, predicted: str) -> dict[str, Any]:
    return {
        "run_id": "run-a",
        "sample_id": sample_id,
        "result": {"review_status": "GENERATED_UNVALIDATED", "output": {"predicted_defect": predicted}},
    }


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("MissingPart", "missing part"),
        ("WrongPart_13", "wrong part"),
        ("Shift", "shifted"),
        ("shifted", "shifted"),
        ("SolderInsuffcient_5", "solder insufficient"),  # the datasets' own spelling
        ("SolderInsufficient", "solder insufficient"),
        ("Golden", "no defect"),
        (None, "no defect"),
        ("No Defect / Pass", "no defect / pass"),
    ],
)
def test_normalize_defect(label: str | None, expected: str) -> None:
    assert normalize_defect(label) == expected


def test_a_sample_without_a_decision_is_not_a_correction() -> None:
    summary = summarize_run([_point("S1")], [])

    assert summary["corrections"] == []
    (model,) = summary["models"]
    assert (model["samples"], model["decided"], model["corrected"]) == (1, 0, 0)
    assert model["correction_rate"] is None


def test_accept_machine_is_agreement_and_a_different_result_is_a_correction() -> None:
    points = [_point("S1"), _point("S2"), _point("S3"), _point("S4")]
    reviews = [
        _decision("S1", "missing part", source="MACHINE"),  # same as Agent 1 (normalised)
        _decision("S2", "wrong part", source="AI", notes="bare pads"),
        _decision("S3", "Tombstone", source="MANUAL"),
    ]

    summary = summarize_run(points, reviews)

    (model,) = summary["models"]
    assert (model["decided"], model["corrected"], model["correction_rate"]) == (3, 2, 0.667)
    assert [c["sample_id"] for c in summary["corrections"]] == ["S2", "S3"]
    first = summary["corrections"][0]
    assert first == {
        "sample_id": "S2",
        "model_name": "pcb_body_defect",
        "model_version": "JcProg/body@v2",
        "agent1_label": "MissingPart",
        "final_result": "wrong part",
        "selected_source": "AI",
        "operator_notes": "bare pads",
        "queueable": True,
    }
    assert summary["totals"] == {"samples": 4, "review_required": 4, "decided": 3, "corrected": 2}


def test_agent2_disagreement_and_confidence_are_counted_per_model() -> None:
    monkeypatched = settings.adc_defect_confidence_threshold
    points = [
        _point("S1", confidence=0.9),
        _point("S2", confidence=monkeypatched - 0.1),
        _point("S3", label="Shift", model="pcb_lead_defect"),
    ]
    reviews = [_agent2("S1", "missing part"), _agent2("S2", "tombstone"), _agent2("S3", "shifted")]

    summary = summarize_run(points, reviews)

    body, lead = summary["models"]
    assert (body["model_name"], lead["model_name"]) == ("pcb_body_defect", "pcb_lead_defect")
    assert body["agent2_disagreed"] == 1  # S2: tombstone vs missing part
    assert lead["agent2_disagreed"] == 0  # S3: "Shift" and "shifted" agree
    assert body["low_confidence_rate"] == 0.5
    assert body["mean_confidence"] == round((0.9 + monkeypatched - 0.1) / 2, 3)


def test_a_sample_that_stopped_at_stage_one_has_no_model_and_cannot_be_queued() -> None:
    points = [_point("S1", model=None)]

    summary = summarize_run(points, [_decision("S1", "tombstone")])

    (group,) = summary["models"]
    assert group["model_name"] is None and group["samples"] == 1
    # Agent 1's baseline is the dataset's own machine defect (WrongPart_13 -> wrong part)
    (correction,) = summary["corrections"]
    assert correction["agent1_label"] == "WrongPart_13"
    assert correction["queueable"] is False and correction["model_name"] is None


def test_an_empty_run_summarises_to_nothing() -> None:
    assert summarize_run([], []) == {
        "totals": {"samples": 0, "review_required": 0, "decided": 0, "corrected": 0},
        "models": [],
        "corrections": [],
    }


def test_the_helpers_read_both_inference_shapes() -> None:
    completed = _point("S1")
    stage_one = _point("S2", model=None)

    assert run_drift.sample_model_name(completed) == "pcb_body_defect"
    assert run_drift.sample_model_version(completed) == "JcProg/body@v2"
    assert run_drift.sample_confidence(completed) == 0.9
    assert run_drift.sample_model_name(stage_one) is None
    assert run_drift.sample_confidence(stage_one) is None
