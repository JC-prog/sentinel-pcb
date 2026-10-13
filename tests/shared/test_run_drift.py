"""app/shared/modelops/run_drift.py: how much the model drifted in one Work-tab run, judged by the
operator's final decisions (their relabels), computed from the stored payloads (pure - no I/O)."""

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


def test_the_final_decision_is_the_label_and_drift_is_what_the_model_did_not_get_right() -> None:
    points = [_point(f"S{i}", label="MissingPart") for i in range(1, 6)]
    reviews = [
        _decision("S1", "missing part", source="MACHINE"),  # the console's Machine = model's call
        _decision("S2", "wrong part", source="AI", notes="bare pads"),
        _decision("S3", "Tombstone", source="MANUAL"),
        _decision("S4", "MissingPart", source="MANUAL"),  # picked the model's own class
    ]  # S5 not reviewed yet

    summary = summarize_run(points, reviews)

    assert summary["totals"] == {
        "samples": 5,
        "review_required": 5,
        "decided": 4,
        "confirmed": 2,
        "relabelled": 2,
        "no_prediction": 0,
        "corrected": 2,
    }
    assert summary["drift_rate"] == 0.5
    (model,) = summary["models"]
    assert (model["confirmed"], model["relabelled"], model["no_prediction"]) == (2, 2, 0)
    assert (model["corrected"], model["correction_rate"]) == (2, 0.5)
    assert [c["sample_id"] for c in summary["corrections"]] == ["S2", "S3"]
    assert summary["corrections"][0] == {
        "sample_id": "S2",
        "kind": "defect",
        "model_name": "pcb_body_defect",
        "model_version": "JcProg/body@v2",
        "model_source": "classified",
        "region_guess": {"label": "Body", "confidence": 0.95},
        "region_result": None,
        "agent1_label": "MissingPart",
        "machine_label": "WrongPart_13",
        "agent2_label": None,
        "final_result": "wrong part",
        "selected_source": "AI",
        "operator_notes": "bare pads",
        "outcome": "relabelled",
        "queueable": True,
    }


def test_the_saved_label_counts_not_which_button_was_pressed() -> None:
    """Whatever the decision type, it is the saved final result that is compared with the model - the
    dataset's machine label plays no part."""

    points = [
        _point("S1", label="MissingPart", machine_defect="WrongPart_13"),
        _point("S2", label="MissingPart", machine_defect="MissingPart_1"),
    ]
    reviews = [
        _decision("S1", "missing part", source="MACHINE"),  # machine label differs - irrelevant
        _decision("S2", "wrong part", source="MACHINE"),  # saved label differs from the model
    ]

    summary = summarize_run(points, reviews)

    assert [c["sample_id"] for c in summary["corrections"]] == ["S2"]
    assert summary["corrections"][0]["final_result"] == "wrong part"


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


def test_a_sample_the_model_couldnt_decide_is_not_drift_but_trains_the_model_its_region_guess_routes_to() -> None:
    """It stopped at the region stage (below the threshold): the model never answered, so it is not
    drift and no model is credited with it in the per-model table - but the operator's label can
    still train the defect model the region guess routes to."""

    points = [_point("S1", model=None), _point("S2", model=None)]
    reviews = [
        _decision("S1", "tombstone"),
        _decision("S2", "wrong part", source="MACHINE"),
    ]

    summary = summarize_run(points, reviews)

    # no defect model ran, so both sit in the "no model answered" group, not under the text model
    # the region guess ("Text") routes them to
    (group,) = summary["models"]
    assert (group["model_name"], group["model_version"]) == (None, None)
    assert (group["decided"], group["no_prediction"], group["relabelled"]) == (2, 2, 0)
    assert (group["corrected"], group["correction_rate"]) == (0, None)
    assert summary["drift_rate"] is None  # nothing classified: undefined, not 0% or 100%
    assert summary["totals"]["no_prediction"] == 2 and summary["totals"]["corrected"] == 0
    first = summary["corrections"][0]
    assert (first["outcome"], first["agent1_label"]) == ("no_prediction", None)
    assert (first["model_name"], first["model_source"]) == ("pcb_text_defect", "region_guess")
    assert first["region_guess"] == {"label": "Text", "confidence": 0.3}
    # the region model's version must not be stamped on a defect-model ticket
    assert first["model_version"] is None
    assert all(c["queueable"] for c in summary["corrections"])
    assert summary["decisions"]["top_overruled"] == []


