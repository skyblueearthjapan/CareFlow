"""W-2 テスト: POST /api/v1/schedule/v2/pool-bulk-apply.

プール一括投入 (simulate) の placements を **1 トランザクション**で固定訪問週間 (PFV) に
登録し、今週 visits を再生成する apply endpoint. 反映先は pattern_and_week 固定 (D-2).

検証内容:
    1. 正常系: simulate → apply → PFV が増え今週 visits が再生成される.
       applied_patients / applied_slots が正しい.
    2. 409: apply 前に visit を動かすと state_token 不一致で 409.
    3. 422 + 全体 rollback: 2 人目の V2 pinned 違反で 1 人目も含め全部戻る (all-or-nothing).
    4. warnings: V5 容量超過気味の配置は warnings で通る (ブロックしない).
    5. op_log 非汚染: apply は schedule_op_log に記録せず undo API が拾わない + AuditLog に記録.
    6. RBAC: staff 403 / no-auth 401.

ローカル SQLite のみ (本番 DB 禁止). fixture は test_pool_bulk_simulate.py と整合.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.audit_log import AuditLog
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.schedule_op_log import ScheduleOpLog
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.pool_bulk_inserter import compute_bulk_state_token

ISO_YEAR = 2026
ISO_WEEK = 21
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # Mon

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)

SIMULATE_URL = "/api/v1/schedule/v2/pool-bulk-simulate"
APPLY_URL = "/api/v1/schedule/v2/pool-bulk-apply"


# ---------------------------------------------------------------------------
# Helpers (test_pool_bulk_simulate.py と同一パターン)
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(
    db, *, name: str = "稲", code: str | None = "INAGE"
) -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_patient(
    db,
    *,
    office: Office,
    code: str,
    lat: float | None,
    lng: float | None,
    name: str | None = None,
    weekly_pattern: dict | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(
    db, *, office: Office, staff: Staff | None, weekday: int = 0, code: str = "A"
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(db, *, patient: Patient, course: Course, start: time, end: time) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(visit)
    await db.flush()
    return visit


async def _seed_anchor_course(
    db, *, office: Office, staff: Staff, weekday: int = 0, code: str = "A", anchor_xy=NEAR
) -> Course:
    """指定曜日に「近接の既存訪問 1 件だけ」の開講コースを作る (空き枠が出る状態)."""
    course = await _seed_course(db, office=office, staff=staff, weekday=weekday, code=code)
    anchor = await _seed_patient(
        db,
        office=office,
        code=f"ANC{weekday}{code}{uuid4().hex[:4]}",
        lat=anchor_xy[0],
        lng=anchor_xy[1],
    )
    await _seed_visit(db, patient=anchor, course=course, start=time(9, 30), end=time(10, 0))
    return course


def _pool_wp(**overrides: Any) -> dict[str, Any]:
    """propose-slots が受ける weekly_pattern サマリ形式 (既定: Mon 終日 30分 週1回)."""
    wp: dict[str, Any] = {
        "service_minutes": 30,
        "time_type": "終日",
        "preferred_weekdays": ["Mon"],
        "frequency_per_week": 1,
    }
    wp.update(overrides)
    return wp


def _simulate_payload(office: Office, patient_ids: list[str]) -> dict[str, Any]:
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_id": str(office.id),
        "patient_ids": patient_ids,
    }


def _apply_payload(office: Office, placements: list[dict], state_token: str) -> dict[str, Any]:
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_id": str(office.id),
        "placements": placements,
        "state_token": state_token,
    }


def _placement(
    patient: Patient,
    office: Office,
    *,
    weekday: int,
    seq: int = 1,
    course_code: str = "A",
    start_time: str = "09:00:00",
    service_minutes: int = 30,
) -> dict[str, Any]:
    """simulate 出力と同形の PoolBulkPlacement dict を手組みする."""
    return {
        "seq": seq,
        "patient_id": str(patient.id),
        "patient_name": patient.name,
        "weekday": weekday,
        "course_code": course_code,
        "office_id": str(office.id),
        "start_time": start_time,
        "service_minutes": service_minutes,
        "delta_minutes": None,
        "warnings": [],
    }


async def _normal_pfvs(db, patient_id) -> list[PatientFixedVisit]:
    return list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == patient_id,
                    PatientFixedVisit.mode == "normal",
                )
            )
        ).all()
    )


async def _active_visits(db, patient_id) -> list[Visit]:
    return list(
        (
            await db.scalars(
                select(Visit).where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 1. 正常系: simulate → apply → PFV 増加 + 今週 visits 再生成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_happy_path(client, db) -> None:
    admin = await _make_user(db, email="pba-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲A1", code="A1OFF")
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    wp = _pool_wp()  # Mon 週1.
    p = await _seed_patient(
        db, office=office, code="HP", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    sim = await client.post(
        SIMULATE_URL, headers=_bearer(admin), json=_simulate_payload(office, [str(p.id)])
    )
    assert sim.status_code == 200, sim.text
    sim_body = sim.json()
    placements = sim_body["placements"]
    assert len(placements) == 1, sim_body
    state_token = sim_body["state_token"]

    res = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, state_token)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_patients"] == 1, body
    assert body["applied_slots"] == 1, body

    # PFV が増えた (Mon slot0 が 1 件).
    pfvs = await _normal_pfvs(db, p.id)
    assert len(pfvs) == 1, pfvs
    assert pfvs[0].weekday == 0 and pfvs[0].slot_index == 0

    # 今週 visits が再生成された (Mon に active visit).
    visits = await _active_visits(db, p.id)
    assert any(v.visit_date == WEEK_MONDAY for v in visits), visits


# ---------------------------------------------------------------------------
# 2. 409: apply 前に visit を動かすと state_token 不一致
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_state_token_conflict_409(client, db) -> None:
    """simulate 後にスケジュールが変わると (= 1 度 apply して PFV/visits が動くと)、
    同じ古い state_token での再 apply は 409 になる (楽観ロック)."""
    admin = await _make_user(db, email="pba-admin2@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲409", code="C409OFF")
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    wp = _pool_wp()
    p = await _seed_patient(
        db, office=office, code="C409", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    sim = await client.post(
        SIMULATE_URL, headers=_bearer(admin), json=_simulate_payload(office, [str(p.id)])
    )
    assert sim.status_code == 200, sim.text
    sim_body = sim.json()
    stale_token = sim_body["state_token"]
    placements = sim_body["placements"]

    # 1 回目の apply は成功 → PFV 追加 + 今週 visits 再生成でスケジュールが変わる.
    first = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, stale_token)
    )
    assert first.status_code == 200, first.text

    # 同じ (今や古い) state_token での 2 回目は 409 (recompute が不一致).
    res = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, stale_token)
    )
    assert res.status_code == 409, res.text
    assert "再計算" in res.text


# ---------------------------------------------------------------------------
# 3. 422 + 全体 rollback (2 人目の pinned 違反で 1 人目も戻る = all-or-nothing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_pinned_violation_rolls_back_all(client, db) -> None:
    admin = await _make_user(db, email="pba-admin3@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲PIN", code="PINOFF")
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    # p1: clean pool patient. p2: 既存 pinned PFV (Fri) を持つ → apply-individual が 422.
    p1 = await _seed_patient(
        db, office=office, code="PIN1", lat=BASE[0], lng=BASE[1], weekly_pattern=_pool_wp()
    )
    p2 = await _seed_patient(
        db, office=office, code="PIN2", lat=NEAR[0], lng=NEAR[1], weekly_pattern=_pool_wp()
    )
    pinned = PatientFixedVisit(
        patient_id=p2.id,
        mode="normal",
        weekday=4,
        start_time=time(9, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=True,
    )
    db.add(pinned)
    await db.commit()

    token = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    # p1 を先に処理 (成功) → p2 で pinned 違反 (422). placements 順で by_patient の順序が決まる.
    placements = [
        _placement(p1, office, weekday=0, seq=1),
        _placement(p2, office, weekday=0, seq=2),
    ]

    res = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, token)
    )
    assert res.status_code == 422, res.text

    # 全体 rollback: p1 の PFV は作られていない (all-or-nothing).
    assert await _normal_pfvs(db, p1.id) == []
    # p2 は元の pinned Fri PFV のみ (Mon は追加されていない).
    p2_pfvs = await _normal_pfvs(db, p2.id)
    assert len(p2_pfvs) == 1 and p2_pfvs[0].weekday == 4, p2_pfvs


# ---------------------------------------------------------------------------
# 4. warnings: V5 容量超過気味の配置は warnings で通る (200)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_capacity_warning_passes(client, db) -> None:
    """同住所の他患者が長時間 PFV を持つコースに追加投入 → V5 総所要超過 warning で通る."""
    admin = await _make_user(db, email="pba-admin4@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲CAP", code="CAPOFF")
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=BASE)
    # 同住所 (BASE) の他患者: Mon に 470 分の PFV (course 未指定 = None バケット).
    heavy = await _seed_patient(db, office=office, code="HEAVY", lat=BASE[0], lng=BASE[1])
    db.add(
        PatientFixedVisit(
            patient_id=heavy.id,
            mode="normal",
            weekday=0,
            start_time=time(8, 0),
            duration_min=470,
            slot_index=0,
        )
    )
    # 投入対象: 同住所 (BASE) の pool 患者 Mon 週1.
    p = await _seed_patient(
        db, office=office, code="CAP", lat=BASE[0], lng=BASE[1], weekly_pattern=_pool_wp()
    )
    await db.commit()

    token = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    placements = [_placement(p, office, weekday=0, seq=1, start_time="09:00:00")]

    res = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, token)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_patients"] == 1, body
    # 容量超過気味の警告が収集される (ブロックはしない).
    assert body["warnings"], body
    assert any("上限" in w for w in body["warnings"]), body["warnings"]
    # PFV は登録済み.
    assert len(await _normal_pfvs(db, p.id)) == 1


# ---------------------------------------------------------------------------
# 5. op_log 非汚染 + AuditLog 記録
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_not_recorded_in_op_log(client, db) -> None:
    """apply は undo 対象外 — schedule_op_log に記録せず undo state が拾わない.
    代わりに AuditLog (action=pool_bulk_apply) に監査記録される."""
    admin = await _make_user(db, email="pba-admin5@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲OPL", code="OPLOFF")
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    p = await _seed_patient(
        db, office=office, code="OPL", lat=BASE[0], lng=BASE[1], weekly_pattern=_pool_wp()
    )
    await db.commit()

    token = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    placements = [_placement(p, office, weekday=0, seq=1)]
    res = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, token)
    )
    assert res.status_code == 200, res.text

    # undo state: この admin のその週に undo 対象が無い (schedule_op_log 未記録).
    state = await client.get(
        "/api/v1/schedule/v2/op-log/state",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert state.status_code == 200, state.text
    assert state.json()["can_undo"] is False, state.json()

    # schedule_op_log に一括投入の行が無い.
    op_rows = (
        await db.scalars(select(ScheduleOpLog).where(ScheduleOpLog.user_id == admin.id))
    ).all()
    assert list(op_rows) == [], op_rows

    # AuditLog に pool_bulk_apply の監査行がある.
    audit = (await db.scalars(select(AuditLog).where(AuditLog.action == "pool_bulk_apply"))).all()
    assert len(list(audit)) == 1, "AuditLog に pool_bulk_apply 監査が記録されていない"


# ---------------------------------------------------------------------------
# 6. RBAC (staff 403 / no-auth 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="pba-staff@example.com", role="staff")
    res = await client.post(
        APPLY_URL,
        headers=_bearer(staff_user),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(uuid4()),
            "placements": [],
            "state_token": "x" * 64,
        },
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_apply_rejects_no_auth(client, db) -> None:
    res = await client.post(
        APPLY_URL,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(uuid4()),
            "placements": [],
            "state_token": "x" * 64,
        },
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# 7. union 保持: 既存 PFV が残り、新 placement だけ追加される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_preserves_existing_pfv_union(client, db) -> None:
    """患者が weekday=4 (Fri) の既存 PFV を持つ状態で weekday=0 (Mon) を apply する.

    期待: apply 後に weekday=0 と weekday=4 の両 PFV が存在し、
    weekday=4 の start_time / duration_min が不変 (union セマンティクス).
    """
    admin = await _make_user(db, email="pba-admin7@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲UNI", code="UNIOFF")
    # Mon (0) 用コースを作成
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)

    p = await _seed_patient(
        db, office=office, code="UNI", lat=BASE[0], lng=BASE[1], weekly_pattern=_pool_wp()
    )
    # 既存 PFV: Fri (weekday=4), non-pinned
    existing_start = time(14, 0)
    existing_duration = 45
    existing_pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=4,
        start_time=existing_start,
        duration_min=existing_duration,
        slot_index=0,
        is_pinned=False,
    )
    db.add(existing_pfv)
    await db.commit()

    token = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    # Mon placement のみ送信 (Fri PFV は送らない)
    placements = [
        _placement(p, office, weekday=0, seq=1, start_time="09:00:00", service_minutes=30)
    ]

    res = await client.post(
        APPLY_URL, headers=_bearer(admin), json=_apply_payload(office, placements, token)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied_patients"] == 1, body
    assert body["applied_slots"] == 1, body

    # 適用後: weekday=0 と weekday=4 の両方が存在する.
    pfvs = await _normal_pfvs(db, p.id)
    weekdays = {pfv.weekday for pfv in pfvs}
    assert weekdays == {0, 4}, f"期待 {{0,4}}、実際 {weekdays}"

    # weekday=4 の内容 (時刻・duration) が不変.
    fri_pfv = next(pfv for pfv in pfvs if pfv.weekday == 4)
    assert fri_pfv.start_time == existing_start, fri_pfv.start_time
    assert fri_pfv.duration_min == existing_duration, fri_pfv.duration_min
