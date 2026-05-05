"""Wave 4-F /admin/users CRUD + RBAC tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password, verify_password
from app.models import User


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_user_returns_temp_password_and_persists_hash(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-1@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "newbie@example.com", "role": "staff"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["user"]["email"] == "newbie@example.com"
    assert body["user"]["role"] == "staff"
    assert body["user"]["must_change_password"] is True
    assert isinstance(body["temp_password"], str) and len(body["temp_password"]) >= 12

    # Persisted row carries a bcrypt hash, not the plaintext.
    created = await db.scalar(select(User).where(User.email == "newbie@example.com"))
    assert created is not None
    assert created.password_hash != body["temp_password"]
    assert verify_password(body["temp_password"], created.password_hash)


@pytest.mark.asyncio
async def test_create_user_rejects_invalid_role(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-2@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "x@example.com", "role": "wizard"},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_create_user_duplicate_email_returns_409(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-3@example.com", "admin")
    payload = {"email": "dup@example.com", "role": "staff"}
    r1 = await client.post("/api/v1/admin/users", headers=_bearer(admin), json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/admin/users", headers=_bearer(admin), json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_list_users_filters_by_role_and_q(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-4@example.com", "admin")
    await _make_user(db, "filter-mgr@example.com", "manager")
    await _make_user(db, "filter-staff@example.com", "staff")

    res = await client.get(
        "/api/v1/admin/users?role=manager",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert all(it["role"] == "manager" for it in items)
    assert any(it["email"] == "filter-mgr@example.com" for it in items)

    res2 = await client.get(
        "/api/v1/admin/users?q=filter-staff",
        headers=_bearer(admin),
    )
    assert res2.status_code == 200
    emails = [it["email"] for it in res2.json()["items"]]
    assert "filter-staff@example.com" in emails


@pytest.mark.asyncio
async def test_patch_user_changes_role(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-5@example.com", "admin")
    target = await _make_user(db, "promote-me@example.com", "staff")
    res = await client.patch(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
        json={"role": "manager"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["role"] == "manager"


@pytest.mark.asyncio
async def test_reset_password_issues_new_credential(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-6@example.com", "admin")
    target = await _make_user(db, "needs-reset@example.com", "staff")
    res = await client.post(
        f"/api/v1/admin/users/{target.id}/reset-password",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user_id"] == str(target.id)
    assert isinstance(body["temp_password"], str) and len(body["temp_password"]) >= 12

    await db.refresh(target)
    assert target.must_change_password is True
    assert verify_password(body["temp_password"], target.password_hash)


@pytest.mark.asyncio
async def test_delete_user_soft_deletes_and_excludes_from_default_list(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-7@example.com", "admin")
    target = await _make_user(db, "byebye@example.com", "staff")
    res = await client.delete(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    listing = await client.get("/api/v1/admin/users", headers=_bearer(admin))
    emails = [it["email"] for it in listing.json()["items"]]
    assert "byebye@example.com" not in emails

    listing2 = await client.get("/api/v1/admin/users?include_deleted=true", headers=_bearer(admin))
    emails2 = [it["email"] for it in listing2.json()["items"]]
    assert "byebye@example.com" in emails2


@pytest.mark.asyncio
async def test_admin_cannot_self_delete(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-8@example.com", "admin")
    res = await client.delete(f"/api/v1/admin/users/{admin.id}", headers=_bearer(admin))
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_manager_cannot_create_user(client, db) -> None:
    manager = await _make_user(db, "wave4f-mgr-1@example.com", "manager")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(manager),
        json={"email": "x@example.com", "role": "staff"},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# /audit-logs RBAC seeding contract (W5-F follow-up, issue #16)
#
# The /audit-logs endpoint is admin-only by design (see backend/app/api/v1/
# audit_logs.py — `Depends(require_role("admin"))`). When chie.kawana
# reported a 403 from /audit-logs in 2026-05, the root cause was NOT a bug
# in the RBAC code — it was that the production user row carried role
# `"manager"` (not `"admin"`). The 管理者 sheet in Sample 2 lists
# `chie.kawana` with role=manager, and `import_users.py::_norm_role` keeps
# any value that's already in USER_ROLES, so manager stays manager.
#
# These two tests pin the contract so that:
#   1) admin really gets 200 from /audit-logs
#   2) manager (chie's actual role) really gets 403 — confirming that the
#      operational misunderstanding is not a regression in require_role.
#
# If business decides chie should access /audit-logs, the fix is to
# PATCH her role to admin, NOT to relax audit_logs.py.


@pytest.mark.asyncio
async def test_audit_logs_admin_returns_200(client, db) -> None:
    admin = await _make_user(db, "audit-rbac-admin@example.com", "admin")
    res = await client.get("/api/v1/audit-logs", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert "items" in res.json()


@pytest.mark.asyncio
async def test_audit_logs_manager_returns_403(client, db) -> None:
    """Manager role (chie.kawana's actual role per Sample 2 管理者 sheet)
    must NOT access /audit-logs. This documents that the 2026-05 403 report
    is expected behaviour, not a bug."""
    manager = await _make_user(db, "audit-rbac-manager@example.com", "manager")
    res = await client.get("/api/v1/audit-logs", headers=_bearer(manager))
    assert res.status_code == 403, res.text
