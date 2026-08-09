"""Wave 4-F /admin/users CRUD + RBAC tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.models.staff import Staff


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


async def _make_staff(db, name: str, code: str | None = None) -> Staff:
    staff = Staff(name=name, code=code, status="active")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_user_returns_temp_password_and_persists_hash(client, db) -> None:
    admin = await _make_user(db, "wave4f-admin-1@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "newbie@example.com", "username": "newbie-staff", "role": "staff"},
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
    # staff role requires username; use different usernames so only email conflicts.
    r1 = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "dup@example.com", "username": "dup-user-1", "role": "staff"},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "dup@example.com", "username": "dup-user-2", "role": "staff"},
    )
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
    # 二軸分離 (2026-08-09): 旧 'manager' 指定は admin へ寛容パースされる。
    assert res.json()["role"] == "admin"


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
async def test_staff_cannot_create_user_manager_alias_can(client, db) -> None:
    """二軸分離 (2026-08-09): 一般 (staff) はユーザー作成不可。
    旧 'manager' は admin の別名なので作成できる。"""
    staff = await _make_user(db, "wave4f-staff-1@example.com", "staff")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(staff),
        json={"email": "x@example.com", "role": "staff"},
    )
    assert res.status_code == 403, res.text

    # 403 経路の後は共有セッションに未消化ステートメントが残ることがある
    # (aiosqlite の既知の不安定さ)。次の commit を安定させるため明示 rollback。
    await db.rollback()
    manager = await _make_user(db, "wave4f-mgr-1@example.com", "manager")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(manager),
        json={"email": "x2@example.com", "username": "x2", "role": "staff"},
    )
    assert res.status_code == 201, res.text


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
async def test_audit_logs_manager_alias_is_admin(client, db) -> None:
    """二軸分離 (PO 決定 2026-08-09): 旧 'manager' 権限は admin の別名。
    残存する manager 行/トークンでも管理者として扱われ、audit-logs にも入れる
    (本番行は migration 0069 で admin へ移行済み)。一般 (staff) は従来どおり 403。"""
    manager = await _make_user(db, "audit-rbac-manager@example.com", "manager")
    res = await client.get("/api/v1/audit-logs", headers=_bearer(manager))
    assert res.status_code == 200, res.text

    staff = await _make_user(db, "audit-rbac-staff@example.com", "staff")
    res = await client.get("/api/v1/audit-logs", headers=_bearer(staff))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# P1a: staff × account linking (username / staff_id / staff_name)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_with_staff_link_returns_staff_name(client, db) -> None:
    admin = await _make_user(db, "link-admin-1@example.com", "admin")
    staff = await _make_staff(db, "山田太郎", code="S010")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={
            "email": "linked@example.com",
            "username": "s010linked",
            "role": "staff",
            "staff_id": str(staff.id),
        },
    )
    assert res.status_code == 201, res.text
    user = res.json()["user"]
    assert user["staff_id"] == str(staff.id)
    assert user["staff_name"] == "山田太郎"


@pytest.mark.asyncio
async def test_create_staff_user_with_username_only(client, db) -> None:
    admin = await _make_user(db, "link-admin-2@example.com", "admin")
    # uppercase "S020" is normalized to "s020" by the schema validator.
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "S020", "role": "staff"},
    )
    assert res.status_code == 201, res.text
    user = res.json()["user"]
    assert user["username"] == "s020"  # normalized to lowercase
    assert user["email"] is None


@pytest.mark.asyncio
async def test_create_user_without_email_or_username_is_422(client, db) -> None:
    admin = await _make_user(db, "link-admin-3@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"role": "staff", "staff_id": None},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_create_user_double_staff_link_returns_409(client, db) -> None:
    admin = await _make_user(db, "link-admin-4@example.com", "admin")
    staff = await _make_staff(db, "二重紐付け", code="S030")
    r1 = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "S030a", "role": "staff", "staff_id": str(staff.id)},
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "S030b", "role": "staff", "staff_id": str(staff.id)},
    )
    assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_create_user_nonexistent_staff_id_is_422(client, db) -> None:
    admin = await _make_user(db, "link-admin-5@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "S040", "role": "staff", "staff_id": str(uuid.uuid4())},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_create_user_soft_deleted_staff_is_422(client, db) -> None:
    from datetime import UTC, datetime

    admin = await _make_user(db, "link-admin-6@example.com", "admin")
    staff = await _make_staff(db, "退職者", code="S050")
    staff.deleted_at = datetime.now(tz=UTC)
    await db.commit()
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "S050", "role": "staff", "staff_id": str(staff.id)},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_patch_user_links_and_unlinks_staff(client, db) -> None:
    admin = await _make_user(db, "link-admin-7@example.com", "admin")
    staff = await _make_staff(db, "紐付け対象", code="S060")
    target = await _make_user(db, "to-link@example.com", "staff")

    # Link.
    res = await client.patch(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
        json={"staff_id": str(staff.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["staff_id"] == str(staff.id)
    assert res.json()["staff_name"] == "紐付け対象"

    # Unlink — user keeps its email so this is allowed.
    res2 = await client.patch(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
        json={"staff_id": None},
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["staff_id"] is None
    assert res2.json()["staff_name"] is None


@pytest.mark.asyncio
async def test_patch_unlink_leaving_no_identifier_is_422(client, db) -> None:
    """Clearing the only login identifier via PATCH must return 422.

    The DB CHECK constraint (ck_users_has_login_identifier) forbids NULL email +
    NULL username rows, so this test uses the API to create a valid staff account
    (username only) and then verifies that clearing the username is rejected before
    reaching the DB.
    """
    admin = await _make_user(db, "link-admin-8@example.com", "admin")
    staff = await _make_staff(db, "ログイン不能ガード", code="S070")
    # Create a staff-only account (no email, username-only) linked to staff.
    r = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s070guard", "role": "staff", "staff_id": str(staff.id)},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user"]["id"]

    # Clearing the only identifier → login would be impossible → 422.
    res = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_bearer(admin),
        json={"username": None},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_patch_role_change_to_staff_without_username_is_422(client, db) -> None:
    """Changing an email-only account to staff via PATCH requires a username.

    Mirrors create_user's role-specific rule so a direct API PATCH cannot leave
    an account inconsistent with its role.
    """
    admin = await _make_user(db, "link-admin-role1@example.com", "admin")
    # email-only manager account.
    r = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "role-target@example.com", "role": "manager"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user"]["id"]

    # Switch to staff without providing a username → 422.
    res = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_bearer(admin),
        json={"role": "staff"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_patch_role_change_to_admin_without_email_is_422(client, db) -> None:
    """Changing a username-only staff account to admin via PATCH requires email."""
    admin = await _make_user(db, "link-admin-role2@example.com", "admin")
    r = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "roleflip01", "role": "staff"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user"]["id"]

    res = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_bearer(admin),
        json={"role": "admin"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_list_users_q_matches_username(client, db) -> None:
    admin = await _make_user(db, "link-admin-9@example.com", "admin")
    await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "searchme01", "role": "staff"},
    )
    res = await client.get("/api/v1/admin/users?q=searchme", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    usernames = [it["username"] for it in res.json()["items"]]
    assert "searchme01" in usernames


# ---------------------------------------------------------------------------
# Code-review follow-up: normalization, role-specific validation, identifiers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_duplicate_username_returns_409(client, db) -> None:
    admin = await _make_user(db, "link-admin-10@example.com", "admin")
    r1 = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s099dup", "role": "staff"},
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s099dup", "role": "staff"},
    )
    assert r2.status_code == 409, r2.text
    assert "username" in r2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_username_normalized_to_lowercase(client, db) -> None:
    """Upper-case username in the request must be stored as lower-case."""
    admin = await _make_user(db, "link-admin-11@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "S001Norm", "role": "staff"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["user"]["username"] == "s001norm"


@pytest.mark.asyncio
async def test_role_admin_requires_email_422(client, db) -> None:
    """Creating an admin account without email must be rejected with 422."""
    admin = await _make_user(db, "link-admin-12@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "adminnomail", "role": "admin"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_role_staff_requires_username_422(client, db) -> None:
    """Creating a staff account without username must be rejected with 422."""
    admin = await _make_user(db, "link-admin-13@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "staff-no-username@example.com", "role": "staff"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_patch_email_to_null_ok_when_username_remains(client, db) -> None:
    """Clearing email via PATCH is allowed when username is still present."""
    admin = await _make_user(db, "link-admin-14@example.com", "admin")
    # Create staff with both email and username.
    r = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"email": "both-idents@example.com", "username": "bothident", "role": "staff"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user"]["id"]

    # Clearing email is fine because username remains.
    res = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_bearer(admin),
        json={"email": None},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["email"] is None
    assert body["username"] == "bothident"


@pytest.mark.asyncio
async def test_patch_username_to_null_only_identifier_is_422(client, db) -> None:
    """Clearing the username when email is None must be rejected with 422."""
    admin = await _make_user(db, "link-admin-15@example.com", "admin")
    r = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "sole-ident", "role": "staff"},
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["user"]["id"]

    # Clearing the only identifier → login would be impossible → 422.
    res = await client.patch(
        f"/api/v1/admin/users/{user_id}",
        headers=_bearer(admin),
        json={"username": None},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_list_users_response_includes_staff_name(client, db) -> None:
    """list_users items carry staff_name when a staff link exists."""
    admin = await _make_user(db, "link-admin-16@example.com", "admin")
    staff = await _make_staff(db, "一覧確認スタッフ", code="S099")
    await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s099list", "role": "staff", "staff_id": str(staff.id)},
    )
    res = await client.get("/api/v1/admin/users?q=s099list", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) >= 1
    linked = next(it for it in items if it["username"] == "s099list")
    assert linked["staff_name"] == "一覧確認スタッフ"
