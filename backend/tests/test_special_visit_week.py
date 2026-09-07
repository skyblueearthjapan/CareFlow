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
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

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

# place の新モード (course_template_id / visit_id) には「過去日 (JST) には配置できない」
# ガードがあるため、基準週は固定日付ではなく **常に次の月曜から始まる未来週** にする
# (固定日付だと時間経過でテストが落ちる)。
TODAY_JST = datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo")).date()
WEEK_MONDAY = TODAY_JST + timedelta(days=7 - TODAY_JST.weekday())
NEXT_MONDAY = WEEK_MONDAY + timedelta(days=7)
ISO_YEAR, ISO_WEEK, _ = WEEK_MONDAY.isocalendar()
NEXT_ISO_YEAR, NEXT_ISO_WEEK, _ = NEXT_MONDAY.isocalendar()

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
    source: str = "auto",
    visit_group_id: UUID | None = None,
    required_staff_count: int = 1,
) -> Visit:
    end_total = start.hour * 60 + start.minute + duration_min
    visit = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=time(end_total // 60, end_total % 60),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source=source,
        required_staff_count=required_staff_count,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
        visit_group_id=visit_group_id,
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
    assert (w21["iso_year"], w21["iso_week"]) == (NEXT_ISO_YEAR, NEXT_ISO_WEEK)
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
# ⑥-b place の追加モード (course_template_id = 案 F-2 / visit_id = リンク)
# ---------------------------------------------------------------------------


async def _place_mark_setup(db, *, email: str, code: str, label: str = "M"):
    """place の追加モード用: admin / 拠点 / テンプレート / 患者 / 期間 / ○ マーク.

    **API を一切叩かず DB だけで組む**。app 側 session はレスポンス後に共有
    コネクション (in-memory SQLite) へ ROLLBACK を出すことがあり、API 呼び出しを
    挟んでから ORM で seed すると未 commit の行が巻き添えで消えて flaky になる。
    テストは「DB seed をすべて済ませて commit → その後に API」の順で書くこと。
    """
    admin = await _make_user(db, email=email)
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office, label=label)
    patient = await _seed_patient(db, office=office, code=code)
    await _seed_pfv(db, patient=patient, weekday=0, start=time(9, 30), duration_min=45)
    period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=PERIOD_START,
        end_date=PERIOD_END,
        weekly_target=3,
        status="active",
    )
    db.add(period)
    await db.flush()
    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=patient.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=2,
        kind="extra",
        status="pool",
    )
    db.add(mark)
    await db.flush()
    return admin, office, staff, template, patient, period, mark


@pytest.mark.asyncio
async def test_place_by_course_template_creates_missing_course(client, db) -> None:
    """案 F-2: 当該週の Course が無くても M テンプレート指定で配置できる."""
    admin, office, _staff, template, patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-tpl@example.com", code="SVW-TPL"
    )
    await db.commit()
    # 当該週・曜日の Course はまだ存在しない.
    assert (
        await db.scalar(
            select(Course).where(
                Course.template_id == template.id,
                Course.iso_year == ISO_YEAR,
                Course.iso_week == ISO_WEEK,
                Course.weekday == 2,
            )
        )
    ) is None
    await db.commit()  # 読み取り TX を閉じる (同上).

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mark"]["status"] == "placed"
    assert body["mark"]["placed_visit_id"] == body["visit_id"]
    assert body["mark"]["placed_summary"] == {"start_time": "14:00", "course_label": "稲M"}

    course = await db.scalar(
        select(Course).where(
            Course.template_id == template.id,
            Course.iso_year == ISO_YEAR,
            Course.iso_week == ISO_WEEK,
            Course.weekday == 2,
        )
    )
    assert course is not None
    assert course.code == "M"
    assert course.office_id == office.id
    # 担当なし (assigned_staff_id=None) でも配置できる.
    assert course.assigned_staff_id is None

    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.source == VISIT_SOURCE_MANUAL_WEEK
    assert visit.course_id == course.id
    assert visit.primary_staff_id is None
    assert visit.visit_date == WEEK_MONDAY + timedelta(days=2)
    assert visit.start_time == time(14, 0)
    assert visit.end_time == time(14, 45)
    assert visit.patient_id == patient.id


