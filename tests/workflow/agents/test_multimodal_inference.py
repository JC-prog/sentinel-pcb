"""TwoStageInferenceService's routing/version bookkeeping (app/workflow/agents/orchestrator_agent/
services/multimodal_inference.py) - the two fields app/shared/modelops needs from a workflow
sample that the ported code didn't previously keep: the real inference-service model name
("service_model", not route_feature()'s raw "body"/"lead"/"text" key) and each stage's
model_version. Everything else about this ported service is covered by
test_orchestrator_escalation.py at the orchestrator level.
"""

from pathlib import Path
from typing import Any

import pytest

from app.shared.inference import Classification
from app.workflow.agents.orchestrator_agent.services.model_lifecycle import ModelLifecycleService
from app.workflow.agents.orchestrator_agent.services.multimodal_inference import (
    TwoStageInferenceService,
)

# orchestrator_agent/ is excluded from strict typing (pyproject.toml) as a close, deliberately
# unannotated port - calling into it from this typed test file needs no-untyped-call silenced,
# same as test_orchestrator_escalation.py's _agent() helper.


def _sample(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    image_path = tmp_path / "defect.jpg"
    image_path.write_bytes(b"fake-image-bytes")
    base = {
        "sample_id": "S1",
        "defect_image": str(image_path),
        "source_feature": "Body",
        "machine_defect": "MissingPart",
        "failed_inspections": {},
    }
    base.update(overrides)
    return base


def _classification(model: str, label: str, confidence: float, version: str) -> Classification:
    return Classification(
        model=model,
        model_version=version,
        username="tester",
        label=label,
        index=0,
        confidence=confidence,
        scores={label: confidence},
        request_id="req-1",
    )


@pytest.fixture(autouse=True)
def _inference_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.shared.config.settings import settings

    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")


def _service(username: str = "tester") -> TwoStageInferenceService:
    return TwoStageInferenceService(ModelLifecycleService(), username)  # type: ignore[no-untyped-call]


async def test_a_successful_sample_records_the_real_model_name_and_each_stages_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = [
        _classification("pcb_region", "Body", 0.95, "JcProg/region@v1"),
        _classification("pcb_body_defect", "MissingPart", 0.88, "JcProg/body@v2"),
    ]

    async def fake_classify(**kwargs: Any) -> Classification:
        return responses.pop(0)

    monkeypatch.setattr(
        "app.workflow.agents.orchestrator_agent.services.multimodal_inference.classify",
        fake_classify,
    )

    result = await _service().infer_sample(_sample(tmp_path))  # type: ignore[no-untyped-call]

    assert result.success
    routing = result.data["routing"]
    # "selected_model" stays the raw routing key; "service_model" is the name modelops code
    # (drift reports, retraining tickets) must match against - they are NOT the same string.
    assert routing == {"selected_model": "body", "service_model": "pcb_body_defect"}
    assert result.data["feature_classification"]["model_version"] == "JcProg/region@v1"
    assert result.data["defect_classification"]["model_version"] == "JcProg/body@v2"


async def test_an_empty_version_from_the_inference_service_normalizes_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service that predates versioning reports model_version="" - never an empty-looking
    string, so callers can tell "no data" apart from a real (if short) version string."""

    responses = [
        _classification("pcb_region", "Lead", 0.95, ""),
        _classification("pcb_lead_defect", "SolderInsufficient", 0.9, ""),
    ]

    async def fake_classify(**kwargs: Any) -> Classification:
        return responses.pop(0)

    monkeypatch.setattr(
        "app.workflow.agents.orchestrator_agent.services.multimodal_inference.classify",
        fake_classify,
    )

    result = await _service().infer_sample(_sample(tmp_path, source_feature="Lead"))  # type: ignore[no-untyped-call]

    assert result.data["feature_classification"]["model_version"] is None
    assert result.data["defect_classification"]["model_version"] is None


async def test_a_sample_that_never_reaches_stage_two_still_carries_stage_ones_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Low stage-1 confidence stops before routing/stage 2 - the only place a caller can still
    recover a model_version/name from is feature_classification, under "details" on failure."""

    async def fake_classify(**kwargs: Any) -> Classification:
        return _classification("pcb_region", "Body", 0.10, "JcProg/region@v1")

    monkeypatch.setattr(
        "app.workflow.agents.orchestrator_agent.services.multimodal_inference.classify",
        fake_classify,
    )

    result = await _service().infer_sample(_sample(tmp_path))  # type: ignore[no-untyped-call]

    assert result.success is False
    assert result.status == "FEATURE_CLASSIFICATION_UNCERTAIN"
    assert result.data["feature_classification"]["model_version"] == "JcProg/region@v1"
    assert "routing" not in result.data  # never reached stage 2 - no service_model to report
