"""Tests for W9-BE2: 固定化 API (from-week / from-week-bulk / fix-or-pattern).

検証観点 (最低 12 ケース):
  from-week:
    1. 当該週に visits あり → mode auto detect (normal)
    2. 当該週に visits あり → mode auto detect (special)
    3. mode 明示指定で上書き
    4. visits 0 件 → 既存 fixed_visits も削除して空配列返す
    5. 同一 weekday に複数 visit → 409
    6. staff 権限 → 403

  from-week-bulk:
    7. 全患者一括書き戻し → updated_count
    8. 冪等 (2 度実行で同結果)
    9. admin/manager のみ (staff → 403)

  fix-or-pattern:
    10. this_week_only → visit 単独 update、fixed_visits 不変
    11. pattern_change (normal) → patient_fixed_visits 更新 + 当該週 visits 更新
    12. pattern_change (special) → patient_fixed_visits の special を更新
    13. mode 不正値 → 422
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient_fixed_visit import PatientFixedVisit

# テスト週: ISO 2026-W19 (月曜 = 2026-05-04)
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEK_MONDAY = date(2026, 5, 4)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_patient(
    db,
    code: str,
    *,
    special_week_active: list | None = None,
    status: str = "active",
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status=status,
        special_week_active=special_week_active or [],
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, name: str = "スタッフ") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_visit(
    db,
    *,
    patient: Patient,
    visit_date: date,
    start: str = "09:00",
    end: str = "10:00",
) -> Visit:
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=time(sh, sm),
        end_time=time(eh, em),
        type="regular",
        status="planned",
        source="auto",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. from-week: 当該週に visits あり → mode auto detect (normal)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_normal_mode_auto_detect(client, db) -> None:
    """visits あり + special_week_active 非該当 → normal で固定化される."""
    admin = await _make_user(db, "fw-1-admin@example.com", "admin")
    patient = await _make_patient(db, "FW-001")

    # 月曜 (weekday=0) に visit を作成
    await _make_visit(db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00")

    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) == 1
    assert data[0]["mode"] == "normal"
    assert data[0]["weekday"] == 0  # Mon
    assert data[0]["duration_min"] == 60


# ---------------------------------------------------------------------------
# 2. from-week: mode auto detect (special)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_special_mode_auto_detect(client, db) -> None:
    """special_week_active 該当週 → special で固定化される."""
    admin = await _make_user(db, "fw-2-admin@example.com", "admin")
    patient = await _make_patient(
        db,
        "FW-002",
        special_week_active=[{"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK}],
    )

    # 火曜 (weekday=1, 2026-05-05) に visit
    await _make_visit(db, patient=patient, visit_date=date(2026, 5, 5), start="10:00", end="11:00")

    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) == 1
    assert data[0]["mode"] == "special"
    assert data[0]["weekday"] == 1  # Tue


# ---------------------------------------------------------------------------
# 3. from-week: mode 明示指定で上書き
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_explicit_mode_override(client, db) -> None:
    """mode=special を明示すれば special_week_active に関係なく special で固定化."""
    admin = await _make_user(db, "fw-3-admin@example.com", "admin")
    patient = await _make_patient(db, "FW-003")  # special_week_active は空

    # 水曜 (weekday=2, 2026-05-06) に visit
    await _make_visit(db, patient=patient, visit_date=date(2026, 5, 6), start="14:00", end="15:00")

    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}&mode=special",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) == 1
    assert data[0]["mode"] == "special"
    assert data[0]["weekday"] == 2  # Wed


# ---------------------------------------------------------------------------
# 4. from-week: visits 0 件 → 既存削除して空配列返す
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_zero_visits_clears_existing(client, db) -> None:
    """visits 0 件 → 既存 fixed_visits を削除して空配列返す."""
    admin = await _make_user(db, "fw-4-admin@example.com", "admin")
    patient = await _make_patient(db, "FW-004")

    # 先に固定枠を登録
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.commit()

    # 当該週に visits を作らない (0 件)
    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data == []

    # DB からも消えていること
    remaining = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# 5. from-week: 同一 weekday に複数 visit → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_duplicate_weekday_409(client, db) -> None:
    """同一曜日に複数 visit → 409."""
    admin = await _make_user(db, "fw-5-admin@example.com", "admin")
    patient = await _make_patient(db, "FW-005")

    # 月曜に 2 つ visit
    await _make_visit(db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00")
    await _make_visit(db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="14:00", end="15:00")

    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert res.status_code == 409, res.text


# ---------------------------------------------------------------------------
# 6. from-week: staff 権限 → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_staff_forbidden(client, db) -> None:
    """staff ロールは from-week を呼べない (403)."""
    staff = await _make_staff(db, "スタッフ6")
    staff_user = await _make_user(db, "fw-6-staff@example.com", "staff", staff_id=staff.id)
    patient = await _make_patient(db, "FW-006")

    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 7. from-week-bulk: 全患者一括書き戻し → updated_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_bulk_updated_count(client, db) -> None:
    """全 active 患者に対して固定化 → updated_count が返る."""
    admin = await _make_user(db, "fwb-7-admin@example.com", "admin")
    p1 = await _make_patient(db, "FWB-001")
    p2 = await _make_patient(db, "FWB-002")

    # p1: 月曜に visit
    await _make_visit(db, patient=p1, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00")
    # p2: 火曜に visit
    await _make_visit(db, patient=p2, visit_date=date(2026, 5, 5), start="10:00", end="11:00")

    res = await client.post(
        f"/api/v1/patients/fixed-visits/from-week-bulk"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["updated_count"] >= 2
    assert str(p1.id) in data["patients"]
    assert str(p2.id) in data["patients"]


# ---------------------------------------------------------------------------
# 8. from-week-bulk: 冪等 (2 度実行で同結果)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_bulk_idempotent(client, db) -> None:
    """2 度実行しても同じ updated_count / 同じ固定枠データ."""
    admin = await _make_user(db, "fwb-8-admin@example.com", "admin")
    patient = await _make_patient(db, "FWB-003")

    await _make_visit(db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00")

    url = (
        f"/api/v1/patients/fixed-visits/from-week-bulk"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}"
    )

    res1 = await client.post(url, headers=_bearer(admin))
    assert res1.status_code == 200, res1.text
    data1 = res1.json()

    res2 = await client.post(url, headers=_bearer(admin))
    assert res2.status_code == 200, res2.text
    data2 = res2.json()

    assert data1["updated_count"] == data2["updated_count"]

    # DB の固定枠数は変わらない
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 9. from-week-bulk: admin/manager のみ (staff → 403)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_bulk_staff_forbidden(client, db) -> None:
    """staff ロールは from-week-bulk を呼べない."""
    staff = await _make_staff(db, "スタッフ9")
    staff_user = await _make_user(db, "fwb-9-staff@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        f"/api/v1/patients/fixed-visits/from-week-bulk"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 10. fix-or-pattern: this_week_only → visit 単独 update、fixed_visits 不変
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_or_pattern_this_week_only(client, db) -> None:
    """this_week_only: visit のみ更新、patient_fixed_visits は変化しない."""
    admin = await _make_user(db, "fop-10-admin@example.com", "admin")
    patient = await _make_patient(db, "FOP-001")

    # 固定枠を事前に登録
    fv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(9, 0),
        duration_min=60,
    )
    db.add(fv)
    await db.commit()
    await db.refresh(fv)

    # visit を作成
    v = await _make_visit(
        db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00"
    )

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "mode": "this_week_only",
            "visit_id": str(v.id),
            "new_weekday": 1,  # Tue
            "new_start_time": "11:00",
            "new_duration_min": 45,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["mode"] == "this_week_only"
    assert data["updated_visit"] is not None
    assert data["updated_fixed_visit"] is None

    # fixed_visits は変わっていない
    await db.refresh(fv)
    assert fv.weekday == 0  # 変化なし
    assert fv.start_time == time(9, 0)


# ---------------------------------------------------------------------------
# 11. fix-or-pattern: pattern_change (normal) → fixed_visits + visit 更新
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_or_pattern_pattern_change_normal(client, db) -> None:
    """pattern_change (normal): patient_fixed_visits + 当該週 visit が更新される."""
    admin = await _make_user(db, "fop-11-admin@example.com", "admin")
    patient = await _make_patient(db, "FOP-002")

    v = await _make_visit(
        db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00"
    )

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "mode": "pattern_change",
            "visit_id": str(v.id),
            "new_weekday": 2,  # Wed
            "new_start_time": "14:00",
            "new_duration_min": 60,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["mode"] == "pattern_change"
    assert data["updated_visit"] is not None
    assert data["updated_fixed_visit"] is not None
    assert data["updated_fixed_visit"]["mode"] == "normal"
    assert data["updated_fixed_visit"]["weekday"] == 2
    assert data["updated_fixed_visit"]["duration_min"] == 60

    # DB 確認
    fv_row = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.weekday == 2,
        )
    )
    assert fv_row is not None
    assert fv_row.start_time == time(14, 0)


# ---------------------------------------------------------------------------
# 12. fix-or-pattern: pattern_change (special) → special fixed_visits 更新
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_or_pattern_pattern_change_special(client, db) -> None:
    """pattern_change: special_week_active 該当週 → special mode で固定枠更新."""
    admin = await _make_user(db, "fop-12-admin@example.com", "admin")
    patient = await _make_patient(
        db,
        "FOP-003",
        special_week_active=[{"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK}],
    )

    v = await _make_visit(
        db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00"
    )

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "mode": "pattern_change",
            "visit_id": str(v.id),
            "new_weekday": 3,  # Thu
            "new_start_time": "10:00",
            "new_duration_min": 30,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["updated_fixed_visit"]["mode"] == "special"
    assert data["updated_fixed_visit"]["weekday"] == 3

    # DB: normal 側は変わっていない
    normal_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
            )
        )
    ).all()
    assert len(normal_rows) == 0


# ---------------------------------------------------------------------------
# 13. fix-or-pattern: mode 不正値 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_or_pattern_invalid_mode_422(client, db) -> None:
    """mode に不正値を送ると 422."""
    admin = await _make_user(db, "fop-13-admin@example.com", "admin")
    import uuid

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "mode": "invalid_mode",
            "visit_id": str(uuid.uuid4()),
            "new_weekday": 0,
            "new_start_time": "09:00",
            "new_duration_min": 60,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# M2: fix-or-pattern の RBAC テスト (staff → 403)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_or_pattern_staff_forbidden(client, db) -> None:
    """staff ロールは fix-or-pattern を呼べない (403)."""
    import uuid

    staff = await _make_staff(db, "スタッフ14")
    staff_user = await _make_user(db, "fop-14-staff@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(staff_user),
        json={
            "mode": "this_week_only",
            "visit_id": str(uuid.uuid4()),
            "new_weekday": 0,
            "new_start_time": "09:00",
            "new_duration_min": 60,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_fix_or_pattern_viewer_forbidden(client, db) -> None:
    """viewer ロールは fix-or-pattern を呼べない (403)."""
    import uuid

    viewer = await _make_user(db, "fop-15-viewer@example.com", "viewer")

    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(viewer),
        json={
            "mode": "pattern_change",
            "visit_id": str(uuid.uuid4()),
            "new_weekday": 1,
            "new_start_time": "10:00",
            "new_duration_min": 30,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# (中-1) fix-or-pattern: pattern_change で course_template_id が引き継がれる
# ---------------------------------------------------------------------------


async def _make_office(db, name: str = "事業所X") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_course_template(db, office_id, label: str = "A") -> CourseTemplate:
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


@pytest.mark.asyncio
async def test_fix_or_pattern_preserves_course_template_id(client, db) -> None:
    """pattern_change 後も旧 pfv.course_template_id が新 pfv に引き継がれること."""
    admin = await _make_user(db, "fop-ct-admin@example.com", "admin")
    patient = await _make_patient(db, "FOP-CT-001")

    # course_template を用意
    office = await _make_office(db, "CT-事業所")
    tpl = await _make_course_template(db, office.id, label="Z")

    # 月曜 (weekday=0) に visit を作成
    v = await _make_visit(
        db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00"
    )

    # 既存 pfv を作成 (weekday=0, course_template_id=tpl.id)
    existing_fv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(9, 0),
        duration_min=60,
        course_template_id=tpl.id,
    )
    db.add(existing_fv)
    await db.commit()

    # pattern_change で weekday を 0→2 (Wed) に変更
    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=_bearer(admin),
        json={
            "mode": "pattern_change",
            "visit_id": str(v.id),
            "new_weekday": 2,
            "new_start_time": "14:00",
            "new_duration_min": 60,
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["updated_fixed_visit"] is not None
    assert data["updated_fixed_visit"]["weekday"] == 2

    # DB 確認: 新 pfv (weekday=2) に course_template_id が引き継がれていること
    new_fv_row = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.weekday == 2,
        )
    )
    assert new_fv_row is not None
    assert new_fv_row.course_template_id == tpl.id


# ---------------------------------------------------------------------------
# (中-2) from-week: visit.course_id → pfv.course_template_id の逆引きが動く
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_from_week_saves_course_template_id(client, db) -> None:
    """from-week で visit.course_id 経由の course_template_id が pfv に保存されること."""
    admin = await _make_user(db, "fw-ct-admin@example.com", "admin")
    patient = await _make_patient(db, "FW-CT-001")

    # Course / CourseTemplate を用意
    office = await _make_office(db, "FW-事業所")
    tpl = await _make_course_template(db, office.id, label="W")
    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        office_id=office.id,
        template_id=tpl.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)

    # visit に course_id を紐付け
    v = await _make_visit(
        db, patient=patient, visit_date=TEST_WEEK_MONDAY, start="09:00", end="10:00"
    )
    v.course_id = course.id
    db.add(v)
    await db.commit()

    res = await client.post(
        f"/api/v1/patients/{patient.id}/fixed-visits/from-week"
        f"?iso_year={TEST_ISO_YEAR}&iso_week={TEST_ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) == 1
    assert data[0]["course_template_id"] == str(tpl.id)

    # DB 確認
    pfv_row = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == patient.id,
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv_row is not None
    assert pfv_row.course_template_id == tpl.id
