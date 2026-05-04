"""Pytest fixtures: in-memory SQLite engine + ASGI httpx client.

The fixtures reset the global engine/session-factory in `app.db.session` so
each test session uses an isolated `sqlite+aiosqlite:///:memory:` database.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

# Force test settings BEFORE importing app modules so pydantic-settings picks
# them up at first construction.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-which-is-32-chars-long!")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models import User  # noqa: E402,F401  (registers metadata)


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Provide a session-scoped event loop for async fixtures."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def _engine():
    """Build a fresh in-memory SQLite engine + create all tables."""
    # Refresh the cached settings so DATABASE_URL env var takes effect.
    get_settings.cache_clear()
    db_session.reset_engine()

    engine = db_session.get_engine()
    async with engine.begin() as conn:
        # SQLite ignores PG-specific JSONB / UUID types via SQLAlchemy fallback.
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db_session.dispose_engine()


@pytest_asyncio.fixture(scope="function")
async def db(_engine) -> AsyncIterator:
    """Yield an async DB session."""
    factory = db_session.get_session_factory()
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(_engine):
    """Yield an httpx AsyncClient bound to the FastAPI app via ASGITransport."""
    from httpx import ASGITransport, AsyncClient

    from app.core.rate_limit import limiter
    from app.main import create_app

    # Reset slowapi state per test so per-IP windows do not bleed across cases.
    limiter.reset()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture(scope="function")
async def test_user(db) -> "User":
    """Insert a baseline user (admin/secret-pass-01) and return it."""
    from app.models import User

    user = User(
        email="admin@example.com",
        password_hash=hash_password("secret-pass-01"),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
