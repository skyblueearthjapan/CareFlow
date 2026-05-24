"""Phase G-45: offices API の operating_weekdays 入出力."""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import User


async def _make_admin(db, email: str = "g45-admin@example.com") -> User:
    user = User(email=email, password_hash=hash_password("dont-care"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_office_create_operating_weekdays_default(client, db) -> None:
    """POST /offices で operating_weekdays 省略時は default (= 月-土) が入る."""
    admin = await _make_admin(db)
    res = await client.post(
        "/api/v1/offices",
        json={"name": "g45-api-default"},
        headers=_bearer(admin),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["operating_weekdays"] == [0, 1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_office_patch_operating_weekdays_round_trip(client, db) -> None:
    """POST → PATCH で operating_weekdays を変更して GET で読み取り."""
    admin = await _make_admin(db, "g45-admin-patch@example.com")
    res = await client.post(
        "/api/v1/offices",
        json={"name": "g45-api-patch", "operating_weekdays": [0, 1, 2, 3, 4]},
        headers=_bearer(admin),
    )
    assert res.status_code == 201, res.text
    office_id = res.json()["id"]
    assert res.json()["operating_weekdays"] == [0, 1, 2, 3, 4]

    res2 = await client.patch(
        f"/api/v1/offices/{office_id}",
        json={"operating_weekdays": [0, 2, 4]},
        headers=_bearer(admin),
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["operating_weekdays"] == [0, 2, 4]

    res3 = await client.get(f"/api/v1/offices/{office_id}", headers=_bearer(admin))
    assert res3.status_code == 200, res3.text
    assert res3.json()["operating_weekdays"] == [0, 2, 4]


@pytest.mark.asyncio
async def test_office_patch_operating_weekdays_none_preserves_existing(client, db) -> None:
    """PATCH で operating_weekdays を省略すると既存値を維持する (= 触らない)."""
    admin = await _make_admin(db, "g45-admin-preserve@example.com")
    res = await client.post(
        "/api/v1/offices",
        json={"name": "g45-api-preserve", "operating_weekdays": [0, 1, 2, 3, 4]},
        headers=_bearer(admin),
    )
    office_id = res.json()["id"]
    res2 = await client.patch(
        f"/api/v1/offices/{office_id}",
        json={"name": "g45-api-preserve-renamed"},  # operating_weekdays 省略.
        headers=_bearer(admin),
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["operating_weekdays"] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_operating_weekdays_validation_rejects_invalid(client, db) -> None:
    """7 / 重複 / 空 / 範囲外 は validation error."""
    admin = await _make_admin(db, "g45-admin-validate@example.com")

    # 7 (= 範囲外)
    res = await client.post(
        "/api/v1/offices",
        json={"name": "g45-bad-7", "operating_weekdays": [0, 7]},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text

    # 重複
    res2 = await client.post(
        "/api/v1/offices",
        json={"name": "g45-bad-dup", "operating_weekdays": [0, 1, 1, 2]},
        headers=_bearer(admin),
    )
    assert res2.status_code == 422, res2.text

    # 空
    res3 = await client.post(
        "/api/v1/offices",
        json={"name": "g45-bad-empty", "operating_weekdays": []},
        headers=_bearer(admin),
    )
    assert res3.status_code == 422, res3.text

    # 負値
    res4 = await client.post(
        "/api/v1/offices",
        json={"name": "g45-bad-neg", "operating_weekdays": [-1, 0]},
        headers=_bearer(admin),
    )
    assert res4.status_code == 422, res4.text
