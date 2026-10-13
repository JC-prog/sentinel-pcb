"""The inspect agent's verdict rules - pure, so no database or inference service involved."""

import pytest

from app.chat.agents.inspection_agent.state import InspectionRequest, InspectionRun, Stage
from app.chat.agents.inspection_agent.verdict import decide
from app.chat.db.models import CaseStatus
from app.shared.config.settings import settings


def _run(**fields: object) -> InspectionRun:
    request = InspectionRequest(
        image_bytes=b"",
        image_name="x.png",
        username="u",
        user_id="1",
        conversation_id="c",
        board_id="b",
        component_ref="U1",
    )
    run = InspectionRun(request=request)
    for key, value in fields.items():
        setattr(run, key, value)
    return run


def _stage(confidence: float, model: str = "pcb_body_defect") -> Stage:
    return Stage(
        model=model, model_version="v1", label="MissingPart", confidence=confidence, scores={}
    )


def _invalid(issue: str) -> dict[str, object]:
    return {"valid": False, "issues": [issue], "inspection_results": {}}


def test_a_confident_defect_is_accepted() -> None:
    verdict = decide(_run(region=_stage(0.95, "pcb_region"), defect=_stage(0.95)))

    assert verdict.status == CaseStatus.ACCEPTED
    assert verdict.reasons == []


def test_a_defect_below_the_threshold_needs_review() -> None:
    low = settings.adc_defect_confidence_threshold - 0.1

    verdict = decide(_run(region=_stage(0.95, "pcb_region"), defect=_stage(low)))

    assert verdict.review_required
    assert "defect confidence" in verdict.reasons[0]


def test_an_uncertain_region_needs_review_and_says_no_defect_model_ran() -> None:
    low = settings.adc_region_confidence_threshold - 0.1

    verdict = decide(_run(region=_stage(low, "pcb_region"), region_uncertain=True))

    assert verdict.review_required
    assert "no defect model was run" in verdict.reasons[0]


def test_failed_measurement_validation_overrides_a_confident_classifier() -> None:
    verdict = decide(
        _run(
            region=_stage(0.95, "pcb_region"),
            defect=_stage(0.95),
            measurement_validation=_invalid("MALFORMED_NUMERIC:x"),
        )
    )

    assert verdict.review_required
    assert "MALFORMED_NUMERIC:x" in verdict.reasons[0]


@pytest.mark.parametrize(
    "validation", [None, {"valid": True, "issues": [], "inspection_results": {}}]
)
def test_missing_or_passing_measurements_do_not_block_acceptance(
    validation: dict[str, object] | None,
) -> None:
    verdict = decide(
        _run(
            region=_stage(0.95, "pcb_region"),
            defect=_stage(0.95),
            measurement_validation=validation,
        )
    )

    assert verdict.status == CaseStatus.ACCEPTED


def test_every_failing_rule_is_reported_not_just_the_first() -> None:
    low = settings.adc_defect_confidence_threshold - 0.1

    verdict = decide(
        _run(
            region=_stage(0.95, "pcb_region"),
            defect=_stage(low),
            measurement_validation=_invalid("XML_UNPARSEABLE"),
        )
    )

    assert len(verdict.reasons) == 2
