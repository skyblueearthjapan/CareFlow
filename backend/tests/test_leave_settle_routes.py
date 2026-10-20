"""休み設計 §4-2 の表の ○ の経路 (訪問を作る・担当を書く処理) が最後に後始末を呼ぶ.

* 表の ○ の 13 経路 (関数は 15。特別訪問の「置く・戻す」と申請の反映「臨時の訪問・振替」が
  2 つずつ) ＋ 患者ステータス連動の作り直し: 処理の本体に ``settle_leave_unassign(`` が
  あること (関数ごとに parametrize)。
* 動かして呼ばれることを確かめる (既存の試験の流れに後始末の見張りを付ける): 特別訪問の
  置く・戻す、週のコピー、申請の反映 (振替)、患者ステータス連動の作り直し (下)。
* 動かして確かめる経路: 週の生成・自動スタッフ割当・一斉未割当・週を空に (test_leave_engine)、
  コースの担当の変更・今週だけ動かす (test_leave_review_fixes)、申請の反映 (下)、
  訪問の作成・固定枠へ戻す (下)。共通の材料 = 午後休の職員と、その人の 14:00 の訪問。
"""

from __future__ import annotations

import importlib
import inspect
from datetime import date, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    Course,
    CourseTemplate,
    Office,
    Patient,
    PatientFixedVisit,
    Staff,
    StaffShift,
    User,
    Visit,
)
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override
from app.services.staff_leave import today_jst

#: (モジュール, 関数 (クラス.メソッド可))
SETTLE_ROUTES = [
    ("app.services.scheduling.layer1_expander", "Layer1Expander.expand_week"),
    ("app.services.scheduling.layer3_assignment", "Layer3Assigner.assign"),
    ("app.services.scheduling.auto_allocator_v2", "apply_week_only"),
    ("app.services.scheduling.auto_allocator_v2", "reset_visits_to_fixed"),
    ("app.services.scheduling.copy_week", "apply_copy_plan"),
    ("app.services.template_ws.commit", "week_apply"),
    ("app.api.v1.schedule", "place_and_fix"),
    ("app.api.v1.special_visits", "place_mark"),
    ("app.api.v1.special_visits", "restore_mark"),
    ("app.services.pending_request_applier", "_apply_visit_add_onetime"),
    ("app.services.pending_request_applier", "_apply_patient_reschedule"),
    ("app.api.v1.visits", "create_visit"),
    ("app.api.v1.schedule_v2", "_apply_visit_move_week_only"),
    ("app.api.v1.courses", "update_course"),
    ("app.services.scheduling.auto_allocator", "auto_allocate"),
    ("app.services.patient_status_sync", "_regenerate_from_fixed"),
]


@pytest.mark.parametrize(("module", "func"), SETTLE_ROUTES)
def test_route_calls_settle(module: str, func: str) -> None:
    obj = importlib.import_module(module)
    for part in func.split("."):
        obj = getattr(obj, part)
    src = inspect.getsource(obj)
    assert "settle_leave_unassign(" in src, f"{module}.{func} が後始末を呼んでいない"


# ---------------------------------------------------------------------------
# 共通の材料
# ---------------------------------------------------------------------------


def _future_monday() -> date:
    t = today_jst()
    return t + timedelta(days=7 - t.weekday() + 7)


async def _half_leave_staff(db, day: date) -> tuple[Office, Staff]:
    office = Office(name=f"経路拠点{uuid4().hex[:4]}", lat=35.6, lng=140.1)
    db.add(office)
    await db.flush()
    staff = Staff(
        name=f"午後休 {uuid4().hex[:4]}", role="staff", status="active", primary_office_id=office.id
    )
    db.add(staff)
    await db.flush()
    for wd in range(6):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    y, w, wd = iso_key(day)
    await set_staff_override(
        db, staff_id=staff.id, iso_year=y, iso_week=w, weekday=wd, override_type="pm_off"
    )
    await db.flush()
    return office, staff


async def _patient(db, office: Office) -> Patient:
    p = Patient(
        code=f"RT-{uuid4().hex[:6]}",
        name="経路患者",
        status="active",
        special_week_active=[],
        primary_office_id=office.id,
        lat=35.6,
        lng=140.1,
    )
    db.add(p)
    await db.flush()
    return p


def _assert_unassigned(v: Visit) -> None:
    assert v.primary_staff_id is None
    assert v.manual_staff_override is True


@pytest.mark.asyncio
async def test_create_visit_settles(client, db) -> None:
    day = _future_monday()
    office, staff = await _half_leave_staff(db, day)
    patient = await _patient(db, office)
    admin = User(
        email=f"rt-{uuid4().hex[:6]}@example.com", password_hash=hash_password("pw"), role="admin"
    )
    db.add(admin)
    await db.commit()
    token = create_access_token(subject=admin.id, role=admin.role, staff_id=None)
    res = await client.post(
        "/api/v1/visits",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "patient_id": str(patient.id),
            "primary_staff_id": str(staff.id),
            "visit_date": day.isoformat(),
            "start_time": "14:00",
            "end_time": "15:00",
            "type": "regular",
        },
    )
    assert res.status_code == 201, res.text
    v = await db.get(Visit, UUID(res.json()["id"]), populate_existing=True)
    assert v is not None
    _assert_unassigned(v)


