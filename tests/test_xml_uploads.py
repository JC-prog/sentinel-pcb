import shutil
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings

_XML_BYTES = b"<Boards><Board Name='BOARD-1'></Board></Boards>"


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.chat_upload_dir, ignore_errors=True)


def test_upload_xml_requires_login(client: TestClient) -> None:
    response = client.post(
        "/api/uploads/xml", files={"file": ("inspection.xml", _XML_BYTES, "application/xml")}
    )
    assert response.status_code == 401


def test_upload_xml_accepts_xml_extension(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/uploads/xml", files={"file": ("inspection.xml", _XML_BYTES, "application/xml")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["url"] == f"/api/uploads/{body['id']}"


def test_upload_xml_rejects_non_xml_files(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/uploads/xml", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert response.status_code == 422
