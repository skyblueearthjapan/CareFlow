"""users.display_name (画面に出す呼び名・PO 2026-10-13) — admin API と /auth/me。

名前は既定では空 (NULL)。空のあいだ画面はログイン ID を出す (フロント側)。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.schemas.admin_user import AdminUserCreate, AdminUserUpdate


async def _make_user(db, email: str | None, role: str, username: str | None = None) -> User:
    user = User(email=email, username=username, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# --- schema --------------------------------------------------------------------


def test_schema_trims_and_blank_becomes_none() -> None:
    assert AdminUserCreate(username="a1", display_name="  今泉　勇二  ").display_name == "今泉　勇二"
    assert AdminUserUpdate(display_name="   ").display_name is None
    assert AdminUserUpdate(display_name=None).display_name is None
    assert AdminUserCreate(username="a1").display_name is None


def test_schema_rejects_over_60_chars() -> None:
    AdminUserUpdate(display_name="あ" * 60)
    with pytest.raises(ValueError):
        AdminUserUpdate(display_name="あ" * 61)


# --- API -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_with_display_name(client, db) -> None:
    admin = await _make_user(db, "dn-admin-1@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s901", "role": "staff", "display_name": " 山田 花子 "},
    )
    assert res.status_code == 201, res.text
    assert res.json()["user"]["display_name"] == "山田 花子"
    row = await db.scalar(select(User).where(User.username == "s901"))
    assert row is not None and row.display_name == "山田 花子"


@pytest.mark.asyncio
async def test_create_user_without_display_name_stays_empty(client, db) -> None:
    admin = await _make_user(db, "dn-admin-2@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s902", "role": "staff"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["user"]["display_name"] is None


@pytest.mark.asyncio
async def test_create_user_display_name_too_long_is_422(client, db) -> None:
    admin = await _make_user(db, "dn-admin-3@example.com", "admin")
    res = await client.post(
        "/api/v1/admin/users",
        headers=_bearer(admin),
        json={"username": "s903", "role": "staff", "display_name": "x" * 61},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_patch_sets_and_clears_display_name(client, db) -> None:
    admin = await _make_user(db, "dn-admin-4@example.com", "admin")
    target = await _make_user(db, None, "staff", username="s904")

    res = await client.patch(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
        json={"display_name": "佐藤 一郎"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["display_name"] == "佐藤 一郎"

    # 一覧にも出る・名前で検索できる。
    lst = await client.get("/api/v1/admin/users", headers=_bearer(admin), params={"q": "佐藤"})
    assert lst.status_code == 200, lst.text
    assert [u["display_name"] for u in lst.json()["items"]] == ["佐藤 一郎"]

    # 空白だけ → 空 (NULL) に戻る。
    res = await client.patch(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
        json={"display_name": "  "},
    )
    assert res.status_code == 200, res.text
    assert res.json()["display_name"] is None


@pytest.mark.asyncio
async def test_patch_display_name_too_long_is_422(client, db) -> None:
    admin = await _make_user(db, "dn-admin-5@example.com", "admin")
    target = await _make_user(db, None, "staff", username="s905")
    res = await client.patch(
        f"/api/v1/admin/users/{target.id}",
        headers=_bearer(admin),
        json={"display_name": "x" * 61},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_me_returns_display_name(client, db) -> None:
    user = await _make_user(db, None, "staff", username="s906")
    res = await client.get("/api/v1/auth/me", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["display_name"] is None

    user.display_name = "鈴木 次郎"
    await db.commit()
    res = await client.get("/api/v1/auth/me", headers=_bearer(user))
    assert res.json()["display_name"] == "鈴木 次郎"
