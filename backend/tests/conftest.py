"""Pytest fixtures: in-memory SQLite engine + ASGI httpx client.

The fixtures reset the global engine/session-factory in `app.db.session` so
each test session uses an isolated `sqlite+aiosqlite:///:memory:` database.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

# IMPORTANT (W41 v2.5 / 2026-05-17 事故再発防止):
# 本番 DATABASE_URL が container env に存在しても、tests では絶対に sqlite を強制する。
# setdefault では本番 URL が優先され、Base.metadata.drop_all が本番 DB を破壊する事故が
# 過去 2 回発生した。強制代入で上書きする。
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET"] = "test-secret-key-which-is-32-chars-long!"
os.environ["APP_ENV"] = "test"
os.environ["CORS_ORIGINS"] = "http://localhost:3000"

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
    # SAFETY (W41 v2.5 / 2026-05-17): 本番事故防止ガード。
    # APP_ENV != "test" の場合は create_all / drop_all を絶対に実行しない。
    # これにより、conftest が想定外に本番 container で動いても DB 破壊を防ぐ。
    # (過去 2 回の本番 DB 全消失事故に基づく二重安全装置)
    settings = get_settings()
    if settings.app_env != "test":
        raise RuntimeError(
            f"Refusing to create test engine: APP_ENV={settings.app_env!r} != 'test'. "
            "This is a production safety check (2 prior DB-loss incidents). "
            "Do NOT run pytest inside the production container."
        )

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
async def test_user(db) -> User:
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
