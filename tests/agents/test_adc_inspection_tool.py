import json
from collections.abc import Callable

import httpx
import pytest

from app.agents.adc_inspection_agent import AdcInspectionTool
from app.config.settings import settings

_RealAsyncClient = httpx.AsyncClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return _RealAsyncClient(*args, **kwargs)  # type: ignore[arg-type]

    # Patches the shared httpx module object (the same one app.inference.client's own `import
    # httpx` sees) - matches tests/test_inference_client.py's own pattern.
    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _classify_response(model: str, label: str, index: int, scores: dict[str, float]) -> dict:
    return {
        "model": model,
        "username": "jane-qa",
        "label": label,
        "index": index,
        "confidence": scores[label],
        "scores": scores,
        "request_id": f"req-{model}",
    }


@pytest.fixture(autouse=True)
def _inference_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "http://inference.test:8001")


async def test_run_routes_body_region_to_the_body_defect_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode(errors="ignore")
        if "pcb_region" in body:
            return httpx.Response(
                200,
                json=_classify_response(
                    "pcb_region", "Body", 0, {"Body": 0.9, "Lead": 0.05, "Text": 0.05}
                ),
            )
        assert "pcb_body_defect" in body
        return httpx.Response(
            200,
            json=_classify_response(
                "pcb_body_defect", "MissingPart", 2, {"Golden": 0.1, "MissingPart": 0.8}
            ),
        )

    _mock_async_client(monkeypatch, handler)

    result = json.loads(
        await AdcInspectionTool().run(
            image_bytes=b"\x89PNGfake", image_name="board.png", username="jane-qa"
        )
    )

    assert result["region"] == "Body"
    assert result["defect_model"] == "pcb_body_defect"
    assert result["defect_label"] == "MissingPart"
    assert result["defect_confidence"] == 0.8


async def test_run_routes_lead_region_to_the_lead_defect_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode(errors="ignore")
        if "pcb_region" in body:
            return httpx.Response(
                200,
                json=_classify_response(
                    "pcb_region", "Lead", 1, {"Body": 0.05, "Lead": 0.9, "Text": 0.05}
                ),
            )
        assert "pcb_lead_defect" in body
        return httpx.Response(
            200,
            json=_classify_response(
                "pcb_lead_defect", "SolderInsufficient", 1, {"Golden": 0.2, "SolderInsufficient": 0.8}
            ),
        )

    _mock_async_client(monkeypatch, handler)

    result = json.loads(
        await AdcInspectionTool().run(
            image_bytes=b"\x89PNGfake", image_name="board.png", username="jane-qa"
        )
    )

    assert result["region"] == "Lead"
    assert result["defect_model"] == "pcb_lead_defect"
    assert result["defect_label"] == "SolderInsufficient"


async def test_run_returns_error_when_inference_service_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "inference_base_url", "")

    result = json.loads(
        await AdcInspectionTool().run(
            image_bytes=b"\x89PNGfake", image_name="board.png", username="jane-qa"
        )
    )

    assert "error" in result


async def test_run_returns_error_on_upstream_region_classification_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_async_client(monkeypatch, lambda request: httpx.Response(500, text="boom"))

    result = json.loads(
        await AdcInspectionTool().run(
            image_bytes=b"\x89PNGfake", image_name="board.png", username="jane-qa"
        )
    )

    assert "error" in result


def test_tool_metadata_shape() -> None:
    tool = AdcInspectionTool()
    assert tool.name == "adc_inspection"
    assert tool.parameters["required"] == ["image_id"]
    assert set(tool.parameters["properties"]) == {"image_id"}
