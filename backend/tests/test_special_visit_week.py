"""特別訪問週間 (special visit week) のテスト — 設計 §1〜§5.

検証内容:
    - 期間 CRUD + 同一患者 active 重複は 422.
    - カレンダー: 生成済み週 = 実 visit / 未生成週 = PFV 投影 / 週合計 + target_met.
    - ○ (extra) マークの追加 / 取消 / 同セル 409.
    - 退避 (displaced): visit soft-delete + snapshot → restore で復元.
    - 配置済み退避の restore は force 必須 (409).
    - place: visit 作成 (source='manual_week') + マーク placed.
    - プール一覧 + 自己回復 (配置済みだが訪問が消えた → pool 扱い).
    - Layer1: displaced マークのある曜日は PFV 展開されない.

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.staff import Staff
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.layer1_expander import Layer1Expander

ISO_YEAR = 2026
ISO_WEEK = 20
NEXT_ISO_WEEK = 21
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)
NEXT_MONDAY = date.fromisocalendar(ISO_YEAR, NEXT_ISO_WEEK, 1)  # 2026-05-18 (Mon)

# 2 週間 (ISO 週 20 + 21) を覆う期間.
PERIOD_START = WEEK_MONDAY
PERIOD_END = WEEK_MONDAY + timedelta(days=13)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(db) -> tuple[Office, Staff]:
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_template(db, *, office: Office, label: str = "A") -> CourseTemplate:
    template = CourseTemplate(
        office_id=office.id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
    )
    db.add(template)
    await db.flush()
    return template


async def _seed_patient(db, *, office: Office | None = None, code: str = "SVW1") -> Patient:
    patient = Patient(
        code=code,
        name=f"利用者 {code}",
        status="active",
        primary_office_id=office.id if office is not None else None,
    )
    db.add(patient)
    await db.flush()
    return patient


async def _seed_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start: time,
    duration_min: int = 30,
    template: CourseTemplate | None = None,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=duration_min,
        course_template_id=template.id if template is not None else None,
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _seed_course(
    db,
    *,
    office: Office,
    staff: Staff | None,
    weekday: int,
    iso_week: int = ISO_WEEK,
    code: str = "A",
    template: CourseTemplate | None = None,
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
        template_id=template.id if template is not None else None,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(
    db,
    *,
    patient: Patient,
    course: Course,
    visit_date: date,
    start: time,
    duration_min: int = 30,
) -> Visit:
    end_total = start.hour * 60 + start.minute + duration_min
    visit = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=time(end_total // 60, end_total % 60),
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


async def _create_period(client, admin: User, patient: Patient, *, weekly_target: int = 5) -> dict:
    res = await client.post(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "start_date": PERIOD_START.isoformat(),
            "end_date": PERIOD_END.isoformat(),
            "weekly_target": weekly_target,
            "note": "退院直後の集中訪問",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------
# ① 期間 CRUD + active 重複 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_period_crud_and_active_duplicate_is_422(client, db) -> None:
    admin = await _make_user(db, email="svw-period@example.com")
    office, _staff = await _seed_office_staff(db)
    patient = await _seed_patient(db, office=office, code="SVW-P1")
    await db.commit()

    created = await _create_period(client, admin, patient, weekly_target=5)
    assert created["status"] == "active"
    assert created["weekly_target"] == 5
    assert created["start_date"] == PERIOD_START.isoformat()

    # 同一患者で 2 本目の active は 422.
    dup = await client.post(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        json={
            "patient_id": str(patient.id),
            "start_date": PERIOD_START.isoformat(),
            "end_date": PERIOD_END.isoformat(),
            "weekly_target": 5,
        },
    )
    assert dup.status_code == 422, dup.text

    # 一覧 (既定は active のみ).
    listed = await client.get(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        params={"patient_id": str(patient.id)},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1

    # 延長 + 目標変更 + 終了.
    patched = await client.patch(
        f"/api/v1/special-visit-periods/{created['id']}",
        headers=_bearer(admin),
        json={"weekly_target": 4, "end_date": (PERIOD_END + timedelta(days=7)).isoformat()},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["weekly_target"] == 4

    ended = await client.patch(
        f"/api/v1/special-visit-periods/{created['id']}",
        headers=_bearer(admin),
        json={"status": "ended"},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"

    # 終了後は既定一覧に出ない / include_inactive で出る.
    after = await client.get(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        params={"patient_id": str(patient.id)},
    )
    assert after.json() == []
    after_all = await client.get(
        "/api/v1/special-visit-periods",
        headers=_bearer(admin),
        params={"patient_id": str(patient.id), "include_inactive": True},
    )
    assert len(after_all.json()) == 1

    # active が無くなったので新規作成は通る.
    again = await _create_period(client, admin, patient)
    assert again["status"] == "active"


@pytest.mark.asyncio
async def test_period_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="svw-staff@example.com", role="staff")
    res = await client.get("/api/v1/special-visit-periods", headers=_bearer(staff_user))
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# ② カレンダー (生成済み週 = visit / 未生成週 = PFV 投影 / 週合計・target_met)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_generated_and_ungenerated_weeks(client, db) -> None:
    admin = await _make_user(db, email="svw-cal@example.com")
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office)
    patient = await _seed_patient(db, office=office, code="SVW-CAL")
    # 固定訪問は Mon(0) と Wed(2) の週 2 回.
    await _seed_pfv(db, patient=patient, weekday=0, start=time(9, 30), template=template)
    await _seed_pfv(db, patient=patient, weekday=2, start=time(10, 0), template=template)
    # 週 20 のみ生成済み (Course 行が存在する) + 実 visit 2 件.
    mon_course = await _seed_course(db, office=office, staff=staff, weekday=0, template=template)
    wed_course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    await _seed_visit(
        db, patient=patient, course=mon_course, visit_date=WEEK_MONDAY, start=time(9, 30)
    )
    await _seed_visit(
        db,
        patient=patient,
        course=wed_course,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(10, 0),
    )
    await db.commit()

    period = await _create_period(client, admin, patient, weekly_target=3)

    res = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["period"]["id"] == period["id"]
    assert len(body["weeks"]) == 2

    # --- 週 20 = 生成済み: 実 visit が visit_id 付きで出る ---
    w20 = body["weeks"][0]
    assert (w20["iso_year"], w20["iso_week"]) == (ISO_YEAR, ISO_WEEK)
    assert w20["week_monday"] == WEEK_MONDAY.isoformat()
    assert len(w20["days"]) == 6  # 月〜土 (日曜は対象外)
    mon = w20["days"][0]
    assert len(mon["fixed_visits"]) == 1
    assert mon["fixed_visits"][0]["generated"] is True
    assert mon["fixed_visits"][0]["visit_id"] is not None
    assert mon["fixed_visits"][0]["start_time"] == "09:30"
    assert mon["fixed_visits"][0]["end_time"] == "10:00"
    assert mon["fixed_visits"][0]["course_label"] == "稲A"
    assert mon["fixed_visits"][0]["staff_name"] == "担当看護師"
    assert w20["days"][1]["fixed_visits"] == []
    assert len(w20["days"][2]["fixed_visits"]) == 1
    assert w20["total"] == 2
    assert w20["target_met"] is False  # 目標 3 に対して 2

    # --- 週 21 = 未生成: PFV の投影 (visit_id=None / generated=False) ---
    w21 = body["weeks"][1]
    assert (w21["iso_year"], w21["iso_week"]) == (ISO_YEAR, NEXT_ISO_WEEK)
    assert w21["week_monday"] == NEXT_MONDAY.isoformat()
    w21_mon = w21["days"][0]
    assert len(w21_mon["fixed_visits"]) == 1
    assert w21_mon["fixed_visits"][0]["generated"] is False
    assert w21_mon["fixed_visits"][0]["visit_id"] is None
    assert w21_mon["fixed_visits"][0]["start_time"] == "09:30"
    assert w21_mon["fixed_visits"][0]["course_label"] == "稲A"
    assert w21["total"] == 2
    assert w21["target_met"] is False

    # --- ○ を 1 つ足すと週合計が 3 になり目標達成 ---
    added = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/marks",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 4},
    )
    assert added.status_code == 201, added.text

    res2 = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    w20b = res2.json()["weeks"][0]
    assert w20b["days"][4]["extra_mark"] is not None
    assert w20b["days"][4]["extra_mark"]["kind"] == "extra"
    assert w20b["days"][4]["extra_mark"]["status"] == "pool"
    assert w20b["total"] == 3
    assert w20b["target_met"] is True


@pytest.mark.asyncio
async def test_calendar_preferred_from_weekly_pattern(client, db) -> None:
    """希望訪問カレンダー (patients.weekly_pattern) の曜日別希望時間帯が出る."""
    admin = await _make_user(db, email="svw-pref@example.com")
    office, _staff = await _seed_office_staff(db)
    patient = await _seed_patient(db, office=office, code="SVW-PREF")
    patient.weekly_pattern = {
        "entries": [
            {
                "weekday": "Tue",
                "time_type": "時間帯",
                "preferred_start": "14:00",
                "preferred_end": "16:00",
                "service_minutes": 30,
            }
        ]
    }
    await db.commit()

    period = await _create_period(client, admin, patient)
    res = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    days = res.json()["weeks"][0]["days"]
    assert days[1]["preferred"] == [{"start": "14:00", "end": "16:00"}]
    # 希望のない曜日は空配列.
    assert days[0]["preferred"] == []


# ---------------------------------------------------------------------------
# ③ ○ マークの追加 / 取消 / 同セル 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extra_mark_create_duplicate_409_and_cancel(client, db) -> None:
    admin = await _make_user(db, email="svw-mark@example.com")
    office, _staff = await _seed_office_staff(db)
    patient = await _seed_patient(db, office=office, code="SVW-MK")
    await db.commit()
    period = await _create_period(client, admin, patient)

    payload = {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 3}
    first = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/marks",
        headers=_bearer(admin),
        json=payload,
    )
    assert first.status_code == 201, first.text
    mark = first.json()
    assert mark["kind"] == "extra"
    assert mark["status"] == "pool"
    assert mark["patient_id"] == str(patient.id)
    assert mark["placed_visit_id"] is None

    # 同セルは 409.
    dup = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/marks",
        headers=_bearer(admin),
        json=payload,
    )
    assert dup.status_code == 409, dup.text

    # 取消 (204) → 同セルに再度 ○ を立てられる.
    deleted = await client.delete(
        f"/api/v1/special-visit-marks/{mark['id']}", headers=_bearer(admin)
    )
    assert deleted.status_code == 204, deleted.text

    again = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/marks",
        headers=_bearer(admin),
        json=payload,
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] != mark["id"]

    # 期間範囲外の週は 422 (API 直叩きの水増し防止・レビュー補強).
    out_of_range = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/marks",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK + 30, "weekday": 3},
    )
    assert out_of_range.status_code == 422, out_of_range.text


# ---------------------------------------------------------------------------
# ④ 退避 → visit soft-delete + snapshot → 復元
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_displace_soft_deletes_visit_and_restore_brings_it_back(client, db) -> None:
    admin = await _make_user(db, email="svw-disp@example.com")
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office)
    patient = await _seed_patient(db, office=office, code="SVW-DISP")
    await _seed_pfv(db, patient=patient, weekday=0, start=time(9, 30), template=template)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, template=template)
    visit = await _seed_visit(
        db, patient=patient, course=course, visit_date=WEEK_MONDAY, start=time(9, 30)
    )
    visit_id = visit.id
    await db.commit()

    period = await _create_period(client, admin, patient, weekly_target=2)

    res = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    assert res.status_code == 201, res.text
    mark = res.json()
    assert mark["kind"] == "displaced"
    assert mark["status"] == "pool"

    # 訪問は soft-delete されている.
    await db.refresh(visit)
    assert visit.deleted_at is not None

    # snapshot に復元情報が入っている.
    mark_row = await db.scalar(
        select(SpecialVisitMark).where(SpecialVisitMark.id == UUID(mark["id"]))
    )
    assert mark_row is not None
    snapshot = mark_row.displaced_snapshot
    assert snapshot is not None
    assert len(snapshot["visits"]) == 1
    assert snapshot["visits"][0]["visit_id"] == str(visit_id)
    assert snapshot["visits"][0]["start_time"] == "09:30"
    assert snapshot["visits"][0]["course_label"] == "稲A"

    # 恒久パターン (PFV) は一切変更されない.
    pfv_count = len(
        (
            await db.scalars(
                select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
            )
        ).all()
    )
    assert pfv_count == 1

    # カレンダー: 固定訪問カードは消え displaced_mark が立つ. 週合計は不変 (退避しても 1).
    cal = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    w20 = cal.json()["weeks"][0]
    assert w20["days"][0]["fixed_visits"] == []
    assert w20["days"][0]["displaced_mark"]["status"] == "pool"
    assert w20["total"] == 1

    # 同じセルの二重退避は 409.
    dup = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    assert dup.status_code == 409, dup.text

    # 復元 (未配置なので force 不要).
    restored = await client.post(
        f"/api/v1/special-visit-marks/{mark['id']}/restore", headers=_bearer(admin)
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "cancelled"

    await db.refresh(visit)
    assert visit.deleted_at is None

    cal2 = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    w20b = cal2.json()["weeks"][0]
    assert len(w20b["days"][0]["fixed_visits"]) == 1
    assert w20b["days"][0]["displaced_mark"] is None
    assert w20b["total"] == 1


@pytest.mark.asyncio
async def test_displace_on_ungenerated_week_records_pfv_snapshot(client, db) -> None:
    """未生成週の退避はマークのみ (snapshot={"pfv": true})・訪問は触らない."""
    admin = await _make_user(db, email="svw-disp-pfv@example.com")
    office, _staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office)
    patient = await _seed_patient(db, office=office, code="SVW-DPFV")
    await _seed_pfv(db, patient=patient, weekday=1, start=time(11, 0), template=template)
    await db.commit()

    period = await _create_period(client, admin, patient, weekly_target=1)
    res = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 1},
    )
    assert res.status_code == 201, res.text
    mark_row = await db.scalar(
        select(SpecialVisitMark).where(SpecialVisitMark.id == UUID(res.json()["id"]))
    )
    assert mark_row.displaced_snapshot == {"pfv": True}

    # 未生成週なので PFV 投影も消え、代わりに displaced チケットが立つ (週合計は不変).
    cal = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    w20 = cal.json()["weeks"][0]
    assert w20["days"][1]["fixed_visits"] == []
    assert w20["days"][1]["displaced_mark"] is not None
    assert w20["total"] == 1


@pytest.mark.asyncio
async def test_displace_without_fixed_visit_is_409(client, db) -> None:
    admin = await _make_user(db, email="svw-disp-none@example.com")
    office, _staff = await _seed_office_staff(db)
    patient = await _seed_patient(db, office=office, code="SVW-DNONE")
    await db.commit()
    period = await _create_period(client, admin, patient)

    res = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 5},
    )
    assert res.status_code == 409, res.text


# ---------------------------------------------------------------------------
# ⑤ 配置済み退避の restore は force 必須 (409)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_placed_displaced_requires_force(client, db) -> None:
    admin = await _make_user(db, email="svw-force@example.com")
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office)
    patient = await _seed_patient(db, office=office, code="SVW-FORCE")
    await _seed_pfv(db, patient=patient, weekday=0, start=time(9, 30), template=template)
    mon_course = await _seed_course(db, office=office, staff=staff, weekday=0, template=template)
    thu_course = await _seed_course(db, office=office, staff=staff, weekday=3, template=template)
    original = await _seed_visit(
        db, patient=patient, course=mon_course, visit_date=WEEK_MONDAY, start=time(9, 30)
    )
    await db.commit()

    period = await _create_period(client, admin, patient, weekly_target=1)
    displaced = await client.post(
        f"/api/v1/special-visit-periods/{period['id']}/displace",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 0},
    )
    assert displaced.status_code == 201, displaced.text
    mark_id = displaced.json()["id"]

    # 退避チケットを木曜へ配置しようとすると曜日不一致で 422.
    mismatched = await client.post(
        f"/api/v1/special-visit-marks/{mark_id}/place",
        headers=_bearer(admin),
        json={"course_id": str(thu_course.id), "start_time": "14:00"},
    )
    assert mismatched.status_code == 422, mismatched.text

    # 同曜日 (月) の別枠へ配置.
    placed = await client.post(
        f"/api/v1/special-visit-marks/{mark_id}/place",
        headers=_bearer(admin),
        json={"course_id": str(mon_course.id), "start_time": "15:00"},
    )
    assert placed.status_code == 200, placed.text
    placed_visit_id = placed.json()["visit_id"]
    assert placed.json()["mark"]["status"] == "placed"

    # force 無しの restore は 409.
    conflict = await client.post(
        f"/api/v1/special-visit-marks/{mark_id}/restore", headers=_bearer(admin)
    )
    assert conflict.status_code == 409, conflict.text

    # force=true で配置先を削除してから復元.
    forced = await client.post(
        f"/api/v1/special-visit-marks/{mark_id}/restore",
        headers=_bearer(admin),
        params={"force": True},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["status"] == "cancelled"

    placed_row = await db.scalar(select(Visit).where(Visit.id == UUID(placed_visit_id)))
    assert placed_row.deleted_at is not None
    await db.refresh(original)
    assert original.deleted_at is None


# ---------------------------------------------------------------------------
# ⑥ place → visit 作成 + マーク placed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_creates_manual_week_visit(client, db) -> None:
    admin = await _make_user(db, email="svw-place@example.com")
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office)
    patient = await _seed_patient(db, office=office, code="SVW-PLACE")
    # PFV の duration_min (45) が配置時の所要時間として使われる.
    await _seed_pfv(
        db, patient=patient, weekday=0, start=time(9, 30), duration_min=45, template=template
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    await db.commit()

    period = await _create_period(client, admin, patient, weekly_target=3)
    mark = (
        await client.post(
            f"/api/v1/special-visit-periods/{period['id']}/marks",
            headers=_bearer(admin),
            json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": 2},
        )
    ).json()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark['id']}/place",
        headers=_bearer(admin),
        json={"course_id": str(course.id), "start_time": "14:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mark"]["status"] == "placed"
    assert body["mark"]["placed_visit_id"] == body["visit_id"]
    assert body["mark"]["placed_summary"] == {"start_time": "14:00", "course_label": "稲A"}

    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.source == VISIT_SOURCE_MANUAL_WEEK
    assert visit.status == VISIT_STATUS_PLANNED
    assert visit.visit_date == WEEK_MONDAY + timedelta(days=2)
    assert visit.start_time == time(14, 0)
    assert visit.end_time == time(14, 45)
    assert visit.required_staff_count == 1
    assert visit.primary_staff_id == staff.id
    assert visit.course_id == course.id

    # PFV は作られない (この週だけの決定).
    pfv_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(pfv_rows) == 1

    # 二重配置は 409.
    again = await client.post(
        f"/api/v1/special-visit-marks/{mark['id']}/place",
        headers=_bearer(admin),
        json={"course_id": str(course.id), "start_time": "15:00"},
    )
    assert again.status_code == 409, again.text

    # 配置済み ○ の訪問は「固定訪問の残数」に混ぜない (二重計上防止) が週合計には入る.
    cal = await client.get(
        f"/api/v1/special-visit-periods/{period['id']}/calendar", headers=_bearer(admin)
    )
    w20 = cal.json()["weeks"][0]
    assert w20["days"][2]["fixed_visits"] == []
    assert w20["days"][2]["extra_mark"]["status"] == "placed"
    assert w20["days"][2]["extra_mark"]["placed_summary"]["start_time"] == "14:00"
    assert w20["total"] == 1


# ---------------------------------------------------------------------------
# ⑦ プール一覧 + 自己回復
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_list_and_self_healing(client, db) -> None:
    admin = await _make_user(db, email="svw-pool@example.com")
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office)
    patient = await _seed_patient(db, office=office, code="SVW-POOL")
    course = await _seed_course(db, office=office, staff=staff, weekday=0, template=template)
    await db.commit()

    period = await _create_period(client, admin, patient, weekly_target=2)
    marks = []
    for weekday in (0, 1):
        res = await client.post(
            f"/api/v1/special-visit-periods/{period['id']}/marks",
            headers=_bearer(admin),
            json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "weekday": weekday},
        )
        assert res.status_code == 201, res.text
        marks.append(res.json())

    params = {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK}
    pool = await client.get(
        "/api/v1/special-visit-marks/pool", headers=_bearer(admin), params=params
    )
    assert pool.status_code == 200, pool.text
    tickets = pool.json()
    assert len(tickets) == 2
    assert tickets[0]["patient"]["code"] == "SVW-POOL"
    assert tickets[0]["patient"]["id"] == str(patient.id)
    assert tickets[0]["period"]["weekly_target"] == 2
    assert tickets[0]["period"]["end_date"] == PERIOD_END.isoformat()
    assert tickets[0]["last_placement"] is None

    # 別拠点で絞ると 0 件.
    other_office = Office(name="都賀", code="TSUGA")
    db.add(other_office)
    await db.commit()
    filtered = await client.get(
        "/api/v1/special-visit-marks/pool",
        headers=_bearer(admin),
        params={**params, "office_id": str(other_office.id)},
    )
    assert filtered.json() == []

    # 1 件配置 → プールは 1 件に減り、残りに last_placement が付く.
    placed = await client.post(
        f"/api/v1/special-visit-marks/{marks[0]['id']}/place",
        headers=_bearer(admin),
        json={"course_id": str(course.id), "start_time": "13:00"},
    )
    assert placed.status_code == 200, placed.text
    placed_visit_id = placed.json()["visit_id"]

    pool2 = await client.get(
        "/api/v1/special-visit-marks/pool", headers=_bearer(admin), params=params
    )
    tickets2 = pool2.json()
    assert len(tickets2) == 1
    assert tickets2[0]["mark"]["id"] == marks[1]["id"]
    assert tickets2[0]["last_placement"] == {
        "weekday": 0,
        "start_time": "13:00",
        "course_label": "稲A",
        "staff_name": "担当看護師",
    }

    # 自己回復: 配置先訪問が消えた (soft-delete) → placed のまま pool 扱いで返る.
    visit = await db.scalar(select(Visit).where(Visit.id == UUID(placed_visit_id)))
    visit.deleted_at = datetime.now(UTC)
    await db.commit()

    pool3 = await client.get(
        "/api/v1/special-visit-marks/pool", headers=_bearer(admin), params=params
    )
    tickets3 = pool3.json()
    assert len(tickets3) == 2
    healed = next(t for t in tickets3 if t["mark"]["id"] == marks[0]["id"])
    assert healed["mark"]["status"] == "placed"  # DB 値は据え置き (書き戻し不要)
    assert healed["last_placement"] is None  # 生きた配置先がもう無い


# ---------------------------------------------------------------------------
# ⑧ Layer1: displaced マークのある曜日は PFV 展開されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer1_skips_displaced_weekday(db) -> None:
    expander = Layer1Expander()
    patient = await _seed_patient(db, code="SVW-L1")
    await _seed_pfv(db, patient=patient, weekday=0, start=time(9, 0))
    await _seed_pfv(db, patient=patient, weekday=2, start=time(10, 0))
    period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=PERIOD_START,
        end_date=PERIOD_END,
        weekly_target=5,
        status="active",
    )
    db.add(period)
    await db.flush()
    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=patient.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        kind="displaced",
        status="pool",
        displaced_snapshot={"pfv": True},
    )
    db.add(mark)
    await db.commit()

    result = await expander.expand_week(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK)
    await db.commit()

    # Mon(0) は退避済みなので生成されず、Wed(2) だけが生成される.
    assert result.visits_created_count == 1
    rows = list(
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient.id, Visit.deleted_at.is_(None))
        )
    )
    assert len(rows) == 1
    assert rows[0].visit_date == WEEK_MONDAY + timedelta(days=2)

    # 別週 (21) は退避マークが無いので通常どおり 2 件.
    result_next = await expander.expand_week(db, iso_year=ISO_YEAR, iso_week=NEXT_ISO_WEEK)
    await db.commit()
    assert result_next.visits_created_count == 2

    # 退避を取消すと当該週も通常どおり 2 件に戻る.
    mark.status = "cancelled"
    await db.commit()
    result_again = await expander.expand_week(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK)
    await db.commit()
    assert result_again.visits_created_count == 2
