from typing import cast

from fastapi.testclient import TestClient
from httpx import Response

_QA_PAYLOAD = {
    "username": "first-user",
    "email": "first@example.com",
    "password": "correct-horse-battery-staple",
    "employee_id": "EMP-001",
    "department_shift": "QA Day Shift",
    "role": "qa",
}


def _register(client: TestClient, **overrides: object) -> Response:
    payload = {**_QA_PAYLOAD, **overrides}
    return cast(Response, client.post("/api/auth/register", json=payload))


def test_first_user_becomes_admin_regardless_of_requested_role(client: TestClient) -> None:
    response = _register(client, role="qa")
    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_second_user_gets_the_role_they_requested(client: TestClient) -> None:
    _register(
        client,
        username="admin-bootstrap",
        email="admin-bootstrap@example.com",
        employee_id="EMP-000",
    )

    response = _register(
        client,
        username="second-user",
        email="second@example.com",
        employee_id="EMP-002",
        role="operator",
    )
    assert response.status_code == 201
    assert response.json()["role"] == "operator"


def test_register_allows_admin_role(client: TestClient) -> None:
    response = _register(client, role="admin")
    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_register_rejects_duplicate_username(client: TestClient) -> None:
    _register(client)
    response = _register(client, email="someone-else@example.com", employee_id="EMP-002")
    assert response.status_code == 409


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    _register(client)
    response = _register(client, username="someone-else", employee_id="EMP-002")
    assert response.status_code == 409


def test_register_rejects_duplicate_employee_id(client: TestClient) -> None:
    _register(client)
    response = _register(client, username="someone-else", email="someone-else@example.com")
    assert response.status_code == 409


def test_register_sets_auth_cookies(client: TestClient) -> None:
    response = _register(client)
    assert "access_token" in response.cookies
    assert "refresh_token" in response.cookies


def test_login_with_correct_credentials(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/auth/login",
        json={"username": _QA_PAYLOAD["username"], "password": _QA_PAYLOAD["password"]},
    )
    assert response.status_code == 200
    assert response.json()["username"] == _QA_PAYLOAD["username"]


def test_login_with_incorrect_password(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/auth/login", json={"username": _QA_PAYLOAD["username"], "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_with_unknown_username(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert response.status_code == 401


def test_me_reflects_the_session_identity(client: TestClient) -> None:
    _register(client)
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == _QA_PAYLOAD["email"]


def test_me_without_a_session_is_401(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_the_refresh_token(client: TestClient) -> None:
    _register(client)
    old_refresh_token = client.cookies.get("refresh_token")
    assert old_refresh_token is not None
    assert client.post("/api/auth/logout").status_code == 204

    # logout cleared the cookie from the client - put the (now revoked) token back to confirm
    # the server itself rejects it, not just that the client forgot it.
    client.cookies.set("refresh_token", old_refresh_token)
    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_clears_cookies(client: TestClient) -> None:
    _register(client)
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_rotates_the_token_and_old_one_cannot_be_reused(client: TestClient) -> None:
    _register(client)
    old_refresh_token = client.cookies.get("refresh_token")
    assert old_refresh_token is not None

    first_refresh = client.post("/api/auth/refresh")
    assert first_refresh.status_code == 200
    new_refresh_token = client.cookies.get("refresh_token")
    assert new_refresh_token != old_refresh_token

    client.cookies.set("refresh_token", old_refresh_token)
    reuse_attempt = client.post("/api/auth/refresh")
    assert reuse_attempt.status_code == 401


def test_refresh_without_a_cookie_is_401(client: TestClient) -> None:
    assert client.post("/api/auth/refresh").status_code == 401
