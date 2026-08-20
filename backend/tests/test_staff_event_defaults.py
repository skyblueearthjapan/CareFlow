"""毎週の固定イベント (staff_event_defaults) のテスト — Phase 2.

正典 = docs/plans/kaipoke-event-two-way-design.md §3-②。

検証観点:
  1. 週展開: source='fixed' + external_id='{default_id}:{date}' で作成 /
     再実行しても増えない (冪等) / 内容一致の既存行 (昇格済み・取込済み) が
     居れば作らない / 休職スタッフは展開しない
  2. API: CRUD + RBAC (本人GET可・staff POST 403)
  3. 展開行が outbound 送信対象 (source='fixed') に含まれる
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.staff import StaffEvent, StaffEventDefault
from app.services.staff_event_defaults import expand_staff_event_defaults

ISO_YEAR, ISO_WEEK = 2026, 37  # 2026-09-07(月) の週
MONDAY = date(2026, 9, 7)


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, name: str = "固定 花子", status: str = "active") -> Staff:
    s = Staff(name=name, status=status)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_default(
    db, staff: Staff, *, weekday: int = 2, start: str = "09:00", end: str = "09:15",
    title: str = "朝会", blocking: bool = False,
) -> StaffEventDefault:
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    row = StaffEventDefault(
        staff_id=staff.id,
        weekday=weekday,
        start_time=time(sh, sm),
        end_time=time(eh, em),
        title=title,
        blocking=blocking,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _fixed_events(db) -> list[StaffEvent]:
    return list(
        (await db.scalars(select(StaffEvent).where(StaffEvent.source == "fixed"))).all()
    )


# ---------------------------------------------------------------------------
# 1) 週展開
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_creates_fixed_event_idempotently(client, db) -> None:
    staff = await _make_staff(db)
    d = await _make_default(db, staff, weekday=2, title="朝会", blocking=True)

    created = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created == 1
    rows = await _fixed_events(db)
    assert len(rows) == 1
    ev = rows[0]
    assert ev.external_id == f"{d.id}:2026-09-09"  # 月曜+2 = 水曜
    assert ev.starts_at == datetime(2026, 9, 9, 9, 0)
    assert ev.ends_at == datetime(2026, 9, 9, 9, 15)
    assert ev.title == "朝会"
    assert ev.blocking is True

    # 再実行で増えない (冪等)
    created2 = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created2 == 0
    assert len(await _fixed_events(db)) == 1


@pytest.mark.asyncio
async def test_expand_skips_when_same_content_exists(client, db) -> None:
    """昇格済み (source='kaipoke') や取込済みの同内容が居れば再作成しない。"""
    staff = await _make_staff(db)
    await _make_default(db, staff, weekday=2, title="朝会")

    # カイポケへ送信して昇格済みのイベント (同 staff×日×時刻×名称)
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="event",
            starts_at=datetime(2026, 9, 9, 9, 0),
            ends_at=datetime(2026, 9, 9, 9, 15),
            title="朝会",
            source="kaipoke",
            external_id="555:4465191:2026-09-09",
        )
    )
    await db.commit()

    created = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created == 0
    assert len(await _fixed_events(db)) == 0  # fixed 行は作られない


@pytest.mark.asyncio
async def test_expand_skips_inactive_staff(client, db) -> None:
    staff = await _make_staff(db, status="on_leave")
    await _make_default(db, staff)
    created = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created == 0


@pytest.mark.asyncio
async def test_expanded_fixed_event_is_outbound_sendable(client, db) -> None:
    """展開行 (source='fixed') は送信プレビューの対象に含まれる。"""
    from app.services.kaipoke.events_outbound import build_outbound_plan

    staff = await _make_staff(db)
    # 職員内部IDの逆引き供給源 (取込済み行)
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="event",
            starts_at=datetime(2026, 8, 3, 9, 0),
            ends_at=datetime(2026, 8, 3, 10, 0),
            title="過去取込",
            source="kaipoke",
            external_id="111:4465191:2026-08-03",
        )
    )
    await db.commit()
    await _make_default(db, staff, weekday=2, title="朝会")
    await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()

    plan = await build_outbound_plan(db, MONDAY)
    assert plan.sendable_count == 1
    assert plan.items[0].title == "朝会"


# ---------------------------------------------------------------------------
# 2) API CRUD + RBAC
# ---------------------------------------------------------------------------


def _url(staff_id) -> str:
    return f"/api/v1/staff/{staff_id}/event-defaults"


@pytest.mark.asyncio
async def test_event_defaults_crud(client, db) -> None:
    admin = await _make_user(db, "ed-admin@example.com", "admin")
    staff = await _make_staff(db)

    res = await client.post(
        _url(staff.id),
        headers=_bearer(admin),
        json={"weekday": 0, "start_time": "09:00", "end_time": "09:15", "title": "朝会"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["weekday_label"] == "月"
    default_id = body["id"]

    res = await client.patch(
        f"{_url(staff.id)}/{default_id}",
        headers=_bearer(admin),
        json={"start_time": "08:50", "blocking": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["start_time"] == "08:50"
    assert res.json()["blocking"] is True

    res = await client.get(_url(staff.id), headers=_bearer(admin))
    assert res.status_code == 200
    assert len(res.json()) == 1

    res = await client.delete(f"{_url(staff.id)}/{default_id}", headers=_bearer(admin))
    assert res.status_code == 204
    total = await db.scalar(select(func.count()).select_from(StaffEventDefault))
    assert int(total or 0) == 0


@pytest.mark.asyncio
async def test_event_defaults_rbac(client, db) -> None:
    staff = await _make_staff(db)
    me = await _make_user(db, "ed-staff@example.com", "staff", staff_id=staff.id)
    other_staff = await _make_staff(db, name="他人 次郎")
    other = await _make_user(db, "ed-other@example.com", "staff", staff_id=other_staff.id)

    # 本人は読める / 他人は 404 / staff の POST は 403
    res = await client.get(_url(staff.id), headers=_bearer(me))
    assert res.status_code == 200
    res = await client.get(_url(staff.id), headers=_bearer(other))
    assert res.status_code == 404
    res = await client.post(
        _url(staff.id),
        headers=_bearer(me),
        json={"weekday": 0, "start_time": "09:00", "end_time": "09:15", "title": "朝会"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_event_defaults_validation(client, db) -> None:
    admin = await _make_user(db, "ed-admin2@example.com", "admin")
    staff = await _make_staff(db)
    # weekday 6 (日曜) は 422
    res = await client.post(
        _url(staff.id),
        headers=_bearer(admin),
        json={"weekday": 6, "start_time": "09:00", "end_time": "09:15", "title": "朝会"},
    )
    assert res.status_code == 422
    # 逆転時刻は 422
    res = await client.post(
        _url(staff.id),
        headers=_bearer(admin),
        json={"weekday": 0, "start_time": "10:00", "end_time": "09:00", "title": "朝会"},
    )
    assert res.status_code == 422
