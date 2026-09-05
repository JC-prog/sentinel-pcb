"""Async engine/session setup. No Alembic for this pass - a single table, no existing data to
migrate, and no second environment yet, so `create_all` on startup is the right amount of
ceremony until that stops being true.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.settings import settings

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
