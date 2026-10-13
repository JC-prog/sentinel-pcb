"""Covers the two files swapped to call the inference/ microservice instead of loading local ONNX
files: app/workflow/src/agent1_orchestrator/services/{model_lifecycle,multimodal_inference}.py.
Imported through the same sys.path bridge app/workflow/services/streaming.py uses at runtime (see
_bridge_sys_path there) - these modules use the teammate's own bare (unqualified) imports.
"""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.shared.config.settings import settings
from app.shared.inference.schemas import Classification
from app.workflow.services.streaming import _bridge_sys_path

_bridge_sys_path()

# Bare imports only resolvable via _bridge_sys_path() above - app/workflow/src is excluded from
# mypy (pyproject.toml) the same way it's excluded from ruff, so it can't be resolved statically.
from services.model_lifecycle import ModelLifecycleService  # type: ignore
from services.multimodal_inference import TwoStageInferenceService  # type: ignore


@pytest.fixture(autouse=True)
def _fake_image_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real file needs to exist on disk for these tests - only _classify's read_bytes() call
    is exercised, never the actual image content."""

    monkeypatch.setattr(Path, "read_bytes", lambda self: b"fake-image-bytes")


def _classification(**overrides: Any) -> Classification:
    base: dict[str, Any] = {
        "model": "pcb_region",
        "model_version": "JcProg/PCBInspect-Region@abc123",
        "username": "workflow",
        "label": "Body",
        "index": 0,
        "confidence": 0.97,
        "scores": {"Body": 0.97, "Lead": 0.02, "Text": 0.01},
        "request_id": "req-1",
    }
    base.update(overrides)
    return Classification.model_validate(base)


def test_get_model_reports_missing_when_inference_base_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")
    result = ModelLifecycleService().get_model("feature")
    assert result.success is False
    assert result.status == "MODEL_FILES_MISSING"
    assert result.recoverable is True


def test_get_model_rejects_unknown_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.internal")
    result = ModelLifecycleService().get_model("nonsense")
    assert result.success is False
    assert result.status == "MODEL_NOT_CONFIGURED"


def test_get_model_maps_key_to_service_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.internal")
    result = ModelLifecycleService(project_root="ignored", config_path="ignored").get_model("BODY")
    assert result.success is True
    assert result.data == {"model_key": "body", "service_model": "pcb_body_defect"}


def test_infer_sample_populates_service_model_and_model_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A plain (non-async) test on purpose: infer_sample/_classify are synchronous and bridge into
    # the async classify() via asyncio.run() internally - exactly like the real call path, where
    # they only ever run inside an asyncio.to_thread worker with no event loop of its own already
    # running (see multimodal_inference.py's module docstring). Calling them from an async test
    # would hit asyncio.run()'s "cannot be called from a running event loop" error instead.
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.internal")

    responses = [
        _classification(model="pcb_region", label="Body", confidence=0.95),
        _classification(
            model="pcb_body_defect",
            model_version="JcProg/PCBInspect-BodyDefect@def456",
            label="WrongPart",
            confidence=0.88,
            scores={"WrongPart": 0.88},
        ),
    ]
    mock_classify = AsyncMock(side_effect=responses)
    monkeypatch.setattr("services.multimodal_inference.classify", mock_classify)

    service = TwoStageInferenceService(ModelLifecycleService())
    result = service.infer_sample({
        "sample_id": "S1",
        "source_feature": "Body",
        "machine_defect": "WrongPart_13",
        "defect_image": "S1/sample.jpg",
    })

    assert result.success is True
    assert result.data["routing"] == {"selected_model": "body", "service_model": "pcb_body_defect"}
    assert result.data["defect_classification"]["model_version"] == "JcProg/PCBInspect-BodyDefect@def456"
    assert result.data["feature_classification"]["model_version"] == "JcProg/PCBInspect-Region@abc123"
    assert mock_classify.call_count == 2
    assert mock_classify.call_args_list[0].kwargs["model"] == "pcb_region"
    assert mock_classify.call_args_list[0].kwargs["username"] == "workflow"
    assert mock_classify.call_args_list[1].kwargs["model"] == "pcb_body_defect"


def test_classify_treats_empty_model_version_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inference service predating versioning reports model_version="" - normalized to None
    rather than kept as a falsy-but-truthy-looking empty string. Plain (non-async) test - see the
    comment on test_infer_sample_populates_service_model_and_model_version above."""

    mock_classify = AsyncMock(
        return_value=_classification(label="Text", confidence=0.9, model_version="")
    )
    monkeypatch.setattr("services.multimodal_inference.classify", mock_classify)

    service = TwoStageInferenceService(ModelLifecycleService())
    stage = service._classify("pcb_region", "S2/sample.jpg")

    assert stage is not None
    assert stage["model_version"] is None
    assert stage["prediction"] == "Text"
    mock_classify.assert_awaited_once()


def test_infer_sample_reports_inference_service_error_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.internal")

    async def _raise(**_kwargs: Any) -> Classification:
        raise RuntimeError("inference service unreachable")

    monkeypatch.setattr("services.multimodal_inference.classify", _raise)

    service = TwoStageInferenceService(ModelLifecycleService())
    result = service.infer_sample({
        "sample_id": "S3",
        "source_feature": "Body",
        "machine_defect": "Golden",
        "defect_image": "S3/sample.jpg",
    })

    assert result.success is False
    assert result.status == "INFERENCE_SERVICE_ERROR"
    assert result.recoverable is True
