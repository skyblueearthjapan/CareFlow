"""Audit-middleware resilience tests (Wave 4-G).

Production incident: an out-of-date ``audit_logs`` schema raised
``sqlalchemy.exc.ProgrammingError`` from every mutation's background persist
task and degraded the API. The fix routes ``ProgrammingError`` through a
dedicated WARN log path (with operator-targeted guidance) while preserving
the broad fallback. These tests pin both behaviours so a future refactor
cannot regress the contract: **the response is always returned untouched**.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.exc import ProgrammingError

from app.middleware import audit as audit_module
from app.middleware.audit import _persist_async


@pytest.mark.asyncio
async def test_persist_swallows_programming_error_with_schema_hint(caplog) -> None:
    """ProgrammingError → WARN with the migration hint, never raised."""
    async def _boom(*_a, **_kw):
        raise ProgrammingError("INSERT", {}, Exception("undefined column"))

    with patch.object(audit_module, "get_session_factory", side_effect=_boom):
        with caplog.at_level("WARNING", logger="app.middleware.audit"):
            # Must NOT raise — caller is fire-and-forget.
            await _persist_async({"action": "POST"})

    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "DB schema mismatch" in joined
    assert "alembic upgrade head" in joined


@pytest.mark.asyncio
async def test_persist_swallows_unexpected_errors(caplog) -> None:
    """Anything else still falls through the broad except — never raised."""
    async def _boom(*_a, **_kw):
        raise RuntimeError("kapow")

    with patch.object(audit_module, "get_session_factory", side_effect=_boom):
        with caplog.at_level("ERROR", logger="app.middleware.audit"):
            await _persist_async({"action": "POST"})

    assert any(
        "persist failed" in rec.getMessage() for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_request_response_unaffected_by_audit_persist_failure(client, db) -> None:
    """End-to-end: even when the persist task explodes, the client still sees 200."""
    from app.core.security import create_access_token, hash_password
    from app.models import User

    admin = User(
        email="audit-resilience-admin@example.com",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    headers = {
        "Authorization": (
            f"Bearer {create_access_token(subject=admin.id, role=admin.role, staff_id=admin.staff_id)}"
        )
    }

    async def _boom(*_a, **_kw):
        raise ProgrammingError("INSERT", {}, Exception("undefined column"))

    with patch.object(audit_module, "get_session_factory", side_effect=_boom):
        res = await client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={"email": "resilience-target@example.com", "role": "staff"},
        )
    # Audit failure must not leak into the user-visible response.
    assert res.status_code == 201, res.text
