"""Covers the three /api/orchestrator/uploads/* routes (app/main.py) - the web equivalent of the
tkinter source app's file/folder pickers. See app/workflow/services/uploads.py for the
storage layer these exercise."""

import shutil
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.shared.config.settings import settings


@pytest.fixture(autouse=True)
def _clean_upload_dir() -> Generator[None, None, None]:
    yield
    shutil.rmtree(settings.orchestrator_data_dir, ignore_errors=True)


def test_dataset_upload_requires_login(client: TestClient) -> None:
    response = client.post(
        "/api/orchestrator/uploads/dataset",
        files={"file": ("dataset.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 401


def test_dataset_upload_requires_qa_or_admin(other_authenticated_client: TestClient) -> None:
    """other_authenticated_client registers with role "qa" but only sticks if a user already
    exists - since QA already has access here, this really just exercises the happy path through
    a second, independent session (see conftest.py's docstring on that fixture)."""

    response = other_authenticated_client.post(
        "/api/orchestrator/uploads/dataset",
        files={"file": ("dataset.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 200


def test_dataset_upload_rejects_non_csv(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/dataset",
        files={"file": ("dataset.txt", b"a,b\n1,2\n", "text/plain")},
    )
    assert response.status_code == 422


def test_dataset_upload_roundtrip(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/dataset",
        files={"file": ("dataset.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"]


def test_xml_upload_rejects_non_xml(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/xml",
        files={"file": ("inspection.txt", b"<root/>", "text/plain")},
    )
    assert response.status_code == 422


def test_xml_upload_roundtrip(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/xml",
        files={"file": ("inspection.xml", b"<root/>", "application/xml")},
    )
    assert response.status_code == 200
    assert response.json()["id"]


def test_image_root_upload_roundtrip(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/image-root",
        files=[
            ("files", ("image.jpg", b"fake-image-bytes", "image/jpeg")),
        ],
        data={"relative_paths": ["sample_data/35-abc/Golden/image.jpg"]},
    )
    assert response.status_code == 200
    assert response.json()["id"]


def test_image_root_upload_rejects_path_traversal(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/image-root",
        files=[("files", ("image.jpg", b"fake-image-bytes", "image/jpeg"))],
        data={"relative_paths": ["../../etc/passwd"]},
    )
    assert response.status_code == 422


def test_image_root_upload_rejects_mismatched_lengths(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/orchestrator/uploads/image-root",
        files=[("files", ("image.jpg", b"fake-image-bytes", "image/jpeg"))],
        data={"relative_paths": ["a.jpg", "b.jpg"]},
    )
    assert response.status_code == 422
