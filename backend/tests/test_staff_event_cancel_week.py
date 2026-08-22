"""イベントの「今週だけ外す」 — POST /api/v1/staff/{id}/events/{event_id}/cancel-week

正典 = docs/plans/week-cockpit-design.md 決定 D2 / §2-3 (mig 0075)。

検証観点:
  1. cancel=true で ``cancelled_at`` が立ち、EventRead / 週一括取得 (GET
     /staff/{id}/events = FE の useWeekStaffEvents が叩く先) が露出する
  2. cancel=false で降りる / 冪等
  3. source 不問 (fixed / manual / kaipoke)
  4. RBAC: staff は 403 / 未知の event は 404
  5. **復活しないこと**: cancelled な fixed 行が残っている限り
     ``expand_staff_event_defaults`` は再生成しない (冪等キー一致で skip)
  6. cancelled は送信 (``build_outbound_plan``)・Layer3 の重なり判定・
     提案エンジンのイベント収集から外れる
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.staff import StaffEvent, StaffEventDefault
from app.services.kaipoke.events_outbound import build_outbound_plan
from app.services.scheduling.layer3_assignment import Layer3Assigner
from app.services.scheduling.propose_slots_service import load_week_event_windows
from app.services.staff_event_defaults import expand_staff_event_defaults

ISO_YEAR, ISO_WEEK = 2026, 37  # 2026-09-07(月) の週
MONDAY = date(2026, 9, 7)
WEDNESDAY = date(2026, 9, 9)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cancel_url(staff_id, event_id) -> str:
    return f"/api/v1/staff/{staff_id}/events/{event_id}/cancel-week"


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, name: str = "取消 花子") -> Staff:
    s = Staff(name=name, status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_event(
    db,
    staff: Staff,
    *,
    source: str = "manual",
    external_id: str | None = None,
    day: date = WEDNESDAY,
    start: time = time(9, 0),
    end: time = time(9, 30),
    title: str = "朝会",
) -> StaffEvent:
    row = StaffEvent(
        staff_id=staff.id,
        event_type="event",
        starts_at=datetime.combine(day, start),
        ends_at=datetime.combine(day, end),
        title=title,
        source=source,
        external_id=external_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# 1-2. 掛け外し + レスポンス露出
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_week_sets_and_clears_cancelled_at(client, db) -> None:
    admin = await _make_user(db, "evc-1@example.com", "admin")
    staff = await _make_staff(db)
    ev = await _make_event(db, staff)

    res = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 200, res.text
    assert res.json()["cancelled_at"] is not None
    await db.refresh(ev)
    assert ev.cancelled_at is not None

    # 冪等 (二度押しでも状態は変わらない)
    again = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert again.status_code == 200
    assert again.json()["cancelled_at"] == res.json()["cancelled_at"]

    back = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": False}
    )
    assert back.status_code == 200, back.text
    assert back.json()["cancelled_at"] is None
    await db.refresh(ev)
    assert ev.cancelled_at is None


@pytest.mark.asyncio
async def test_week_list_returns_cancelled_rows_with_marker(client, db) -> None:
    """週一括取得 (FE useWeekStaffEvents = GET /staff/{id}/events) は cancelled も返す."""
    admin = await _make_user(db, "evc-2@example.com", "admin")
    staff = await _make_staff(db)
    ev = await _make_event(db, staff)
    await client.post(_cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True})

    res = await client.get(
        f"/api/v1/staff/{staff.id}/events?from={MONDAY}&to={MONDAY.replace(day=13)}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1  # 行は消えない (FE が打消線で描く)
    assert body[0]["cancelled_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fixed", "manual", "kaipoke"])
async def test_cancel_week_accepts_any_source(client, db, source: str) -> None:
    admin = await _make_user(db, f"evc-src-{source}@example.com", "admin")
    staff = await _make_staff(db)
    ev = await _make_event(
        db, staff, source=source, external_id=(f"e-{source}" if source != "manual" else None)
    )

    res = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 200, res.text
    await db.refresh(ev)
    assert ev.cancelled_at is not None
    assert ev.source == source  # 出所は失わない
    # 出所は EventRead にも露出する (FE の「全員（固定）」帯が source==='fixed' で絞る)
    assert res.json()["source"] == source
    assert res.json()["external_id"] == (None if source == "manual" else f"e-{source}")


# ---------------------------------------------------------------------------
# 4. RBAC / 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_week_forbidden_for_staff(client, db) -> None:
    staff = await _make_staff(db)
    user = await _make_user(db, "evc-3@example.com", "staff", staff_id=staff.id)
    ev = await _make_event(db, staff)

    res = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(user), json={"cancel": True}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cancel_week_404_for_unknown_event(client, db) -> None:
    import uuid

    admin = await _make_user(db, "evc-4@example.com", "admin")
    staff = await _make_staff(db)

    res = await client.post(
        _cancel_url(staff.id, uuid.uuid4()), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 5. 復活しないこと (固定イベント既定の展開)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_fixed_event_is_not_recreated_by_expand(client, db) -> None:
    """核心: 取消済みの固定イベントは次の週生成でも復活しない.

    削除ではなく取消印にしたのはこのため — 行が残る限り
    ``expand_staff_event_defaults`` の冪等キー (source='fixed' × external_id)
    が埋まっており、再展開は skip される。
    """
    admin = await _make_user(db, "evc-5@example.com", "admin")
    staff = await _make_staff(db)
    default = StaffEventDefault(
        staff_id=staff.id,
        weekday=2,  # 水曜
        start_time=time(9, 0),
        end_time=time(9, 15),
        title="朝会",
    )
    db.add(default)
    await db.commit()
    await db.refresh(default)

    created = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created == 1
    ev = await db.scalar(select(StaffEvent).where(StaffEvent.staff_id == staff.id))
    assert ev is not None

    res = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 200, res.text

    # 週生成をもう一度回しても増えない (= 朝会は今週外れたまま)
    created2 = await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created2 == 0
    total = await db.scalar(
        select(func.count()).select_from(StaffEvent).where(StaffEvent.staff_id == staff.id)
    )
    assert total == 1
    await db.refresh(ev)
    assert ev.cancelled_at is not None


# ---------------------------------------------------------------------------
# 6. 除外 (送信 / Layer3 / 提案エンジン)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_event_excluded_from_outbound_plan(client, db) -> None:
    admin = await _make_user(db, "evc-6@example.com", "admin")
    staff = await _make_staff(db)
    keep = await _make_event(db, staff, title="残す", start=time(13, 0), end=time(13, 30))
    drop = await _make_event(db, staff, title="外す")

    before = await build_outbound_plan(db, MONDAY)
    assert {i.event_id for i in before.items} == {keep.id, drop.id}

    res = await client.post(
        _cancel_url(staff.id, drop.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 200, res.text

    after = await build_outbound_plan(db, MONDAY)
    assert {i.event_id for i in after.items} == {keep.id}


@pytest.mark.asyncio
async def test_cancelled_event_excluded_from_layer3_and_proposals(client, db) -> None:
    admin = await _make_user(db, "evc-7@example.com", "admin")
    staff = await _make_staff(db)
    ev = await _make_event(db, staff)

    assigner = Layer3Assigner()
    loaded = await assigner._load_staff_events(db, staff_ids=[staff.id], week_monday=MONDAY)
    assert [e.id for e in loaded.get(staff.id, [])] == [ev.id]
    windows = await load_week_event_windows(db, staff_ids=[staff.id], week_monday=MONDAY)
    assert windows  # 水曜 (offset=2) に 1 枠

    res = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 200, res.text

    loaded_after = await assigner._load_staff_events(db, staff_ids=[staff.id], week_monday=MONDAY)
    assert loaded_after == {}
    windows_after = await load_week_event_windows(db, staff_ids=[staff.id], week_monday=MONDAY)
    assert windows_after == {}


@pytest.mark.asyncio
async def test_cancelled_promoted_fixed_event_is_not_recreated_by_expand(client, db) -> None:
    """⇧送信で source='kaipoke' へ昇格した固定イベントを取消しても復活しない.

    昇格すると external_id が RPA の複合キーへ差し替わるため、冪等キー
    (source='fixed' × '{default_id}:{date}') は空く。それでも
    ``expand_staff_event_defaults`` の **内容一致** 判定 (staff × 開始 × 終了 ×
    名称・source 不問) が拾うので再展開されない。

    既知制約: 昇格後に時刻や名称を編集すると内容一致も外れるため、
    その週の週生成で朝会が復活する (設計の既知制約)。
    """
    admin = await _make_user(db, "evc-8@example.com", "admin")
    staff = await _make_staff(db)
    default = StaffEventDefault(
        staff_id=staff.id,
        weekday=2,  # 水曜
        start_time=time(9, 0),
        end_time=time(9, 15),
        title="朝会",
    )
    db.add(default)
    await db.commit()
    await db.refresh(default)

    assert await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK) == 1
    await db.commit()
    ev = await db.scalar(select(StaffEvent).where(StaffEvent.staff_id == staff.id))
    assert ev is not None
    assert ev.source == "fixed"

    # ⇧送信の昇格 (events_outbound.promote_sent_events と同じ書込)
    ev.source = "kaipoke"
    ev.external_id = "98765:4321:2026-09-09"
    await db.commit()

    res = await client.post(
        _cancel_url(staff.id, ev.id), headers=_bearer(admin), json={"cancel": True}
    )
    assert res.status_code == 200, res.text

    assert await expand_staff_event_defaults(db, ISO_YEAR, ISO_WEEK) == 0
    await db.commit()
    total = await db.scalar(
        select(func.count()).select_from(StaffEvent).where(StaffEvent.staff_id == staff.id)
    )
    assert total == 1
