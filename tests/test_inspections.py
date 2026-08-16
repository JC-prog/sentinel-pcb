import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.settings import settings

MODEL_PATH = Path(settings.adc_model_path)


def _png_bytes(width: int = 256, height: int = 256) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_create_inspection_rejects_bad_image() -> None:
    client = TestClient(app)
    response = client.post(
        "/inspections",
        files={"image": ("garbage.png", b"not an image", "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "rejected_quality"


@pytest.mark.skipif(
    not MODEL_PATH.exists(),
    reason="ADC model not present - see docs/ADC-Design-Doc-Team10-V2.md §8",
)
def test_create_inspection_accepts_metadata() -> None:
    client = TestClient(app)
    response = client.post(
        "/inspections",
        files={"image": ("board.png", _png_bytes(), "image/png")},
        data={"metadata": '{"line": "SMT-3"}'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"]
    assert body["decision"] in ("auto_accept", "escalate_to_human", "rejected_quality")
