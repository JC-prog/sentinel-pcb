"""Async engine/session setup.

Schema changes now go through Alembic (alembic/ - `uv run alembic revision --autogenerate` /
`upgrade head`), not this module. `init_models()`'s `create_all` still runs at app startup and in
tests for convenience (it's idempotent and a no-op once Alembic has created the tables), but a
real deploy's schema is Alembic's migration history, not this function - wiring `alembic upgrade
head` into an actual deploy step is a separate, later task.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.db.base import Base

# create_async_engine() only parses the URL - it doesn't connect - so an unreachable Postgres is
# fine (tests/conftest.py's db_session fixture catches that at connection time and skips
# cleanly). An empty or malformed URL is a different failure mode though: it raises immediately,
# at import time, before any fixture gets a chance to run - which would take down every test in
# the suite, not just the DB-backed ones. Falling back to a syntactically-valid-but-unreachable
# URL keeps that failure where it belongs: a connection-time skip, not an import-time crash.
_database_url = settings.database_url or "postgresql+asyncpg://unconfigured/unconfigured"

# NullPool: pytest-asyncio gives each test its own event loop, and a pooled asyncpg connection
# checked out in one loop can't be reused (or even closed cleanly) in another - "Future attached
# to a different loop" errors. NullPool opens a fresh physical connection per checkout instead of
# reusing one across calls, which sidesteps that entirely. Fine for this project's scale.
engine = create_async_engine(_database_url, poolclass=NullPool)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency - one session per request."""

    async with async_session_factory() as session:
        yield session


async def init_models() -> None:
    """Create tables if they don't exist yet. Called once from `app/main.py`'s lifespan."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