@pytest.mark.asyncio
async def test_reset_visits_to_fixed_for_one_patient_settles(db) -> None:
    from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

    day = _future_monday()
    office, staff = await _half_leave_staff(db, day)
    patient = await _patient(db, office)
    tpl = CourseTemplate(office_id=office.id, label="A")
    db.add(tpl)
    await db.flush()
    y, w, wd = iso_key(day)
    db.add(
        Course(
            iso_year=y,
            iso_week=w,
            weekday=wd,
            code="A",
            office_id=office.id,
            template_id=tpl.id,
            course_status="staff_assigned",
            assigned_staff_id=staff.id,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=wd,
            start_time=time(14, 0),
            duration_min=60,
            course_template_id=tpl.id,
        )
    )
    await db.flush()
    await reset_visits_to_fixed(
        db, iso_year=y, iso_week=w, office_ids=[office.id], mode="auto", patient_id=patient.id
    )
    rows = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    ).all()
    hits = [v for v in rows if v.visit_date == day and v.start_time == time(14, 0)]
    assert hits, "固定枠から 14:00 の訪問ができる"
    for v in hits:
        _assert_unassigned(v)


@pytest.mark.asyncio
async def test_applier_onetime_visit_add_settles(db) -> None:
    """申請の反映 (臨時の訪問) で、休む人のコースへ入った訪問は担当なし."""
    from app.models import PendingRequest
    from app.services.pending_request_applier import PendingRequestApplier

    day = _future_monday()
    office, staff = await _half_leave_staff(db, day)
    patient = await _patient(db, office)
    tpl = CourseTemplate(office_id=office.id, label="A")
    db.add(tpl)
    await db.flush()
    y, w, wd = iso_key(day)
    course = Course(
        iso_year=y,
        iso_week=w,
        weekday=wd,
        code="A",
        office_id=office.id,
        template_id=tpl.id,
        course_status="staff_assigned",
        assigned_staff_id=staff.id,
    )
    db.add(course)
    requester = User(
        email=f"ap-{uuid4().hex[:6]}@example.com", password_hash=hash_password("pw"), role="admin"
    )
    db.add(requester)
    await db.flush()
    req = PendingRequest(
        requester_user_id=requester.id,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "scope": "one_time",
            "course_id": str(course.id),
            "iso_year": y,
            "iso_week": w,
            "weekday": wd,
            "proposed_visits": [{"weekday": wd, "start_time": "14:00", "duration_min": 60}],
        },
        target_patient_id=patient.id,
        target_date=day,
        status="pending",
        scope="one_time",
    )
    db.add(req)
    await db.flush()
    await PendingRequestApplier().apply(db, req)
    rows = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    ).all()
    assert rows
    for v in rows:
        _assert_unassigned(v)


# ---------------------------------------------------------------------------
# 動かして呼ばれることを確かめる (既存の試験の流れ + 後始末の見張り)
# ---------------------------------------------------------------------------


def _spy(monkeypatch, target_module: str) -> list[dict]:
    """``target_module.settle_leave_unassign`` を、呼び出しを控えて本物を呼ぶものに置き換える."""
    import app.services.staff_leave as staff_leave

    real = staff_leave.settle_leave_unassign
    calls: list[dict] = []

    async def spy(db, **kwargs):
        calls.append(kwargs)
        return await real(db, **kwargs)

    module = importlib.import_module(target_module)
    if hasattr(module, "settle_leave_unassign"):
        monkeypatch.setattr(module, "settle_leave_unassign", spy)
    # 関数の中で import する経路 (copy_week・patient_status_sync) は元の場所を置き換える。
    monkeypatch.setattr(staff_leave, "settle_leave_unassign", spy)
    return calls


@pytest.mark.asyncio
async def test_special_visit_place_calls_settle(client, db, monkeypatch) -> None:
    from tests.test_special_visit_week import test_place_creates_manual_week_visit

    calls = _spy(monkeypatch, "app.api.v1.special_visits")
    await test_place_creates_manual_week_visit(client, db)
    assert calls and all(c.get("dates") for c in calls)


@pytest.mark.asyncio
async def test_special_visit_restore_calls_settle(client, db, monkeypatch) -> None:
    from tests.test_special_visit_week import (
        test_displace_soft_deletes_visit_and_restore_brings_it_back as scenario,
    )

    calls = _spy(monkeypatch, "app.api.v1.special_visits")
    await scenario(client, db)
    assert calls and all(c.get("dates") for c in calls)


@pytest.mark.asyncio
async def test_copy_week_calls_settle(client, db, monkeypatch) -> None:
    from tests.test_copy_week import test_copy_never_copies_staff_and_regroups_pairs as scenario

    calls = _spy(monkeypatch, "app.services.scheduling.copy_week")
    await scenario(client, db)
    assert any(c.get("iso_week") for c in calls)


@pytest.mark.asyncio
async def test_patient_reschedule_calls_settle(db, monkeypatch) -> None:
    from tests.test_pending_request_applier import (
        test_apply_patient_reschedule_one_time_updates_visit_only as scenario,
    )

    calls = _spy(monkeypatch, "app.services.pending_request_applier")
    await scenario(db)
    assert calls and all(c.get("dates") for c in calls)


@pytest.mark.asyncio
async def test_patient_status_regenerate_calls_settle_once(db, monkeypatch) -> None:
    from tests.test_patient_status_sync import (
        test_reactivate_regenerates_from_fixed_visits as scenario,
    )

    calls = _spy(monkeypatch, "app.services.patient_status_sync")
    await scenario(db)
    # 作り直しの週ごとではなく最後に 1 回 (通知を 1 通にまとめる)
    assert len(calls) == 1 and calls[0].get("dates")
