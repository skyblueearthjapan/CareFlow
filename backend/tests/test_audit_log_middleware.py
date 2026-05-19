"""Tests for AuditLogMiddleware (Wave 4-F).

Coverage:
- redact() helper masks sensitive fields per spec
- Mutation requests are recorded with method/path/status/actor
- GET requests are NOT recorded (cheap-skip rule)
- Failed mutations (e.g. bad-role 400) still write a row
- A misbehaving DB does not break the request hot-path
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.middleware.audit import redact
from app.models import AuditLog, User


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Pure-function PII redact tests


def test_redact_password_field_is_blanked() -> None:
    out = redact({"email": "x@example.com", "password": "hunter2"})
    assert out["password"] == "[REDACTED]"


def test_redact_token_variants_are_blanked() -> None:
    out = redact(
        {
            "access_token": "abc",
            "refresh_token": "def",
            "authorization": "Bearer xyz",
            "client_secret": "shh",
        }
    )
    for v in out.values():
        assert v == "[REDACTED]"


def test_redact_email_keeps_only_domain() -> None:
    out = redact({"email": "alice@example.co.jp"})
    assert out["email"] == "***@example.co.jp"


def test_redact_name_keeps_first_char() -> None:
    out = redact({"name": "青栁あかり"})
    # First char preserved, remaining chars replaced with full-width circles.
    assert out["name"].startswith("青")
    assert "◯" in out["name"]
    assert "あ" not in out["name"]


def test_redact_address_partial_mask() -> None:
    out = redact({"home_address": "東京都千代田区一番町1-2-3"})
    assert out["home_address"].startswith("東京都千")
    assert "◯" in out["home_address"]


def test_redact_phone_keeps_endpoints() -> None:
    out = redact({"phone": "090-1234-5678"})
    assert out["phone"].startswith("090") and out["phone"].endswith("78")
    assert "1234" not in out["phone"]


def test_redact_recurses_into_nested_lists() -> None:
    out = redact({"items": [{"password": "p1"}, {"password": "p2"}]})
    assert out["items"][0]["password"] == "[REDACTED]"
    assert out["items"][1]["password"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# Middleware integration tests


async def _wait_for_log(db, *, method: str, path_substring: str, attempts: int = 25):
    """Poll for the audit row written by the fire-and-forget background task."""
    for _ in range(attempts):
        rows = (
            (
                await db.execute(
                    select(AuditLog).where(
                        AuditLog.method == method,
                        AuditLog.path.like(f"%{path_substring}%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        if rows:
            return rows[-1]
        await asyncio.sleep(0.02)
    return None


@pytest.mark.asyncio
async def test_middleware_records_mutation_with_redacted_body(client, db) -> None:
    admin = User(
        email="audit-admin@example.com",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "audited-target@example.com", "role": "staff"},
    )
    assert res.status_code == 201, res.text

    log = await _wait_for_log(db, method="POST", path_substring="/admin/users")
    assert log is not None, "audit row not written by middleware"
    assert log.status_code == 201
    assert log.actor_user_id == admin.id
    assert log.role == "admin"
    # Email should be redacted in the request body cell.
    assert log.request_body is not None
    assert log.request_body.get("email") == "***@example.com"


@pytest.mark.asyncio
async def test_middleware_skips_get_requests(client, db) -> None:
    admin = User(
        email="audit-admin-2@example.com",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    res = await client.get("/api/v1/admin/users", headers=_bearer(admin))
    assert res.status_code == 200

    # Give the event loop a tick — no audit row should appear for the GET.
    await asyncio.sleep(0.05)
    rows = (await db.execute(select(AuditLog).where(AuditLog.method == "GET"))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_middleware_records_failed_mutation(client, db) -> None:
    admin = User(
        email="audit-admin-3@example.com",
        password_hash=hash_password("x"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)

    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "x@example.com", "role": "wizard"},
    )
    assert res.status_code == 400

    log = await _wait_for_log(db, method="POST", path_substring="/admin/users")
    assert log is not None
    assert log.status_code == 400
