import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    frames = [f for f in body.split("\n\n") if f.strip()]
    parsed = []
    for frame in frames:
        event = "message"
        data = "{}"
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        parsed.append((event, json.loads(data)))
    return parsed


def test_chat_stream_rejects_empty_message() -> None:
    response = client.post(
        "/api/chat/stream", json={"conversation_id": "c1", "message": "", "image_ids": []}
    )
    assert response.status_code == 422


def test_chat_stream_yields_deltas_then_done() -> None:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "hello there", "image_ids": []},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    frames = _parse_sse(body)
    assert frames[-1] == ("done", {})

    deltas = [str(data["text"]) for event, data in frames if event == "delta"]
    assert "".join(deltas) == "This is a placeholder response. You said: hello there"


def test_chat_stream_notes_attached_images() -> None:
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"conversation_id": "c1", "message": "see attached", "image_ids": ["abc123"]},
    ) as response:
        body = "".join(response.iter_text())

    deltas = "".join(str(data["text"]) for event, data in _parse_sse(body) if event == "delta")
    assert "(with 1 image(s) attached)" in deltas