@pytest.mark.asyncio
async def test_place_by_course_template_office_mismatch_is_422(client, db) -> None:
    admin, _office, _staff, _template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-tpl-ng@example.com", code="SVW-TPL-NG"
    )
    other_office = Office(name="都賀", code="TSUGA")
    db.add(other_office)
    await db.flush()
    other_template = await _seed_template(db, office=other_office, label="M")
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(other_template.id), "start_time": "14:00"},
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "拠点が一致しません"


@pytest.mark.asyncio
async def test_place_links_existing_visit_without_creating_one(client, db) -> None:
    """visit_id モード: place-and-fix で作られた訪問にマークをリンクするだけ."""
    admin, office, staff, template, patient, period, mark = await _place_mark_setup(
        db, email="svw-place-link@example.com", code="SVW-LINK", label="A"
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    visit = await _seed_visit(
        db,
        patient=patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(16, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    await db.commit()

    before = len((await db.scalars(select(Visit))).all())
    # 読み取りで開いた TX を閉じてから API を叩く (テスト session と app session は
    # in-memory SQLite の同一コネクションを共有するため、開きっぱなしだと app 側の
    # 参照がぶれる — test_constraint_confirm_paths._refresh_session と同じ事情).
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": str(visit.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visit_id"] == str(visit.id)
    assert body["mark"]["status"] == "placed"
    assert body["mark"]["placed_visit_id"] == str(visit.id)
    assert body["mark"]["placed_summary"] == {"start_time": "16:00", "course_label": "稲A"}

    # 訪問は新規作成されない.
    assert len((await db.scalars(select(Visit))).all()) == before

    # カレンダーでも ● 扱い (fixed_visits には二重計上されない).
    cal = await client.get(
        f"/api/v1/special-visit-periods/{period.id}/calendar", headers=_bearer(admin)
    )
    w20 = cal.json()["weeks"][0]
    assert w20["days"][2]["fixed_visits"] == []
    assert w20["days"][2]["extra_mark"]["status"] == "placed"
    assert w20["days"][2]["extra_mark"]["placed_summary"]["start_time"] == "16:00"


@pytest.mark.asyncio
async def test_place_link_rejects_wrong_date_other_patient_and_cancelled(client, db) -> None:
    admin, office, staff, template, patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-link-ng@example.com", code="SVW-LINK-NG", label="A"
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    # ① 日付違い (mark は weekday=2 = 水曜).
    wrong_date = await _seed_visit(
        db,
        patient=patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=3),
        start=time(16, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    # ② 別患者.
    other_patient = await _seed_patient(db, office=office, code="SVW-LINK-OTHER")
    other_visit = await _seed_visit(
        db,
        patient=other_patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(17, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    # ③ 取消済み訪問.
    cancelled = await _seed_visit(
        db,
        patient=patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(18, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    cancelled.status = "cancelled"
    # ④ 固定訪問 (source='auto') はリンク対象外.
    auto_visit = await _seed_visit(
        db,
        patient=patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(19, 0),
    )
    await db.commit()

    for visit_id, expected in (
        (wrong_date.id, "訪問日が対象週・曜日と一致しません"),
        (other_visit.id, "訪問の利用者がチケットと一致しません"),
        (cancelled.id, "予定 (planned) の訪問のみリンクできます"),
        (auto_visit.id, "この訪問はリンクできません（追加枠として作られた訪問のみ）"),
    ):
        res = await client.post(
            f"/api/v1/special-visit-marks/{mark.id}/place",
            headers=_bearer(admin),
            json={"visit_id": str(visit_id)},
        )
        assert res.status_code == 422, res.text
        assert res.json()["detail"] == expected

    # 存在しない訪問は 404.
    missing = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": "00000000-0000-0000-0000-000000000001"},
    )
    assert missing.status_code == 404, missing.text

    # マークは未配置のまま.
    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.status == "pool"
    assert row.placed_visit_id is None


@pytest.mark.asyncio
async def test_place_requires_exactly_one_selector(client, db) -> None:
    admin, office, _staff, template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-selector@example.com", code="SVW-SEL", label="A"
    )
    await db.commit()

    # ① 2 つ同時指定 → 422.
    two = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={
            "course_template_id": str(template.id),
            "office_id": str(office.id),
            "course_code": "A",
            "start_time": "14:00",
        },
    )
    assert two.status_code == 422, two.text

    # ② 1 つも指定しない → 422.
    none_given = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"start_time": "14:00"},
    )
    assert none_given.status_code == 422, none_given.text

    # ③ 訪問を作る経路で start_time 欠落 → 422.
    no_time = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id)},
    )
    assert no_time.status_code == 422, no_time.text


