import io
import shutil
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.settings import settings


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.chat_upload_dir, ignore_errors=True)


def test_upload_requires_login(client: TestClient) -> None:
    response = client.post(
        "/api/uploads", files={"file": ("board.png", _png_bytes(), "image/png")}
    )
    assert response.status_code == 401


def test_upload_and_fetch_image_roundtrip(authenticated_client: TestClient) -> None:
    png = _png_bytes()
    upload = authenticated_client.post(
        "/api/uploads", files={"file": ("board.png", png, "image/png")}
    )
    assert upload.status_code == 200
    body = upload.json()
    assert body["id"]
    assert body["url"] == f"/api/uploads/{body['id']}"

    fetched = authenticated_client.get(body["url"])
    assert fetched.status_code == 200
    assert fetched.content == png


def test_upload_rejects_non_image_files(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/uploads", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 422


def test_get_upload_unknown_filename_is_404(authenticated_client: TestClient) -> None:
    assert authenticated_client.get("/api/uploads/does-not-exist.png").status_code == 404


def test_get_upload_rejects_path_traversal(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/uploads/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404
