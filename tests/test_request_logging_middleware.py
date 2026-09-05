import logging

import pytest
from fastapi.testclient import TestClient


def test_logs_method_path_status_and_duration(
    authenticated_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")

    response = authenticated_client.get("/api/conversations")
    assert response.status_code == 200

    records = [r for r in caplog.records if r.name == "app.access"]
    assert records, "expected app.access to log the request"
    record = records[-1]
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/api/conversations"  # type: ignore[attr-defined]
    assert record.status_code == 200  # type: ignore[attr-defined]
    assert isinstance(record.duration_ms, float)  # type: ignore[attr-defined]


def test_logs_user_id_when_authenticated(
    authenticated_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")

    authenticated_client.get("/api/auth/me")

    records = [r for r in caplog.records if r.name == "app.access"]
    assert records[-1].user_id is not None  # type: ignore[attr-defined]


def test_logs_no_user_id_when_unauthenticated(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")

    client.get("/health")

    records = [r for r in caplog.records if r.name == "app.access"]
    assert records[-1].user_id is None  # type: ignore[attr-defined]