@pytest.mark.asyncio
async def test_place_by_office_and_course_code_still_works(client, db) -> None:
    """既存の (office_id + course_code) 経路 (PoolCandidateList) は不変."""
    admin, office, staff, template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-code@example.com", code="SVW-CODE", label="A"
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"office_id": str(office.id), "course_code": "A", "start_time": "13:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.course_id == course.id
    assert visit.primary_staff_id == staff.id


@pytest.mark.asyncio
async def test_place_by_course_template_reuses_existing_course(client, db) -> None:
    """course_template_id モード: 当該週の Course が既にあれば作らず再利用する."""
    admin, office, staff, template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-tpl-reuse@example.com", code="SVW-TPL-RE", label="A"
    )
    existing = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    await db.commit()
    before = len((await db.scalars(select(Course))).all())
    await db.commit()  # 読み取り TX を閉じる (同上).

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # Course は増えない (helper の SELECT 分岐).
    assert len((await db.scalars(select(Course))).all()) == before
    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.course_id == existing.id
    assert visit.primary_staff_id == staff.id


@pytest.mark.asyncio
async def test_place_link_rejects_visit_already_linked_to_another_mark(client, db) -> None:
    admin, office, staff, template, patient, period, mark = await _place_mark_setup(
        db, email="svw-place-dup@example.com", code="SVW-DUP", label="A"
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    visit = await _seed_visit(
        db,
        patient=patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(16, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    # 同一期間の同一セルには ○ を 2 つ置けない (UNIQUE) ので、終了済みの別期間に
    # 同じセルの ○ を作って「別マークが同じ訪問を指す」状況だけを再現する.
    other_period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=PERIOD_START,
        end_date=PERIOD_END,
        weekly_target=3,
        status="ended",
    )
    db.add(other_period)
    await db.flush()
    dup_mark = SpecialVisitMark(
        period_id=other_period.id,
        patient_id=patient.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=2,
        kind="extra",
        status="pool",
    )
    # 日付違いの ○ (weekday=3): 409 ではなく 422 のままであることの対照.
    other_mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=patient.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=3,
        kind="extra",
        status="pool",
    )
    db.add_all([dup_mark, other_mark])
    await db.commit()

    first = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": str(visit.id)},
    )
    assert first.status_code == 200, first.text

    res = await client.post(
        f"/api/v1/special-visit-marks/{dup_mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": str(visit.id)},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "この訪問は既に別の追加枠に紐づいています"

    # weekday=3 のマークは日付違いのまま (409 ではなく 422).
    wrong_day = await client.post(
        f"/api/v1/special-visit-marks/{other_mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": str(visit.id)},
    )
    assert wrong_day.status_code == 422, wrong_day.text


