import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.settings import settings

MODEL_PATH = Path(settings.adc_model_path)


def _png_bytes(width: int = 256, height: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _wait_for_terminal(client: TestClient, workflow_id: str, timeout: float = 5.0) -> dict[str, object]:
    """`continue_workflow` runs as a BackgroundTask - TestClient's ASGI transport drives it to
    completion before `.post()`/`.get()` returns, but this polls defensively rather than
    assuming that timing.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body: dict[str, object] = client.get(f"/workflows/{workflow_id}").json()
        if body["status"] in ("COMPLETED", "HUMAN_REVIEW", "REJECTED_QUALITY"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"workflow {workflow_id} did not reach a terminal state in {timeout}s")


def test_submit_workflow_rejects_bad_image(client: TestClient) -> None:
    response = client.post(
        "/workflows",
        files={"image": ("garbage.png", b"not an image", "image/png")},
    )
    assert response.status_code == 202
    workflow_id = response.json()["workflow_id"]

    detail = _wait_for_terminal(client, workflow_id)
    assert detail["status"] == "REJECTED_QUALITY"
    assert detail["decision"] == "rejected_quality"


@pytest.mark.skipif(
    not MODEL_PATH.exists(),
    reason="ADC model not present - see docs/ADC-Design-Doc-Team10-V2.md §8",
)
def test_submit_workflow_with_board_info_reaches_a_decision(client: TestClient) -> None:
    response = client.post(
        "/workflows",
        files={"image": ("board.png", _png_bytes(), "image/png")},
        data={"board_id": "MB-2024-REV3", "component_id": "R47", "recipe_id": "RCP-1"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["board_id"] == "MB-2024-REV3"

    detail = _wait_for_terminal(client, body["workflow_id"])
    assert detail["decision"] in ("auto_accept", "escalate_to_human")
    assert detail["status"] in ("COMPLETED", "HUMAN_REVIEW")


def test_list_workflows_filters_by_terminal(client: TestClient) -> None:
    response = client.post(
        "/workflows", files={"image": ("garbage.png", b"not an image", "image/png")}
    )
    workflow_id = response.json()["workflow_id"]
    _wait_for_terminal(client, workflow_id)

    terminal_ids = [w["workflow_id"] for w in client.get("/workflows?terminal=true").json()]
    active_ids = [w["workflow_id"] for w in client.get("/workflows?terminal=false").json()]

    assert workflow_id in terminal_ids
    assert workflow_id not in active_ids


def test_get_workflow_image_roundtrip(client: TestClient) -> None:
    response = client.post("/workflows", files={"image": ("board.png", _png_bytes(), "image/png")})
    workflow_id = response.json()["workflow_id"]

    image_response = client.get(f"/workflows/{workflow_id}/image")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"


def test_get_unknown_workflow_is_404(client: TestClient) -> None:
    assert client.get("/workflows/does-not-exist").status_code == 404
    assert client.get("/workflows/does-not-exist/trace").status_code == 404
