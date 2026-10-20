"""休みの段 #3: 「明示の担当なし」(primary 空＋manual 真) の読み方をそろえる.

正典: docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md §3-2。
スマホ・打刻履歴・送信 CSV・モニターは既に守っている (既存の試験
``test_visit_course_fallback_visibility``・``test_visit_history``・``test_kaipoke_csv_builder``・
``test_visit_monitor``)。ここでは今回変えた箇所と 1 件ずつの付け替え (中2) を確かめる。
"""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.agent import _visit_dict
from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffWeeklyOverride, User, Visit
from app.services.leave_calendar import iso_key
from app.services.scheduling.substitute_candidates import _owner_of

_URL = "/api/v1/schedule/v2/visit-assign-staff-week"
_DAY = date(2026, 9, 4)  # 金曜


def _visit_ns(*, primary=None, manual=False, course_id=None):
    return SimpleNamespace(
        id=uuid4(),
        patient_id=uuid4(),
        visit_date=_DAY,
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        required_staff_count=1,
        visit_group_id=None,
        status="planned",
        source="auto",
        primary_staff_id=primary,
        secondary_staff_id=None,
        mentor_staff_id=None,
        course_id=course_id,
        manual_staff_override=manual,
        kaipoke_id=None,
        week_pinned=False,
        is_unplanned=False,
        note=None,
    )


def test_owner_of_explicit_unassigned_is_none() -> None:
    course = SimpleNamespace(id=uuid4(), assigned_staff_id=uuid4())
    assert _owner_of(_visit_ns(manual=True), course) is None  # type: ignore[arg-type]
    assert _owner_of(_visit_ns(manual=False), course) == course.assigned_staff_id  # type: ignore[arg-type]


def test_agent_effective_staff_respects_manual() -> None:
    course = SimpleNamespace(id=uuid4(), assigned_staff_id=uuid4(), code="A")
    got = _visit_dict(_visit_ns(manual=True, course_id=course.id), course, None, "患者")  # type: ignore[arg-type]
    assert got["effective_staff_id"] is None
    got2 = _visit_dict(_visit_ns(manual=False, course_id=course.id), course, None, "患者")  # type: ignore[arg-type]
    assert got2["effective_staff_id"] == str(course.assigned_staff_id)


# ---------------------------------------------------------------------------
# 1 件ずつの付け替え (中2)
# ---------------------------------------------------------------------------


async def _admin(db) -> dict[str, str]:
    user = User(
        email=f"eu-{uuid4().hex[:6]}@example.com", password_hash=hash_password("pw"), role="admin"
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(subject=user.id, role=user.role, staff_id=None)
    return {"Authorization": f"Bearer {token}"}


async def _seed(db, *, leave_type: str | None, start: time = time(10, 0)):
    office = Office(name="拠点")
    staff = Staff(name="休む 花子")
    other = Staff(name="代わり 太郎")
    patient = Patient(
        code=f"EU-{uuid4().hex[:6]}", name="患者", status="active", special_week_active=[]
    )
    db.add_all([office, staff, other, patient])
    await db.flush()
    y, w, wd = iso_key(_DAY)
    course = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="A",
        office_id=office.id,
        course_status="staff_assigned",
        assigned_staff_id=staff.id,
    )
    db.add(course)
    await db.flush()
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=other.id,
        course_id=course.id,
        visit_date=_DAY,
        start_time=start,
        end_time=time(start.hour + 1, start.minute),
        type="regular",
        status="planned",
        source="auto",
        manual_staff_override=True,
    )
    db.add(visit)
    if leave_type is not None:
        db.add(
            StaffWeeklyOverride(
                staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type=leave_type
            )
        )
    await db.commit()
    await db.refresh(visit)
    return staff, other, visit


@pytest.mark.asyncio
async def test_unassign_keeps_manual_when_course_staff_on_leave(client, db) -> None:
    headers = await _admin(db)
    _staff, _other, visit = await _seed(db, leave_type="am_off", start=time(10, 0))
    res = await client.post(
        _URL, headers=headers, json={"visit_id": str(visit.id), "staff_id": None}
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.primary_staff_id is None
    assert visit.manual_staff_override is True


@pytest.mark.asyncio
async def test_unassign_outside_leave_window_clears_manual(client, db) -> None:
    headers = await _admin(db)
    # 午前休で 13:00 開始の訪問は休みの時間帯の外 → 今までどおり manual=False
    _staff, _other, visit = await _seed(db, leave_type="am_off", start=time(13, 0))
    res = await client.post(
        _URL, headers=headers, json={"visit_id": str(visit.id), "staff_id": None}
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.manual_staff_override is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("leave_type", "start", "blocked"),
    [
        ("off", time(10, 0), True),
        ("paid_off", time(15, 0), True),
        ("pm_off", time(12, 0), True),  # 12:00〜13:00 は 12:30 より後に終わる
        ("pm_off", time(11, 0), False),  # 11:00〜12:00 は午後休の外
        ("custom_time", time(10, 0), False),
    ],
)
async def test_assign_to_staff_on_leave_is_422(client, db, leave_type, start, blocked) -> None:
    headers = await _admin(db)
    staff, other, visit = await _seed(db, leave_type=leave_type, start=start)
    # 一度 other から外し、休んでいる staff へ付け替える
    res = await client.post(
        _URL, headers=headers, json={"visit_id": str(visit.id), "staff_id": str(staff.id)}
    )
    if blocked:
        assert res.status_code == 422, res.text
        assert "休む 花子さんはこの時間は休みです" in res.text
    else:
        assert res.status_code == 200, res.text
    assert other is not None


@pytest.mark.asyncio
async def test_feasibility_items_put_explicit_unassigned_on_unassigned_row(db) -> None:
    from app.services.scheduling.feasibility_check import UNASSIGNED_STAFF_KEY, load_week_items

    staff, _other, visit = await _seed(db, leave_type="am_off", start=time(10, 0))
    visit.primary_staff_id = None  # 明示の担当なし (manual は真のまま)
    await db.commit()
    y, w, _ = iso_key(_DAY)
    monday = date.fromisocalendar(y, w, 1)
    items, _names = await load_week_items(db, week_start=monday, week_end=_DAY)
    assert (UNASSIGNED_STAFF_KEY, _DAY) in items
    assert (str(staff.id), _DAY) not in items