@pytest.mark.asyncio
async def test_place_link_is_visit_group_aware(client, db) -> None:
    """2 名体制 (visit_group_id): 片方をリンクしても相方まで一体で扱う."""
    admin, office, staff, template, patient, period, mark = await _place_mark_setup(
        db, email="svw-place-group@example.com", code="SVW-GRP", label="A"
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    group_id = uuid4()
    kwargs = {
        "visit_date": WEEK_MONDAY + timedelta(days=2),
        "start": time(16, 0),
        "source": VISIT_SOURCE_MANUAL_WEEK,
        "visit_group_id": group_id,
        "required_staff_count": 2,
    }
    v1 = await _seed_visit(db, patient=patient, course=course, **kwargs)
    v2 = await _seed_visit(db, patient=patient, course=course, **kwargs)
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": str(v1.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["mark"]["placed_visit_id"] == str(v1.id)

    # カレンダー: どちらも固定訪問として出ない・週合計は ○ 1 件ぶんだけ.
    cal = await client.get(
        f"/api/v1/special-visit-periods/{period.id}/calendar", headers=_bearer(admin)
    )
    w20 = cal.json()["weeks"][0]
    assert w20["days"][2]["fixed_visits"] == []
    assert w20["days"][2]["extra_mark"]["status"] == "placed"
    assert w20["total"] == 1

    # force 取消でグループ全体が soft-delete される.
    delete = await client.delete(
        f"/api/v1/special-visit-marks/{mark.id}?force=true", headers=_bearer(admin)
    )
    assert delete.status_code == 204, delete.text
    for row in (v1, v2):
        await db.refresh(row)
        assert row.deleted_at is not None, f"visit {row.id} not soft-deleted"


@pytest.mark.asyncio
async def test_place_rejects_past_date_and_allows_today(client, db) -> None:
    """過去日 (JST) ガードは **新モード限定**・当日は可.

    course_template_id / visit_id は 422、既存の course_id / (office_id +
    course_code) 経路 (プール ⭐) は PO 判断が出るまで従来どおり過去日も通す。
    """
    admin = await _make_user(db, email="svw-place-past@example.com")
    office, staff = await _seed_office_staff(db)
    template = await _seed_template(db, office=office, label="A")
    patient = await _seed_patient(db, office=office, code="SVW-PAST")

    # 直近の過去日 (日曜はマーク対象外なので月〜土まで遡る).
    past = TODAY_JST - timedelta(days=1)
    while past.weekday() > 5:
        past -= timedelta(days=1)

    period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=past - timedelta(days=7),
        end_date=TODAY_JST + timedelta(days=7),
        weekly_target=3,
        status="active",
    )
    db.add(period)
    await db.flush()

    async def _cell(day: date) -> tuple[SpecialVisitMark, Course]:
        iso = day.isocalendar()
        mark = SpecialVisitMark(
            period_id=period.id,
            patient_id=patient.id,
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=day.weekday(),
            kind="extra",
            status="pool",
        )
        course = Course(
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=day.weekday(),
            code="A",
            course_status=COURSE_STATUS_STAFF_ASSIGNED,
            assigned_staff_id=staff.id,
            office_id=office.id,
            template_id=template.id,
        )
        db.add_all([mark, course])
        await db.flush()
        return mark, course

    past_mark, past_course = await _cell(past)
    past_visit = await _seed_visit(
        db,
        patient=patient,
        course=past_course,
        visit_date=past,
        start=time(16, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    today_cell = await _cell(TODAY_JST) if TODAY_JST.weekday() <= 5 else None
    await db.commit()

    url = f"/api/v1/special-visit-marks/{past_mark.id}/place"

    # ① course_template_id モード → 422.
    tpl = await client.post(
        url,
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00"},
    )
    assert tpl.status_code == 422, tpl.text
    assert tpl.json()["detail"] == "過去日には配置できません"

    # ② visit_id モード → 422.
    link = await client.post(url, headers=_bearer(admin), json={"visit_id": str(past_visit.id)})
    assert link.status_code == 422, link.text
    assert link.json()["detail"] == "過去日には配置できません"

    # ③ 既存モード (office_id + course_code) は過去日ガードに掛からない (従来動作).
    legacy = await client.post(
        url,
        headers=_bearer(admin),
        json={"office_id": str(office.id), "course_code": "A", "start_time": "14:00"},
    )
    assert legacy.status_code == 200, legacy.text

    # ④ 当日は新モードでも配置できる (日曜に走ったときはマーク対象外なので skip).
    if today_cell is not None:
        today_mark, _today_course = today_cell
        ok = await client.post(
            f"/api/v1/special-visit-marks/{today_mark.id}/place",
            headers=_bearer(admin),
            json={"course_template_id": str(template.id), "start_time": "14:00"},
        )
        assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# ⑥-c place の weekday 上書き (DnD 異曜日ドロップ = dnd-all-views 設計 §2-3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_with_weekday_override_moves_mark(client, db) -> None:
    """weekday 上書き: ○ を同じ週の別曜日へ移してから配置する (単一 TX・週合計不変)."""
    admin, office, staff, template, _patient, period, mark = await _place_mark_setup(
        db, email="svw-place-wd@example.com", code="SVW-WD", label="A"
    )
    # 週を「生成済み」にしておく (未生成週の PFV 投影が混ざると週合計の比較がぶれる).
    await _seed_course(db, office=office, staff=staff, weekday=2, template=template)
    await db.commit()

    cal_before = await client.get(
        f"/api/v1/special-visit-periods/{period.id}/calendar", headers=_bearer(admin)
    )
    assert cal_before.status_code == 200, cal_before.text
    total_before = cal_before.json()["weeks"][0]["total"]

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00", "weekday": 4},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mark"]["weekday"] == 4
    assert body["mark"]["status"] == "placed"

    # カレンダー: ○ は水曜から金曜へ移る・週合計は不変.
    cal_after = await client.get(
        f"/api/v1/special-visit-periods/{period.id}/calendar", headers=_bearer(admin)
    )
    week_after = cal_after.json()["weeks"][0]
    assert week_after["days"][2]["extra_mark"] is None
    assert week_after["days"][4]["extra_mark"]["status"] == "placed"
    assert week_after["total"] == total_before

    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.weekday == 4
    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.visit_date == WEEK_MONDAY + timedelta(days=4)
    assert visit.start_time == time(14, 0)


@pytest.mark.asyncio
async def test_place_weekday_override_conflict_is_409(client, db) -> None:
    """移動先に生きた ○ がある → 409 (FE が既存 ○ へ聞き直すための構造化 detail)."""
    admin, _office, _staff, template, patient, period, mark = await _place_mark_setup(
        db, email="svw-place-wd-dup@example.com", code="SVW-WD-DUP", label="A"
    )
    existing = SpecialVisitMark(
        period_id=period.id,
        patient_id=patient.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=4,
        kind="extra",
        status="pool",
    )
    db.add(existing)
    await db.flush()
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00", "weekday": 4},
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "special_mark_cell_conflict"
    assert detail["message"] == "この曜日には既に追加枠があります"
    assert detail["existing_mark_id"] == str(existing.id)
    assert detail["weekday"] == 4

    # ○ は動かず配置もされない (移動は配置と同一 TX).
    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.weekday == 2
    assert row.status == "pool"
    assert row.placed_visit_id is None


@pytest.mark.asyncio
async def test_place_weekday_override_rejects_displaced_mark(client, db) -> None:
    admin, _office, _staff, template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-wd-disp@example.com", code="SVW-WD-DISP", label="A"
    )
    mark.kind = "displaced"
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00", "weekday": 4},
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "退避枠は曜日を変えられません"

    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.weekday == 2


