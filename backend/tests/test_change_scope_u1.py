"""Wave U-1 (変更反映先の統一 §2.2 / PO 決定 D-1/D-2) の BE 契約テスト.

対象:
  1. PUT /patients/{id}/fixed-visits + change_scope=pattern_and_week
     → PFV 置換 + 今週 visits 再生成 (week_sync 件数) / 省略時は従来どおり visits 不変
  2. pattern_and_week で iso_year/iso_week 欠落 → 422
  3. POST /schedule/place-and-fix fix_pattern=false → source='manual_week' / true は 'manual'
  4. POST /v2/scope-optimization/apply change_scope=pattern_and_week
     → PFV 移動 + 影響患者のみ今週再生成 (無関係患者の visits 不変)
  5. 同 apply change_scope=week_only
     → PFV 完全不変 / 今週 visits が移動先へ変化 / source='manual_week' / 週生成・固定枠戻で温存

設計書: docs/plans/change-scope-unification-design.md
Backend で APP_ENV=test ガード済み (conftest.py).
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.user import User
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

# ISO 2026 W20 = 2026-05-11 (Mon) .. 2026-05-17 (Sun)
ISO_YEAR = 2026
ISO_WEEK = 20
MON = date(2026, 5, 11)

BASE = (35.6000, 140.1000)
FAR = (35.6300, 140.1400)

_SCOPE_SIM = "/api/v1/schedule/v2/scope-optimization/simulate"
_SCOPE_APPLY = "/api/v1/schedule/v2/scope-optimization/apply"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, name: str) -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_patient(
    db, code: str, *, office_id=None, lat: float = 35.65, lng: float = 140.1
) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        special_week_active=[],
        lat=lat,
        lng=lng,
        primary_office_id=office_id,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db, *, patient, visit_date, start, end, source="auto", course_id=None
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source=source,
        required_staff_count=1,
        course_id=course_id,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _make_pfv(
    db, *, patient, weekday, start, duration_min=30, movability="unknown"
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=duration_min,
        slot_index=0,
        movability=movability,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


async def _make_template(db, office_id, label: str = "A") -> CourseTemplate:
    tpl = CourseTemplate(
        office_id=office_id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
        capacity_sun=0,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def _active_visits(db, patient_id):
    return (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
        )
    ).all()


async def _one_pfv(db, patient_id) -> PatientFixedVisit:
    return (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).one()


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


# ---------------------------------------------------------------------------
# 1) PUT fixed-visits change_scope=pattern_and_week
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_fixed_visits_pattern_and_week_regenerates(client, db) -> None:
    """A: PFV を Mon 10:00 に置換 → 今週 visits も Mon 10:00 に再生成 (week_sync)."""
    admin = await _make_user(db, email="u1-put-a@example.com", role="admin")
    office = await _make_office(db, "u1-put-a-office")
    p = await _make_patient(db, "U1-PUT-A", office_id=office.id)
    # 既存 PFV Mon 09:00 + 既存の今週 auto visit Mon 09:00.
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.put(
        f"/api/v1/patients/{p.id}/fixed-visits",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [{"weekday": 0, "start_time": "10:00:00", "duration_min": 30}],
            "change_scope": "pattern_and_week",
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_sync"] is not None
    assert body["week_sync"]["visits_regenerated"] >= 1

    # PFV は Mon 10:00 に置換.
    pfv = await _one_pfv(db, p.id)
    assert pfv.weekday == 0 and pfv.start_time == time(10, 0)

    # 今週 visits も Mon 10:00 に再生成 (旧 09:00 auto は消え、10:00 が現れる).
    visits = await _active_visits(db, p.id)
    starts = {v.start_time for v in visits}
    assert time(10, 0) in starts, f"Mon 10:00 の visit が再生成されるはず: {visits}"
    assert time(9, 0) not in starts, "旧 09:00 の auto visit は soft-delete される"


@pytest.mark.asyncio
async def test_put_fixed_visits_pattern_only_leaves_visits_untouched(client, db) -> None:
    """省略時 (pattern_only): PFV は置換されるが今週 visits は不変 (week_sync=null)."""
    admin = await _make_user(db, email="u1-put-b@example.com", role="admin")
    office = await _make_office(db, "u1-put-b-office")
    p = await _make_patient(db, "U1-PUT-B", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    existing = await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.put(
        f"/api/v1/patients/{p.id}/fixed-visits",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [{"weekday": 0, "start_time": "10:00:00", "duration_min": 30}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["week_sync"] is None

    # 今週 visits は不変 (旧 09:00 のまま).
    visits = await _active_visits(db, p.id)
    assert [v.id for v in visits] == [existing.id]
    assert visits[0].start_time == time(9, 0)


@pytest.mark.asyncio
async def test_put_fixed_visits_pattern_and_week_missing_iso_422(client, db) -> None:
    """pattern_and_week で iso_year/iso_week 欠落 → 422 (破壊的変更前に弾く)."""
    admin = await _make_user(db, email="u1-put-422@example.com", role="admin")
    office = await _make_office(db, "u1-put-422-office")
    p = await _make_patient(db, "U1-PUT-422", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))

    res = await client.put(
        f"/api/v1/patients/{p.id}/fixed-visits",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [{"weekday": 0, "start_time": "10:00:00", "duration_min": 30}],
            "change_scope": "pattern_and_week",
        },
    )
    assert res.status_code == 422, res.text

    # PFV は変更されていない (Mon 09:00 のまま).
    pfv = await _one_pfv(db, p.id)
    assert pfv.start_time == time(9, 0)


# ---------------------------------------------------------------------------
# 3) place-and-fix fix_pattern=false → source='manual_week'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_week_only_uses_manual_week_source(client, db) -> None:
    """B: fix_pattern=false → visit.source='manual_week' で作成、PFV は作られない."""
    admin = await _make_user(db, email="u1-paf-false@example.com", role="admin")
    office = await _make_office(db, "u1-paf-false-office")
    p = await _make_patient(db, "U1-PAF-F", office_id=office.id)
    tpl = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "course_template_id": str(tpl.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 0,
            "start_time": "10:00:00",
            "duration_min": 30,
            "staff_count": 1,
            "fix_pattern": False,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["visit"]["source"] == VISIT_SOURCE_MANUAL_WEEK
    assert data["fixed_visit"] is None

    # PFV は 1 行も作られない (この週だけ配置).
    pfvs = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    assert pfvs == []


@pytest.mark.asyncio
async def test_place_and_fix_fix_pattern_true_keeps_manual_source(client, db) -> None:
    """回帰: fix_pattern=true は従来どおり source='manual' + PFV 作成."""
    admin = await _make_user(db, email="u1-paf-true@example.com", role="admin")
    office = await _make_office(db, "u1-paf-true-office")
    p = await _make_patient(db, "U1-PAF-T", office_id=office.id)
    tpl = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "course_template_id": str(tpl.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 0,
            "start_time": "10:00:00",
            "duration_min": 30,
            "staff_count": 1,
            "fix_pattern": True,
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["visit"]["source"] == "manual"
    pfvs = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    assert len(pfvs) == 1


# ---------------------------------------------------------------------------
# scope-optimization apply の共通シード (sandwich: FAR — P(BASE) — FAR)
# ---------------------------------------------------------------------------


async def _seed_sandwich_office(db) -> tuple[Office, Patient, Patient, Patient, Course]:
    office = Office(name="稲", code=f"INA{uuid4().hex[:4]}")
    db.add(office)
    await db.flush()
    staff = Staff(name="S1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))

    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    def _patient(code: str, lat: float, lng: float) -> Patient:
        return Patient(
            code=code,
            name=f"P-{code}",
            status="active",
            lat=lat,
            lng=lng,
            primary_office_id=office.id,
        )

    p = _patient(f"TGT{uuid4().hex[:4]}", *BASE)
    fa1 = _patient(f"FA1{uuid4().hex[:4]}", *FAR)
    fa2 = _patient(f"FA2{uuid4().hex[:4]}", *FAR)
    db.add_all([p, fa1, fa2])
    await db.flush()

    def _visit(patient: Patient, start: time, end: time) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=MON,
            start_time=start,
            end_time=end,
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
            primary_staff_id=staff.id,
        )

    db.add_all(
        [
            _visit(fa1, time(9, 30), time(10, 0)),
            _visit(p, time(10, 30), time(11, 0)),
            _visit(fa2, time(11, 15), time(11, 45)),
        ]
    )
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            slot_index=0,
            start_time=time(10, 30),
            duration_min=30,
            movability="time_flexible",
        )
    )
    await db.commit()
    return office, p, fa1, fa2, course


def _apply_body(office, sim_data, *, change_scope: str) -> dict:
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(office.id), "weekdays": [0], "course_codes": ["A"]},
        "state_token": sim_data["state_token"],
        "steps": sim_data["steps"],
        "change_scope": change_scope,
    }


def _sim_body(office) -> dict:
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(office.id), "weekdays": [0], "course_codes": ["A"]},
    }


# ---------------------------------------------------------------------------
# 4) scope-optimization apply pattern_and_week
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_apply_pattern_and_week_regenerates_affected_only(client, db) -> None:
    """A: PFV 移動 + 影響患者 (TGT) のみ今週再生成. 無関係患者 (FA1/FA2) の visits は不変."""
    admin = await _make_user(db, email="u1-scope-a@example.com", role="admin")
    office, p, fa1, fa2, _course = await _seed_sandwich_office(db)

    sim = await client.post(_SCOPE_SIM, headers=_bearer(admin), json=_sim_body(office))
    assert sim.status_code == 200, sim.text
    sim_data = sim.json()
    assert sim_data["steps"], sim_data["excluded_summary"]
    cand = sim_data["steps"][0]["suggestion"]["candidate"]

    fa1_before = {v.id: v.start_time for v in await _active_visits(db, fa1.id)}
    fa2_before = {v.id: v.start_time for v in await _active_visits(db, fa2.id)}

    res = await client.post(
        _SCOPE_APPLY,
        headers=_bearer(admin),
        json=_apply_body(office, sim_data, change_scope="pattern_and_week"),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["change_scope"] == "pattern_and_week"
    assert data["week_sync"] is not None
    assert data["week_sync"]["patients"] == 1
    assert data["week_sync"]["visits_regenerated"] >= 1

    # PFV は候補位置へ移動.
    pfv = await _one_pfv(db, p.id)
    assert _hhmm(pfv.start_time) == cand["start_time"]

    # TGT の今週 visit は候補位置へ再生成された.
    tgt_visits = await _active_visits(db, p.id)
    assert any(_hhmm(v.start_time) == cand["start_time"] for v in tgt_visits), tgt_visits

    # 無関係患者 (FA1/FA2) の visits は完全に不変.
    fa1_after = {v.id: v.start_time for v in await _active_visits(db, fa1.id)}
    fa2_after = {v.id: v.start_time for v in await _active_visits(db, fa2.id)}
    assert fa1_after == fa1_before
    assert fa2_after == fa2_before


# ---------------------------------------------------------------------------
# 5) scope-optimization apply week_only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_apply_week_only_moves_visits_and_keeps_pfv(client, db) -> None:
    """B: PFV 完全不変 / 今週 visit が候補位置へ変化 / source='manual_week' / reset で温存."""
    admin = await _make_user(db, email="u1-scope-b@example.com", role="admin")
    office, p, fa1, fa2, _course = await _seed_sandwich_office(db)

    sim = await client.post(_SCOPE_SIM, headers=_bearer(admin), json=_sim_body(office))
    assert sim.status_code == 200, sim.text
    sim_data = sim.json()
    assert sim_data["steps"], sim_data["excluded_summary"]
    cand = sim_data["steps"][0]["suggestion"]["candidate"]

    res = await client.post(
        _SCOPE_APPLY,
        headers=_bearer(admin),
        json=_apply_body(office, sim_data, change_scope="week_only"),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["change_scope"] == "week_only"
    assert data["week_sync"] is not None
    assert data["week_sync"]["patients"] == 1
    assert data["week_sync"]["visits_regenerated"] >= 1

    # PFV は完全に不変 (Mon 10:30 のまま).
    pfv = await _one_pfv(db, p.id)
    assert pfv.weekday == 0 and pfv.start_time == time(10, 30)

    # 今週 visit は候補位置へ移動し source='manual_week'.
    tgt_visits = await _active_visits(db, p.id)
    moved = [v for v in tgt_visits if _hhmm(v.start_time) == cand["start_time"]]
    assert len(moved) == 1, tgt_visits
    assert moved[0].source == VISIT_SOURCE_MANUAL_WEEK
    moved_id = moved[0].id

    # 固定枠戻 (reset-to-fixed) を実行しても manual_week visit は温存される (U-0 保護).
    await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], patient_id=p.id
    )
    await db.commit()
    survivors = [v.id for v in await _active_visits(db, p.id)]
    assert moved_id in survivors, "manual_week visit は固定枠戻で消えない"
