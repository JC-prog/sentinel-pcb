import pytest
from fastapi.testclient import TestClient

from tests.chat._llm import install, says


def _send_message(client: TestClient, conversation_id: str, message: str, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, says("ack"))
    with client.stream(
        "POST", "/api/chat/stream", json={"conversation_id": conversation_id, "message": message, "image_ids": []}
    ) as response:
        response.read()
        status_code = response.status_code
    assert status_code == 200


def test_list_conversations_requires_login(client: TestClient) -> None:
    assert client.get("/api/conversations").status_code == 401


def test_list_conversations_is_empty_for_a_new_user(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/conversations")
    assert response.status_code == 200
    assert response.json() == []


def test_conversation_appears_after_first_message_with_a_derived_title(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _send_message(authenticated_client, "c1", "What's the tolerance on a 0402 resistor?", monkeypatch)

    listing = authenticated_client.get("/api/conversations")
    assert listing.status_code == 200
    [summary] = listing.json()
    assert summary["id"] == "c1"
    assert summary["title"] == "What's the tolerance on a 0402 resistor?"


def test_get_conversation_unknown_id_is_404(authenticated_client: TestClient) -> None:
    assert authenticated_client.get("/api/conversations/does-not-exist").status_code == 404


def test_get_conversation_belonging_to_another_user_is_404(
    authenticated_client: TestClient, other_authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _send_message(authenticated_client, "c1", "private", monkeypatch)
    assert other_authenticated_client.get("/api/conversations/c1").status_code == 404


def test_delete_conversation_removes_it(
    authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _send_message(authenticated_client, "c1", "hi", monkeypatch)
    assert authenticated_client.get("/api/conversations/c1").status_code == 200

    assert authenticated_client.delete("/api/conversations/c1").status_code == 204
    assert authenticated_client.get("/api/conversations/c1").status_code == 404
    assert authenticated_client.get("/api/conversations").json() == []


def test_delete_conversation_belonging_to_another_user_is_404(
    authenticated_client: TestClient, other_authenticated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _send_message(authenticated_client, "c1", "private", monkeypatch)

    response = other_authenticated_client.delete("/api/conversations/c1")
    assert response.status_code == 404
    # not actually deleted - still visible to its real owner.
    assert authenticated_client.get("/api/conversations/c1").status_code == 200


def test_delete_conversation_unknown_id_is_404(authenticated_client: TestClient) -> None:
    assert authenticated_client.delete("/api/conversations/does-not-exist").status_code == 404
