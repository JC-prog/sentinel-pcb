import io
import shutil
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.settings import settings

client = TestClient(app)


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.chat_upload_dir, ignore_errors=True)


def test_upload_and_fetch_image_roundtrip() -> None:
    png = _png_bytes()
    upload = client.post(
        "/api/uploads", files={"file": ("board.png", png, "image/png")}
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["id"]
    assert body["url"] == f"/api/uploads/{body['id']}"

    fetched = client.get(body["url"])
    assert fetched.status_code == 200
    assert fetched.content == png


def test_upload_rejects_non_image_files() -> None:
    response = client.post(
        "/api/uploads", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 422


def test_get_upload_unknown_filename_is_404() -> None:
    assert client.get("/api/uploads/does-not-exist.png").status_code == 404


def test_get_upload_rejects_path_traversal() -> None:
    response = client.get("/api/uploads/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404
