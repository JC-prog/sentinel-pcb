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


_REGISTER_PAYLOAD = {
    "username": "body-test",
    "email": "body-test@example.com",
    "password": "correct-horse-battery-staple",
    "employee_id": "EMP-BODY",
    "department_shift": "QA Day Shift",
    "role": "qa",
}


def test_debug_logs_request_body_with_password_redacted(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.access")

    client.post("/api/auth/register", json=_REGISTER_PAYLOAD)

    body_records = [r for r in caplog.records if hasattr(r, "request_body")]
    assert body_records, "expected a debug body-log record"
    request_body = body_records[-1].request_body
    assert request_body["username"] == "body-test"
    assert request_body["password"] == "***"


def test_debug_logs_response_body_for_non_streaming_responses(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.access")

    client.post("/api/auth/register", json=_REGISTER_PAYLOAD)

    body_records = [r for r in caplog.records if hasattr(r, "response_body")]
    assert body_records
    response_body = body_records[-1].response_body
    assert response_body["username"] == "body-test"


def test_no_body_logged_at_default_info_level(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.access")

    client.post("/api/auth/register", json=_REGISTER_PAYLOAD)

    body_records = [r for r in caplog.records if hasattr(r, "request_body")]
    assert body_records == []
