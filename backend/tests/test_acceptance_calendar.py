"""Tests for /api/v1/acceptance-calendar (W15-BE1).

検証観点:
  1. GET (admin) 該当なし → 空配列
  2. PUT (admin) 0 件 → 空配列返却
  3. PUT (admin) 数件 → 件数一致 + ソート
  4. PUT 冪等性: 同じ payload を 2 回送って結果が同じ
  5. PUT 重複 (weekday, time_slot) → 422
  6. PUT staff → 403 (mutation 禁止)
  7. PUT viewer (manager 以外で role に該当しないユーザ) → 403
  8. PUT office_id 不正 → 422
  9. GET (staff) → 200 (read 可)
 10. PUT で既存削除が確実に走る (前後の件数比較)
 11. status 値が不正 → 422
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, User

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


async def _make_office(db, name: str = "事業所A") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _entry(weekday: int, hour: int, status: str = "available", notes: str | None = None) -> dict:
    e = {
        "weekday": weekday,
        "time_slot": f"{hour:02d}:00:00",
        "status": status,
    }
    if notes is not None:
        e["notes"] = notes
    return e


# ---------------------------------------------------------------------------
# 1. GET (admin) 該当なし → 空配列
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_acceptance_calendar_empty(client, db) -> None:
    admin = await _make_user(db, "ac-list1@example.com", "admin")
    office = await _make_office(db)
    res = await client.get(
        f"/api/v1/acceptance-calendar?office_id={office.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 2. PUT (admin) 0 件 → 空配列返却
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_zero(client, db) -> None:
    admin = await _make_user(db, "ac-put0@example.com", "admin")
    office = await _make_office(db)
    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={"office_id": str(office.id), "entries": []},
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 3. PUT (admin) 数件 → 件数一致 + ソート
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_multiple(client, db) -> None:
    admin = await _make_user(db, "ac-put-many@example.com", "admin")
    office = await _make_office(db)

    # 月曜 10/11 + 火曜 10 を投入 (順序を意図的にバラバラに)
    entries = [
        _entry(1, 10, status="consult"),
        _entry(0, 11, status="available"),
        _entry(0, 10, status="available", notes="朝枠"),
    ]
    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={"office_id": str(office.id), "entries": entries},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 3
    # weekday + time_slot 昇順
    keys = [(r["weekday"], r["time_slot"]) for r in body]
    assert keys == sorted(keys)
    # notes が反映されている
    monday_10 = [r for r in body if r["weekday"] == 0 and r["time_slot"] == "10:00:00"][0]
    assert monday_10["notes"] == "朝枠"


# ---------------------------------------------------------------------------
# 4. 冪等性: 同じ payload 2 回 → 結果同一
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_idempotent(client, db) -> None:
    admin = await _make_user(db, "ac-idem@example.com", "admin")
    office = await _make_office(db)

    payload = {
        "office_id": str(office.id),
        "entries": [
            _entry(2, 14, status="available"),
            _entry(2, 15, status="consult"),
            _entry(3, 10, status="unavailable"),
        ],
    }
    r1 = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json=payload,
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()

    r2 = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json=payload,
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()

    # 件数一致 + 内容 (id を除き) 一致
    assert len(body1) == len(body2) == 3
    strip_keys = {"id", "created_at", "updated_at"}

    def _normalize(rows: list[dict]) -> list[dict]:
        return [{k: v for k, v in r.items() if k not in strip_keys} for r in rows]

    assert _normalize(body1) == _normalize(body2)

    # GET でも 3 件
    list_res = await client.get(
        f"/api/v1/acceptance-calendar?office_id={office.id}",
        headers=_bearer(admin),
    )
    assert list_res.status_code == 200
    assert len(list_res.json()) == 3


# ---------------------------------------------------------------------------
# 5. 重複 (weekday, time_slot) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_duplicate_422(client, db) -> None:
    admin = await _make_user(db, "ac-dup@example.com", "admin")
    office = await _make_office(db)

    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "entries": [
                _entry(0, 10),
                _entry(0, 10, status="consult"),
            ],
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 6. PUT staff → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_staff_forbidden(client, db) -> None:
    staff = await _make_user(db, "ac-staff@example.com", "staff")
    office = await _make_office(db)
    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(staff),
        json={"office_id": str(office.id), "entries": [_entry(0, 10)]},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 7. PUT viewer (role が viewer などで mutation 不可) → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_viewer_forbidden(client, db) -> None:
    """role='viewer' は admin/manager に該当しないので 403."""
    viewer = await _make_user(db, "ac-viewer@example.com", "viewer")
    office = await _make_office(db)
    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(viewer),
        json={"office_id": str(office.id), "entries": []},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 8. PUT office_id 不正 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_invalid_office_422(client, db) -> None:
    admin = await _make_user(db, "ac-bad-office@example.com", "admin")
    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={"office_id": str(uuid4()), "entries": [_entry(0, 10)]},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 9. GET (staff) → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_acceptance_calendar_staff_can_read(client, db) -> None:
    staff = await _make_user(db, "ac-list-staff@example.com", "staff")
    office = await _make_office(db)
    res = await client.get(
        f"/api/v1/acceptance-calendar?office_id={office.id}",
        headers=_bearer(staff),
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 10. PUT で既存削除が確実に走る (異なるエントリで上書き)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_overwrites(client, db) -> None:
    admin = await _make_user(db, "ac-overwrite@example.com", "admin")
    office = await _make_office(db)

    # 1 回目: 3 件
    r1 = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "entries": [_entry(0, 10), _entry(0, 11), _entry(1, 10)],
        },
    )
    assert r1.status_code == 200
    assert len(r1.json()) == 3

    # 2 回目: 1 件のみで上書き → 既存 3 件は消えるはず
    r2 = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "entries": [_entry(2, 14, status="unavailable")],
        },
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert len(body2) == 1
    assert body2[0]["weekday"] == 2
    assert body2[0]["time_slot"] == "14:00:00"
    assert body2[0]["status"] == "unavailable"

    # GET でも 1 件
    list_res = await client.get(
        f"/api/v1/acceptance-calendar?office_id={office.id}",
        headers=_bearer(admin),
    )
    assert list_res.status_code == 200
    assert len(list_res.json()) == 1


# ---------------------------------------------------------------------------
# 11. status 値が不正 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_acceptance_calendar_invalid_status_422(client, db) -> None:
    admin = await _make_user(db, "ac-bad-status@example.com", "admin")
    office = await _make_office(db)
    res = await client.put(
        "/api/v1/acceptance-calendar",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "entries": [{"weekday": 0, "time_slot": "10:00:00", "status": "BOGUS"}],
        },
    )
    assert res.status_code == 422, res.text
