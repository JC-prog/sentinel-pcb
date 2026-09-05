"""Shared fixtures. DB-backed tests target the real local Postgres (`docker compose -f
infra/development/docker-compose.yml up -d db`) and skip cleanly if it's unreachable.
"""

from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config.settings import settings
from app.db.base import Base
from app.db.session import engine


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real token issuance needs a real secret - every test gets a consistent one, whether or
    not it touches auth directly (authenticated_client, used by chat/upload tests, needs this
    too)."""

    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-key-for-tests-only-32-bytes+")


@pytest.fixture(autouse=True)
def _memory_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Long-term memory (app/memory/) makes its own embedding/LLM calls through the same
    httpx.AsyncClient chat tests mock - left enabled, those calls would interleave with (and
    break assertions on) the mocked chat-provider requests most tests actually care about.
    Disabled here by default; tests/test_memory.py re-enables it explicitly."""

    monkeypatch.setattr(settings, "memory_enabled", False)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[None, None]:
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except (OperationalError, OSError) as exc:
        pytest.skip(
            f"Postgres not reachable at settings.database_url ({exc!r}) - start it via "
            "`docker compose -f infra/development/docker-compose.yml up -d db`"
        )

    yield

    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture
def client(db_session: None) -> Generator[TestClient, None, None]:
    """A TestClient against a Postgres schema that's guaranteed to exist (via db_session) and
    gets truncated after the test. Tests that don't touch the DB/auth can still use this - it's
    the default now that every route requires a session.
    """

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


_REGISTER_PAYLOAD = {
    "name": "Test QA",
    "email": "qa@example.com",
    "password": "correct-horse-battery-staple",
    "employee_id": "EMP-001",
    "department_shift": "QA Day Shift",
    "role": "qa",
}


@pytest.fixture
def authenticated_client(client: TestClient) -> TestClient:
    """Registers and logs in a QA user, returning the same client - its cookie jar now carries a
    valid session, so subsequent requests hit protected routes as that user."""

    response = client.post("/api/auth/register", json=_REGISTER_PAYLOAD)
    assert response.status_code == 201, response.text
    return client


_OTHER_REGISTER_PAYLOAD = {
    "name": "Other Operator",
    "email": "operator@example.com",
    "password": "correct-horse-battery-staple",
    "employee_id": "EMP-002",
    "department_shift": "Operator Day Shift",
    "role": "operator",
}


@pytest.fixture
def other_authenticated_client(db_session: None) -> Generator[TestClient, None, None]:
    """A second authenticated user with its own TestClient/cookie jar, for cross-user isolation
    tests (chat/conversation scoping) - a distinct instance from `client`/`authenticated_client`
    so the two sessions don't share cookies."""

    from app.main import app

    with TestClient(app) as test_client:
        response = test_client.post("/api/auth/register", json=_OTHER_REGISTER_PAYLOAD)
        assert response.status_code == 201, response.text
        yield test_client
