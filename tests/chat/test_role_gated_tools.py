"""Role -> tool-visibility matrix (app/chat/agents/access.py), exercised through /api/chat/stream, plus
a check that a tool the model was never given cannot be run even if the model asks for it.

Every fixture here registers a *second* user in the test's DB - app/shared/auth/service.py auto-promotes
the first registered user in an empty DB to ADMIN regardless of requested role (see
tests/conftest.py's qa_authenticated_client docstring), so `authenticated_client` itself is used
as the ADMIN case rather than a dedicated fixture.
"""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from tests.chat._llm import ScriptedModel, ai, install, says


def _stream(client: TestClient, message: str, image_ids: list[str] | None = None) -> str:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": message, "image_ids": image_ids or []},
    ) as response:
        return "".join(response.iter_text())


def _offered_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, image_attached: bool
) -> set[str]:
    model = says("ack")
    install(monkeypatch, model)
    _stream(client, "check this board", image_ids=["some-upload-id"] if image_attached else None)
    return set(model.offered)


def test_qa_sees_inspect_image_and_model_health_tools_when_an_image_is_attached(
    qa_authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered = _offered_tools(qa_authenticated_client, monkeypatch, image_attached=True)
    assert offered == {
        "inspect_image",
        "relabel_case",
        "confirm_relabel",
        "review_case",
        "confirm_review",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
        "create_case",
        "get_sample",
        "list_review_cases",
    }


def test_qa_sees_only_model_health_tools_without_an_image_attached(
    qa_authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model-health tools work from a case number (or the conversation's latest case) -
    unlike inspect_image, they need no image attached to this message."""

    offered = _offered_tools(qa_authenticated_client, monkeypatch, image_attached=False)
    assert offered == {
        "relabel_case",
        "confirm_relabel",
        "review_case",
        "confirm_review",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
        "create_case",
        "get_sample",
        "list_review_cases",
    }


def test_admin_sees_every_tool(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    offered = _offered_tools(authenticated_client, monkeypatch, image_attached=True)
    assert offered == {
        "inspect_image",
        "relabel_case",
        "confirm_relabel",
        "review_case",
        "confirm_review",
        "report_model_drift",
        "get_drift_summary",
        "draft_retraining_plan",
        "monitoring_status",
        "create_case",
        "get_sample",
        "list_review_cases",
    }


def test_a_tool_the_model_was_never_given_cannot_be_run(
    qa_authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model only has the tools its request may use, so a call naming anything else is not
    executed - LangGraph answers it with an error. monitoring_status is the one tool QA never
    sees (Admin-only); a prompt-injected call for it must do nothing."""

    model = ScriptedModel(replies=[ai("", "monitoring_status"), ai("done")])
    install(monkeypatch, model)

    _stream(qa_authenticated_client, "what's the model status")

    [result] = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
    assert result.status == "error"
    assert "monitoring_status" in str(result.content)
    assert "live_models" not in str(result.content)  # the overview was never produced
