"""W16 codex fix: 一括修正項目の回帰テスト.

検証項目:
  1. 重大1 — Layer 1 が visit.course_id を埋める (primary_office_id 経由で
     default course_template から (template, year, week, weekday) → Course を解決)
  2. 重大2 — generate-and-assign の office_id スコープ (別拠点の visit を
     破壊しない)
  3. 中1 — generate-and-assign の office_id 不存在 → 404
  4. 中2 — courses.code='E' (本店 A-E ラベル) が CHECK 制約で正常 INSERT できる
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_PROPOSED, Course
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.services.scheduling.layer1_expander import Layer1Expander

# ISO 2026-W22 — Monday is 2026-05-25.
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 22
TEST_WEEK_MONDAY = date(2026, 5, 25)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, name: str = "本店") -> Office:
    o = Office(name=name, lat=35.6383, lng=140.1041)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_template(db, *, office_id, label: str = "A") -> CourseTemplate:
    tpl = CourseTemplate(
        office_id=office_id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=4,
        capacity_sun=0,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def _make_patient(db, *, code: str, primary_office_id, name: str | None = None) -> Patient:
    p = Patient(
        code=code,
        name=name or f"患者 {code}",
        status="active",
        primary_office_id=primary_office_id,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# 1) 重大1: Layer 1 が visit.course_id を埋める
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_layer1_fills_visit_course_id_via_template(db) -> None:
    """primary_office_id がある patient → Layer 1 が visit.course_id を埋める."""
    expander = Layer1Expander()
    office = await _make_office(db, name="本店(稲毛)")
    await _make_template(db, office_id=office.id, label="A")

    patient = await _make_patient(db, code="W16-CFIX-001", primary_office_id=office.id)
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,  # Mon
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.commit()

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    assert result.visits_created_count == 1

    # DB 検証: visit に course_id が埋まっている
    rows = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(rows) == 1
    visit = rows[0]
    assert visit.course_id is not None, (
        "Layer 1 が visit.course_id=None を生成 (重大1 リグレッション)"
    )

    # Course が (template, year, week, weekday) で生成されているか
    course = await db.scalar(select(Course).where(Course.id == visit.course_id))
    assert course is not None
    assert course.iso_year == TEST_ISO_YEAR
    assert course.iso_week == TEST_ISO_WEEK
    assert course.weekday == 0
    assert course.office_id == office.id
    assert course.code == "A"  # template.label='A' → code='A'
    assert course.course_status == COURSE_STATUS_PROPOSED


@pytest.mark.asyncio
async def test_layer1_no_office_keeps_course_id_null(db) -> None:
    """primary_office_id 未設定の patient は course_id=None のまま (旧挙動互換)."""
    expander = Layer1Expander()

    patient = Patient(
        code="W16-CFIX-002",
        name="拠点なし患者",
        status="active",
        primary_office_id=None,
        weekly_pattern={
            "frequency_per_week": 1,
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "09:00",
                    "preferred_end": "10:00",
                    "service_minutes": 60,
                    "staff_count": 1,
                }
            ],
        },
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    result = await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    assert result.visits_created_count == 1
    rows = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(rows) == 1
    assert rows[0].course_id is None  # 旧挙動: 拠点なしは NULL のまま


@pytest.mark.asyncio
async def test_layer1_skips_manager_template_uses_a_first(db) -> None:
    """A label と M label が同じ拠点に存在する場合、A を優先する."""
    expander = Layer1Expander()
    office = await _make_office(db, name="優先テスト")
    # M を先に作る (label でソートしても A < M なので A が優先される検証)
    await _make_template(db, office_id=office.id, label="M")
    tpl_a = await _make_template(db, office_id=office.id, label="A")

    patient = await _make_patient(db, code="W16-CFIX-003", primary_office_id=office.id)
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=1,  # Tue
            start_time=time(10, 0),
            duration_min=60,
        )
    )
    await db.commit()

    await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    visit = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).first()
    assert visit is not None and visit.course_id is not None
    course = await db.scalar(select(Course).where(Course.id == visit.course_id))
    assert course is not None
    assert course.template_id == tpl_a.id
    assert course.code == "A"


# ---------------------------------------------------------------------------
# 2) 重大2: office_id スコープで別拠点を破壊しない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_office_filter_does_not_destroy_other_office(client, db) -> None:
    """office_id 指定時、別拠点の auto-visit が削除されない."""
    admin = await _make_user(db, "ga-w16-1@example.com", "admin")

    # 拠点 A
    office_a = await _make_office(db, name="拠点 A")
    tpl_a = await _make_template(db, office_id=office_a.id, label="A")
    patient_a = await _make_patient(
        db, code="W16-OA-1", primary_office_id=office_a.id, name="患者 A"
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient_a.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )

    # 拠点 B (生成 後に保護されるべき)
    office_b = await _make_office(db, name="拠点 B")
    tpl_b = await _make_template(db, office_id=office_b.id, label="A")
    patient_b = await _make_patient(
        db, code="W16-OB-1", primary_office_id=office_b.id, name="患者 B"
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient_b.id,
            mode="normal",
            weekday=0,
            start_time=time(13, 0),
            duration_min=60,
        )
    )

    # 各拠点 1 staff (Layer 3 が動くために最低限)
    for office in (office_a, office_b):
        s = Staff(
            code=f"S-{office.name[:2]}",
            name=f"スタッフ-{office.name}",
            role="staff",
            status="active",
            primary_office_id=office.id,
        )
        db.add(s)
        await db.flush()
        for wd in range(6):
            db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.commit()

    # 1 回目: 全拠点 (office_id=None) で実行 → 両拠点に visits ができる
    res_all = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res_all.status_code == 200, res_all.text

    # 拠点 B の visit (= patient_b の auto visit) を記録
    visits_b_before = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient_b.id, Visit.source == "auto")
        )
    ).all()
    visits_b_ids_before = {v.id for v in visits_b_before}
    assert len(visits_b_ids_before) >= 1, "1 回目で拠点 B の visit が作られていない"

    # 2 回目: office_id=拠点 A だけ指定 → 拠点 A は再生成、拠点 B は不変
    res_a = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "office_id": str(office_a.id),
        },
    )
    assert res_a.status_code == 200, res_a.text

    # 拠点 B の visit は変わっていない (id 一致)
    visits_b_after = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient_b.id, Visit.source == "auto")
        )
    ).all()
    visits_b_ids_after = {v.id for v in visits_b_after}
    assert visits_b_ids_after == visits_b_ids_before, (
        "office_id=A 指定で拠点 B の visit が削除/再生成された (重大2 リグレッション)"
    )

    # 拠点 A の visit は再生成されている (新 id)
    visits_a_after = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient_a.id, Visit.source == "auto")
        )
    ).all()
    assert len(visits_a_after) >= 1
    # 念のため: tpl_a と tpl_b は各々の office.id を持つ
    assert tpl_a.office_id == office_a.id
    assert tpl_b.office_id == office_b.id


# ---------------------------------------------------------------------------
# 3) 中1: 不存在 office_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_and_assign_unknown_office_returns_404(client, db) -> None:
    """不存在の office_id を渡すと 404 を返す (silently no-op しない)."""
    admin = await _make_user(db, "ga-w16-404@example.com", "admin")
    bogus_office_id = uuid4()

    res = await client.post(
        "/api/v1/schedule/generate-and-assign",
        headers=_bearer(admin),
        json={
            "iso_year": TEST_ISO_YEAR,
            "iso_week": TEST_ISO_WEEK,
            "office_id": str(bogus_office_id),
        },
    )
    assert res.status_code == 404, res.text
    body = res.json()
    assert "Office" in body["detail"] or "office" in body["detail"].lower()


# ---------------------------------------------------------------------------
# 4) 中2: courses.code='E' が正常に INSERT できる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_courses_code_e_accepted_by_check_constraint(db) -> None:
    """CHECK 制約 ('A','B','C','D','E','M') により code='E' が INSERT できる."""
    office = await _make_office(db, name="E label テスト")

    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="E",  # 旧 CHECK では弾かれた
        course_status=COURSE_STATUS_PROPOSED,
        office_id=office.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)

    fetched = await db.scalar(select(Course).where(Course.id == course.id))
    assert fetched is not None
    assert fetched.code == "E"


@pytest.mark.asyncio
async def test_layer1_creates_e_code_course_from_e_template(db) -> None:
    """label='E' の course_template から Layer 1 が code='E' の Course を作成する."""
    expander = Layer1Expander()
    office = await _make_office(db, name="本店(E only)")
    await _make_template(db, office_id=office.id, label="E")

    patient = await _make_patient(db, code="W16-CFIX-E1", primary_office_id=office.id)
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=2,  # Wed
            start_time=time(11, 0),
            duration_min=60,
        )
    )
    await db.commit()

    await expander.expand_week(db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK)
    await db.commit()

    visit = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).first()
    assert visit is not None and visit.course_id is not None

    course = await db.scalar(select(Course).where(Course.id == visit.course_id))
    assert course is not None
    assert course.code == "E", f"E label テンプレ → code='E' のはずが {course.code!r}"
