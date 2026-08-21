"""Shared fixtures. First conftest.py in this repo.

DB-backed tests target the real local Postgres (`docker compose up db`) and skip cleanly if it's
unreachable, mirroring this repo's existing convention of skipping model-dependent tests (see
`pytest.mark.skipif` on `settings.adc_model_path`) rather than mocking infrastructure.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import RemediationDoc
from app.db.session import async_session_factory, engine

# Reference/seed data (scripts/seed_remediation_docs.py), not per-test scratch data - truncating
# it after every test would wipe out the RAG corpus for anything running against this same
# Postgres instance outside the test run (the live app, manual verification).
_PRESERVE_TABLES = {RemediationDoc.__tablename__}


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    try:
        async with engine.begin() as conn:
            # vector extension must exist before Workflow.embedding/RemediationDoc.embedding's
            # Vector columns can be created - mirrors app.db.session.init_models().
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
    except (OperationalError, OSError) as exc:
        pytest.skip(
            f"Postgres not reachable at settings.database_url ({exc!r}) - "
            "start it via `docker compose up db`"
        )

    async with async_session_factory() as session:
        yield session

    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name not in _PRESERVE_TABLES:
                await conn.execute(table.delete())


@pytest.fixture
def client(db_session: AsyncSession) -> Generator[TestClient, None, None]:
    """A TestClient against a Postgres schema that's guaranteed to exist (via db_session) and
    gets truncated after the test. Tests that don't touch the DB should just use
    `fastapi.testclient.TestClient(app)` directly instead of this fixture.
    """

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
