"""POST /api/v1/staff/{id}/events の ``blocking`` 受け取り (Wave 2-D)。

🔒付きひな形 (event_templates.blocking) から作ったイベントが 🔒 を引き継げる
ように、作成 API が ``blocking`` を受ける (staff-event-history-design.md §2
Phase 2)。後方互換 = 省略時は False (従来どおり)。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.staff import StaffEvent


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(db, name: str = "ブロッキング 太郎") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _payload(**over) -> dict:
    base = {
        "date": "2026-09-02",
        "type": "イベント",
        "start_time": "13:00",
        "end_time": "14:00",
        "title": "サービス担当者会議",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_create_event_accepts_blocking_true(client, db) -> None:
    admin = await _make_user(db, "ev-blocking-1@example.com")
    staff = await _make_staff(db)

    res = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(admin),
        json=_payload(blocking=True),
    )
    assert res.status_code == 201, res.text
    assert res.json()["blocking"] is True

    row = await db.scalar(select(StaffEvent).where(StaffEvent.staff_id == staff.id))
    assert row is not None
    assert row.blocking is True


@pytest.mark.asyncio
async def test_create_event_defaults_blocking_false(client, db) -> None:
    """後方互換: ``blocking`` を送らない従来のクライアントは False のまま。"""
    admin = await _make_user(db, "ev-blocking-2@example.com")
    staff = await _make_staff(db)

    res = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(admin),
        json=_payload(),
    )
    assert res.status_code == 201, res.text
    assert res.json()["blocking"] is False

    row = await db.scalar(select(StaffEvent).where(StaffEvent.staff_id == staff.id))
    assert row is not None
    assert row.blocking is False


@pytest.mark.asyncio
async def test_create_event_accepts_blocking_false_explicitly(client, db) -> None:
    admin = await _make_user(db, "ev-blocking-3@example.com")
    staff = await _make_staff(db)

    res = await client.post(
        f"/api/v1/staff/{staff.id}/events",
        headers=_bearer(admin),
        json=_payload(blocking=False),
    )
    assert res.status_code == 201, res.text
    assert res.json()["blocking"] is False
