"""GET /api/orchestrator/status: whether the Work tab's LLM planner has a key to use - the
orchestrator's own override, else the app's OPENAI_API_KEY."""

import pytest
from fastapi.testclient import TestClient

from app.shared.config.settings import settings

STATUS_URL = "/api/orchestrator/status"


@pytest.mark.parametrize(
    ("override", "shared", "expected"),
    [
        ("", "", False),
        ("", "sk-app", True),
        ("sk-work", "", True),
        ("sk-work", "sk-app", True),
    ],
)
def test_status_says_whether_a_planner_key_is_available(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    override: str,
    shared: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(settings, "orchestrator_openai_api_key", override)
    monkeypatch.setattr(settings, "openai_api_key", shared)

    response = authenticated_client.get(STATUS_URL)

    assert response.status_code == 200, response.text
    assert response.json() == {"llm_configured": expected}


def test_the_orchestrator_key_prefers_its_own_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-app")
    monkeypatch.setattr(settings, "orchestrator_openai_api_key", "")
    assert settings.orchestrator_api_key == "sk-app"

    monkeypatch.setattr(settings, "orchestrator_openai_api_key", "sk-work")
    assert settings.orchestrator_api_key == "sk-work"