@pytest.mark.asyncio
async def test_place_weekday_override_out_of_period_is_422(client, db) -> None:
    """期間範囲は create_extra_mark と同じ **週粒度** (○ の週が期間外なら 422)."""
    admin, _office, _staff, template, _patient, period, mark = await _place_mark_setup(
        db, email="svw-place-wd-range@example.com", code="SVW-WD-RANGE", label="A"
    )
    # 期間を翌週だけに縮める → ○ のある週 (ISO_WEEK) が範囲外になる.
    period.start_date = NEXT_MONDAY
    period.end_date = NEXT_MONDAY + timedelta(days=5)
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00", "weekday": 4},
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == "指定曜日は期間の範囲外です"

    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.weekday == 2
    assert row.status == "pool"

    # 週粒度なので「水曜始まりの期間の月曜へ戻す」は通る (create_extra_mark と同じ).
    period.start_date = WEEK_MONDAY + timedelta(days=2)
    period.end_date = WEEK_MONDAY + timedelta(days=5)
    await db.commit()

    ok = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00", "weekday": 0},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["mark"]["weekday"] == 0


@pytest.mark.asyncio
async def test_place_with_same_weekday_behaves_like_no_override(client, db) -> None:
    """同じ曜日を渡しても no-op (移動判定・衝突検査を通さない)."""
    admin, _office, _staff, template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-wd-same@example.com", code="SVW-WD-SAME", label="A"
    )
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"course_template_id": str(template.id), "start_time": "14:00", "weekday": 2},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mark"]["weekday"] == 2

    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.visit_date == WEEK_MONDAY + timedelta(days=2)


