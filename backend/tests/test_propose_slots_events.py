"""イベント考慮2段階提案 (PO確定 2026-07-27) — propose-slots API 統合テスト.

検証内容:
    - コース担当スタッフの staff_events がクリーン枠の探索から避けられる (パスA).
    - クリーン枠が無いときはイベント衝突枠が warnings='event_conflict' +
      event_conflicts 詳細つきで返る (パスB).
    - blocking=True のイベントの上にはフォールバックでも提案しない.
    - イベント無しスタッフ / 他曜日には影響しない (回帰).

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from datetime import datetime, time

import pytest

from app.models.staff import StaffEvent
from tests.test_propose_slots_api import (
    NEAR,
    WEEK_MONDAY,
    _base_payload,
    _bearer,
    _make_user,
    _seed_course,
    _seed_office_staff,
    _seed_patient,
    _seed_shift,
    _seed_visit,
)

URL = "/api/v1/schedule/v2/propose-slots"


async def _seed_event(
    db,
    *,
    staff,
    start: time,
    end: time,
    title: str = "会議",
    blocking: bool = False,
    day_offset: int = 0,
) -> StaffEvent:
    """当該週 (WEEK_MONDAY + day_offset) の staff_event を naive 壁時計で作る."""
    from datetime import timedelta

    day = WEEK_MONDAY + timedelta(days=day_offset)
    ev = StaffEvent(
        staff_id=staff.id,
        event_type="meeting",
        starts_at=datetime.combine(day, start),
        ends_at=datetime.combine(day, end),
        title=title,
        source="kaipoke",
        external_id=f"test:{staff.id}:{day.isoformat()}:{start.isoformat()}",
        blocking=blocking,
    )
    db.add(ev)
    await db.flush()
    return ev


@pytest.mark.asyncio
async def test_clean_slots_avoid_staff_event(client, db) -> None:
    """PM にイベント (13:00-15:00) → クリーン枠はバッファ込みで避けて出る."""
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    p = await _seed_patient(db, office=office, code="EXIST", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 30), end=time(10, 0))
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    await _seed_event(db, staff=staff, start=time(13, 0), end=time(15, 0), title="担当者会議")
    admin = await _make_user(db, email="ev-admin@example.com", role="admin")
    await db.commit()

    res = await client.post(URL, headers=_bearer(admin), json=_base_payload(office))
    assert res.status_code == 200, res.text
    slots = res.json()["slots"]
    assert slots, "AM / 15:15 以降にクリーン枠があるはず"
    for s in slots:
        # バッファ込み区間 12:45-15:15 と重ならない.
        assert not (s["start_time"] < "15:15" and s["end_time"] > "12:45"), (
            f"クリーン枠がイベント(12:45-15:15)と重なった: {s['start_time']}-{s['end_time']}"
        )
        assert s["event_conflicts"] == []
        assert "event_conflict" not in s["warnings"]


@pytest.mark.asyncio
async def test_fallback_slot_carries_conflict_warning(client, db) -> None:
    """PM 全体イベント + 午後希望候補 → フォールバック枠が警告+詳細つきで出る."""
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    # バケットは訪問から構築されるため AM に既存訪問 1 件が必要 (空コースはバケット無し).
    p = await _seed_patient(db, office=office, code="EXIST2", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 30), end=time(10, 0))
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    await _seed_event(db, staff=staff, start=time(13, 0), end=time(18, 0), title="長時間研修")
    admin = await _make_user(db, email="ev-admin2@example.com", role="admin")
    await db.commit()

    res = await client.post(
        URL, headers=_bearer(admin), json=_base_payload(office, time_type="午後")
    )
    assert res.status_code == 200, res.text
    slots = res.json()["slots"]
    assert slots, "フォールバック (イベント無視) の枠が出るはず"
    for s in slots:
        assert "event_conflict" in s["warnings"], s
        assert s["event_conflicts"], s
        assert s["event_conflicts"][0]["title"] == "長時間研修"
        assert s["event_conflicts"][0]["start"] == "13:00"
        assert s["event_conflicts"][0]["end"] == "18:00"


@pytest.mark.asyncio
async def test_blocking_event_suppresses_fallback(client, db) -> None:
    """blocking=True の PM 全体イベント + 午後希望 → 候補 0 件 (衝突提案を出さない)."""
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    p = await _seed_patient(db, office=office, code="EXIST3", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 30), end=time(10, 0))
    await _seed_shift(db, staff=staff, weekday=0, is_on=True)
    await _seed_event(
        db, staff=staff, start=time(13, 0), end=time(18, 0), title="重要会議", blocking=True
    )
    admin = await _make_user(db, email="ev-admin3@example.com", role="admin")
    await db.commit()

    res = await client.post(
        URL, headers=_bearer(admin), json=_base_payload(office, time_type="午後")
    )
    assert res.status_code == 200, res.text
    assert res.json()["slots"] == [], "blocking イベントの上に提案してはいけない"


@pytest.mark.asyncio
async def test_other_weekday_unaffected(client, db) -> None:
    """イベントは当該曜日のみに効く: 月曜イベントは火曜コースの候補に影響しない."""
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff, weekday=1, code="A")
    p = await _seed_patient(db, office=office, code="EXIST4", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 30), end=time(10, 0))
    await _seed_shift(db, staff=staff, weekday=1, is_on=True)
    # 月曜 (weekday=0) に終日イベント — 火曜コースには無関係.
    await _seed_event(db, staff=staff, start=time(9, 0), end=time(18, 0), day_offset=0)
    admin = await _make_user(db, email="ev-admin4@example.com", role="admin")
    await db.commit()

    res = await client.post(
        URL, headers=_bearer(admin), json=_base_payload(office, preferred_weekdays=["Tue"])
    )
    assert res.status_code == 200, res.text
    slots = res.json()["slots"]
    assert slots, "火曜コースには通常どおり候補が出る"
    for s in slots:
        assert s["event_conflicts"] == []
        assert "event_conflict" not in s["warnings"]
