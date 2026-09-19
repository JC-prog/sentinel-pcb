"""Shared fixtures. DB-backed tests target the real local Postgres (`docker compose -f
infra/development/docker-compose.yml up -d db`) and skip cleanly if it's unreachable.
"""

from collections.abc import AsyncGenerator, Generator

import asyncpg  # type: ignore[import-untyped]
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.base import Base
from app.db.session import async_session_factory, engine


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


@pytest.fixture(autouse=True)
def _log_to_file_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps tests independent of whatever LOG_TO_FILE happens to be in a developer's own .env -
    without this, configure_logging() would add a second (file) handler and break assertions
    that count handlers. tests/test_logging_config.py re-enables it explicitly to test the file
    handler itself."""

    monkeypatch.setattr(settings, "log_to_file", False)


@pytest.fixture(autouse=True)
def _intent_router_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The intent router (app/agents/router_agent/) makes its own sync OpenAI call - via the
    `openai` SDK's own httpx.Client, not the httpx.AsyncClient chat tests mock - whenever
    settings.openai_api_key is set and a tool is on offer. Left enabled, that call would hit a
    real network endpoint in any test that configures an OpenAI key (e.g. to exercise the OpenAI
    chat provider), same risk _memory_disabled_by_default guards against. Disabled here by
    default; tests/agents/test_router_agent.py re-enables it explicitly."""

    monkeypatch.setattr(settings, "intent_router_enabled", False)


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
    except asyncpg.exceptions.InvalidAuthorizationSpecificationError as exc:
        # Distinct from "not reachable" above: Postgres is up and responding, it's just
        # rejecting these credentials - almost always a "db" volume that was initialized by an
        # earlier run with different values than the current .env/docker-compose.yml (setup-dev
        # scripts self-heal this on a fresh run, but do nothing for a volume that already went
        # stale under a developer who's just running pytest directly). SQLAlchemy doesn't wrap
        # this as OperationalError, so it needs its own branch - left uncaught, this fails every
        # single DB-backed test individually with the same cryptic traceback instead of stopping
        # the whole run once with the actual fix.
        pytest.exit(
            f"Postgres rejected settings.database_url's credentials ({exc!r}) - its \"db\" "
            "volume most likely predates the current credentials. Reset it:\n"
            "    docker compose -f infra/development/docker-compose.yml --env-file .env down -v\n"
            "    docker compose -f infra/development/docker-compose.yml --env-file .env "
            "up -d --wait db qdrant litellm",
            returncode=1,
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


@pytest_asyncio.fixture
async def db_async_session(db_session: None) -> AsyncGenerator[AsyncSession, None]:
    """A real AsyncSession against the same schema/truncation lifecycle as `client` - for tests
    that call repository/agent functions directly (app/agents/adc_inspection_agent/) rather than
    through the HTTP API, since those take a session as a parameter."""

    async with async_session_factory() as session:
        yield session


_REGISTER_PAYLOAD = {
    "username": "test-qa",
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
    "username": "other-qa",
    "email": "other-qa@example.com",
    "password": "correct-horse-battery-staple",
    "employee_id": "EMP-002",
    "department_shift": "QA Night Shift",
    "role": "qa",
}


@pytest.fixture
def other_authenticated_client(db_session: None) -> Generator[TestClient, None, None]:
    """A second authenticated user with its own TestClient/cookie jar, for cross-user isolation
    tests (chat/conversation scoping) - a distinct instance from `client`/`authenticated_client`
    so the two sessions don't share cookies.

    Registers with role "qa", but that only sticks if a user already exists in this test's DB -
    app/auth/service.py auto-promotes the *first* registered user to ADMIN regardless of requested
    role. Request `authenticated_client` in the same test (it doesn't need to be used) to
    guarantee this one lands second and keeps its requested role."""

    from app.main import app

    with TestClient(app) as test_client:
        response = test_client.post("/api/auth/register", json=_OTHER_REGISTER_PAYLOAD)
        assert response.status_code == 201, response.text
        yield test_client


_QA_REGISTER_PAYLOAD = {
    "username": "second-qa",
    "email": "second-qa@example.com",
    "password": "correct-horse-battery-staple",
    "employee_id": "EMP-003",
    "department_shift": "QA Day Shift",
    "role": "qa",
}


@pytest.fixture
def qa_authenticated_client(
    authenticated_client: TestClient,
) -> Generator[TestClient, None, None]:
    """A QA-role user, registered second (via the `authenticated_client` dependency, which
    consumes the "first user becomes admin" slot - see other_authenticated_client's docstring) so
    it actually keeps the "qa" role it requests."""

    from app.main import app

    with TestClient(app) as test_client:
        response = test_client.post("/api/auth/register", json=_QA_REGISTER_PAYLOAD)
        assert response.status_code == 201, response.text
        yield test_client