@pytest.mark.asyncio
async def test_place_by_course_code_with_weekday_override(client, db) -> None:
    """既存モード (office_id + course_code) も weekday 上書きの対象.

    コース実体は「移動後の曜日」で引くため、その曜日に Course が無ければ 404。
    """
    admin, office, staff, template, _patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-wd-code@example.com", code="SVW-WD-CODE", label="A"
    )
    # 金曜 (weekday=4) にだけ A コースを置く (水曜には無い).
    friday_course = await _seed_course(db, office=office, staff=staff, weekday=4, template=template)
    await db.commit()

    # ① 移動先 (木) に Course が無ければ 404・○ は動かない.
    missing = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "course_code": "A",
            "start_time": "13:00",
            "weekday": 3,
        },
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["detail"] == "Course not found"

    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.weekday == 2
    assert row.status == "pool"
    await db.commit()  # 読み取り TX を閉じる.

    # ② 移動先 (金) に Course があれば配置できる.
    ok = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "course_code": "A",
            "start_time": "13:00",
            "weekday": 4,
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["mark"]["weekday"] == 4
    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["visit_id"])))
    assert visit is not None
    assert visit.course_id == friday_course.id
    assert visit.visit_date == WEEK_MONDAY + timedelta(days=4)


@pytest.mark.asyncio
async def test_place_link_with_weekday_override_matches_new_date(client, db) -> None:
    """visit_id モード: 移動後の曜日の日付にある訪問ならリンクできる."""
    admin, office, staff, template, patient, _period, mark = await _place_mark_setup(
        db, email="svw-place-wd-link@example.com", code="SVW-WD-LINK", label="A"
    )
    course = await _seed_course(db, office=office, staff=staff, weekday=3, template=template)
    visit = await _seed_visit(
        db,
        patient=patient,
        course=course,
        visit_date=WEEK_MONDAY + timedelta(days=3),
        start=time(16, 0),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    await db.commit()

    res = await client.post(
        f"/api/v1/special-visit-marks/{mark.id}/place",
        headers=_bearer(admin),
        json={"visit_id": str(visit.id), "weekday": 3},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visit_id"] == str(visit.id)
    assert body["mark"]["weekday"] == 3
    assert body["mark"]["status"] == "placed"

    row = await db.get(SpecialVisitMark, mark.id)
    await db.refresh(row)
    assert row.weekday == 3


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
    result_next = await expander.expand_week(db, iso_year=NEXT_ISO_YEAR, iso_week=NEXT_ISO_WEEK)
    await db.commit()
    assert result_next.visits_created_count == 2

    # 退避を取消すと当該週も通常どおり 2 件に戻る.
    mark.status = "cancelled"
    await db.commit()
    result_again = await expander.expand_week(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK)
    await db.commit()
    assert result_again.visits_created_count == 2