def test_drift_counts_only_the_samples_the_model_classified() -> None:
    points = [_point("S1"), _point("S2"), _point("S3", model=None), _point("S4", model=None)]
    reviews = [
        _decision("S1", "missing part"),  # right
        _decision("S2", "wrong part"),  # wrong
        _decision("S3", "tombstone"),  # couldn't decide
        _decision("S4", "tombstone"),  # couldn't decide
    ]

    summary = summarize_run(points, reviews)

    assert summary["drift_rate"] == 0.5  # 1 wrong of 2 classified - the two undecided don't count
    assert summary["totals"]["decided"] == 4


def test_a_region_guess_with_no_defect_model_cannot_be_added() -> None:
    point = _point("S1", model=None)
    point["inference"]["details"]["feature_classification"]["prediction"] = "Gap"  # no route

    (correction,) = summarize_run([point], [_decision("S1", "tombstone")])["corrections"]

    assert (correction["model_name"], correction["model_source"]) == (None, None)
    assert correction["queueable"] is False


def test_the_region_routing_matches_agent_1() -> None:
    """REGION_TO_DEFECT_MODEL re-states Agent 1's routing (shared may not import workflow) - read the
    drop-in's own files so the two cannot drift apart."""

    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "workflow" / "src" / "agent1_orchestrator"

    def literal(path: Path, name: str) -> dict[str, str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                value: dict[str, str] = ast.literal_eval(node.value)
                return value
        raise AssertionError(f"{name} not found in {path}")

    routes = literal(root / "inference" / "router.py", "ROUTES")
    services = literal(root / "services" / "model_lifecycle.py", "_MODEL_KEY_TO_SERVICE_NAME")
    assert run_drift.REGION_TO_DEFECT_MODEL == {region: services[key] for region, key in routes.items()}


def test_an_empty_run_summarises_to_nothing() -> None:
    assert summarize_run([], []) == {
        "totals": {
            "samples": 0,
            "review_required": 0,
            "decided": 0,
            "confirmed": 0,
            "relabelled": 0,
            "no_prediction": 0,
            "corrected": 0,
        },
        "region": {
            "reviewed": 0,
            "confirmed": 0,
            "corrected": 0,
            "accuracy": None,
            "mean_confidence": None,
            "stopped_here": 0,
            "by_region": [],
            "swaps": [],
        },
        "funnel": {"flagged": 0, "defect_ran": 0, "reviewed": 0, "corrected": 0},
        "drift_rate": None,
        "models": [],
        "corrections": [],
        "decisions": {
            "by_source": {
                "MACHINE": {"chosen": 0, "overruled_model": 0},
                "AI": {"chosen": 0, "overruled_model": 0},
                "MANUAL": {"chosen": 0, "overruled_model": 0},
            },
            "pending": 0,
            "top_overruled": [],
        },
    }


def test_the_decision_statistics_split_what_the_operator_did_by_kind() -> None:
    points = [
        _point("S1", label="MissingPart"),
        _point("S2", label="MissingPart"),
        _point("S3", label="MissingPart"),
        _point("S4", label="MissingPart"),
        _point("S5", label="Shift"),
        _point("S6", label="MissingPart"),
        _point("S7", model=None),
    ]
    reviews = [
        _decision("S1", "missing part", source="MACHINE"),  # model right
        _decision("S2", "wrong part", source="MACHINE"),  # relabelled
        _decision("S3", "wrong part", source="AI"),  # relabelled
        _decision("S4", "wrong part", source="MANUAL"),  # relabelled
        _decision("S5", "shifted", source="AI"),  # model right ("Shift" == "shifted")
        _decision("S7", "tombstone", source="MANUAL"),  # model gave no answer
    ]  # S6 is still waiting for the operator

    stats = summarize_run(points, reviews)["decisions"]

    assert stats["by_source"] == {
        "MACHINE": {"chosen": 2, "overruled_model": 1},
        "AI": {"chosen": 2, "overruled_model": 1},
        "MANUAL": {"chosen": 2, "overruled_model": 1},  # S7: couldn't decide is not "wrong"
    }
    assert stats["pending"] == 1
    # the model's commonest wrong predictions, most frequent first
    assert stats["top_overruled"] == [
        {"model_label": "missing part", "chosen_label": "wrong part", "count": 3},
    ]


def test_the_commonest_misses_are_ranked_and_capped() -> None:
    labels = ["Tombstone", "Shift", "WrongPart", "ForeignMaterial", "SolderInsufficient", "MissingPart"]
    points = [_point(f"S{i}", label="Golden", machine_defect="Golden") for i in range(1, 9)]
    # Tombstone twice, then one each of the rest - six distinct misses, only five are listed
    chosen = [labels[0], labels[0], *labels[1:], "Golden"]
    reviews = [_decision(f"S{i}", label, source="MANUAL") for i, label in enumerate(chosen[:7], start=1)]

    top = summarize_run(points, reviews)["decisions"]["top_overruled"]

    assert len(top) == 5
    assert top[0] == {"model_label": "no defect", "chosen_label": "tombstone", "count": 2}
    assert [t["count"] for t in top] == [2, 1, 1, 1, 1]


def test_the_helpers_read_both_inference_shapes() -> None:
    completed = _point("S1")
    stage_one = _point("S2", model=None)

    assert run_drift.sample_model_name(completed) == "pcb_body_defect"
    assert run_drift.sample_model_version(completed) == "JcProg/body@v2"
    assert run_drift.sample_confidence(completed) == 0.9
    assert run_drift.sample_model_name(stage_one) is None
    assert run_drift.sample_confidence(stage_one) is None


def test_the_operators_region_choice_routes_a_correction_to_the_defect_model_that_region_belongs_to() -> None:
    point = _point("S1", label="MissingPart", model="pcb_body_defect")  # the model said Body
    review = _decision("S1", "wrong part")
    review["human_decision"]["region_result"] = "Lead"

    correction = run_drift.correction_of(point, review)

    assert correction is not None
    assert correction["region_result"] == "Lead"
    assert (correction["model_name"], correction["model_source"]) == ("pcb_lead_defect", "operator_region")
    # no model version: the lead model never ran on this sample
    assert correction["model_version"] is None


def test_a_region_choice_that_matches_what_ran_keeps_the_classified_model() -> None:
    point = _point("S1", label="MissingPart", model="pcb_body_defect")
    review = _decision("S1", "wrong part")
    review["human_decision"]["region_result"] = "Body"

    correction = run_drift.correction_of(point, review)

    assert correction is not None
    assert (correction["model_name"], correction["model_source"]) == ("pcb_body_defect", "classified")


def _with_region(review: dict[str, Any], region: str) -> dict[str, Any]:
    review["human_decision"]["region_result"] = region
    return review


def test_a_region_the_operator_changed_is_a_correction_for_the_region_model() -> None:
    point = _point("S1", label="MissingPart", model="pcb_body_defect")  # the region model said Body
    review = _with_region(_decision("S1", "missing part"), "Lead")  # agrees with the defect label

    found = run_drift.corrections_of(point, review)

    assert [c["kind"] for c in found] == ["region"]  # the defect label was right
    region = found[0]
    assert (region["model_name"], region["final_result"], region["agent1_label"]) == (
        "pcb_region",
        "Lead",
        "Body",
    )
    assert region["queueable"] is True


def test_a_sample_can_carry_a_defect_and_a_region_correction() -> None:
    point = _point("S1", label="MissingPart", model="pcb_body_defect")
    review = _with_region(_decision("S1", "tombstone"), "Lead")

    found = run_drift.corrections_of(point, review)

    assert sorted((c["kind"], c["model_name"]) for c in found) == [
        ("defect", "pcb_lead_defect"),  # trained on the lead model: the operator says it is a lead part
        ("region", "pcb_region"),
    ]


def test_confirming_the_region_is_not_a_correction_but_counts_as_reviewed() -> None:
    point = _point("S1", label="MissingPart", model="pcb_body_defect")
    review = _with_region(_decision("S1", "missing part"), "Body")

    assert run_drift.corrections_of(point, review) == []
    summary = summarize_run([point], [review])
    assert (summary["region"]["reviewed"], summary["region"]["confirmed"]) == (1, 1)
    assert summary["region"]["accuracy"] == 1.0


def test_region_stats_and_funnel_describe_stage_one() -> None:
    points = [
        _point("S1", label="MissingPart", model="pcb_body_defect"),
        _point("S2", label="MissingPart", model="pcb_body_defect"),
        _point("S3", model=None),  # stopped at the region model
    ]
    reviews = [
        _with_region(_decision("S1", "missing part"), "Body"),
        _with_region(_decision("S2", "missing part"), "Lead"),
    ]

    summary = summarize_run(points, reviews)

    region = summary["region"]
    assert (region["reviewed"], region["confirmed"], region["corrected"]) == (2, 1, 1)
    assert region["accuracy"] == 0.5
    assert region["stopped_here"] == 1
    assert region["swaps"] == [{"model_region": "Body", "chosen_region": "Lead", "count": 1}]
    by_area = {r["region"]: r for r in region["by_region"]}
    assert (by_area["Body"]["samples"], by_area["Body"]["corrected"]) == (2, 1)
    assert summary["funnel"] == {"flagged": 3, "defect_ran": 2, "reviewed": 2, "corrected": 1}
