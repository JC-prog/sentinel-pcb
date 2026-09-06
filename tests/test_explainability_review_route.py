import io
import shutil
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.agents.explainability_review_agent.graph import PCBInspectionState
from app.config.settings import settings

_REQUEST_PAYLOAD = {
    "image_id": "",  # filled in per-test with a real uploaded id
    "board_id": "B1",
    "component_ref": "R131",
}


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.chat_upload_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_openai_key_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The route needs settings.openai_api_key configured - tests that care about the happy
    path set it explicitly; everyone else gets a clean empty default."""

    monkeypatch.setattr(settings, "openai_api_key", "")


def _upload(client: TestClient) -> str:
    response = client.post("/api/uploads", files={"file": ("board.png", _png_bytes(), "image/png")})
    assert response.status_code == 200
    result: str = response.json()["id"]
    return result


def _mock_pipeline_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePipeline:
        def invoke(self, initial_state: PCBInspectionState) -> PCBInspectionState:
            state = dict(initial_state)
            state["final_defect_category"] = "no defect"
            state["final_diagnosis_text"] = "Looks fine."
            state["defect_location"] = None
            state["grounding_confidence"] = 0.95
            state["self_check_passed"] = True
            return state  # type: ignore[return-value]

    monkeypatch.setattr(
        "app.agents.explainability_review_agent.tool.get_pipeline",
        lambda api_key: _FakePipeline(),
    )


def test_requires_login(client: TestClient) -> None:
    response = client.post(
        "/api/agents/explainability-review",
        json={**_REQUEST_PAYLOAD, "image_id": "does-not-exist.png"},
    )
    assert response.status_code == 401


def test_unknown_image_id_is_404(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    response = authenticated_client.post(
        "/api/agents/explainability-review",
        json={**_REQUEST_PAYLOAD, "image_id": "does-not-exist.png"},
    )
    assert response.status_code == 404


def test_returns_503_when_openai_not_configured(authenticated_client: TestClient) -> None:
    image_id = _upload(authenticated_client)

    response = authenticated_client.post(
        "/api/agents/explainability-review",
        json={**_REQUEST_PAYLOAD, "image_id": image_id},
    )
    assert response.status_code == 503


def test_disabled_agent_returns_503(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "explainability_agent_enabled", False)
    image_id = _upload(authenticated_client)

    response = authenticated_client.post(
        "/api/agents/explainability-review",
        json={**_REQUEST_PAYLOAD, "image_id": image_id},
    )
    assert response.status_code == 503


def test_happy_path(authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    _mock_pipeline_invoke(monkeypatch)
    image_id = _upload(authenticated_client)

    response = authenticated_client.post(
        "/api/agents/explainability-review",
        json={**_REQUEST_PAYLOAD, "image_id": image_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "defect_category": "no defect",
        "defect_location": None,
        "explanation": "Looks fine.",
        "confidence_score": 0.95,
        "self_check_passed": True,
        "errors": [],
    }
