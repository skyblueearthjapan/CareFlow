"""W37 Phase 2-A: place-and-fix 複数スタッフ対応 (2 名体制) テスト.

検証観点:
  1. staff_count=2 + course_template_ids 2 件 + fix_pattern=true
     → 2 visit + 2 fixed_visit が visit_group_id 共有で作成される.
  2. staff_count=2 で同一 course_template_id 2 つ → 422 (= 同コース 2 visit 不可).
  3. staff_count=2 で course_template_ids 1 件 → 422 (件数不一致).
  4. staff_count=1 で course_template_ids 2 件 → 422 (件数不一致).
  5. staff_count=2 + 旧形式 course_template_id → 422 (旧形式は 1 名体制のみ).
  6. course_template_id と course_template_ids 両方指定 → 422.
  7. どちらも未指定 → 422.
  8. patient.requires_multiple_staff=true で staff_count=1 → 422 (整合性).
  9. staff_count=2 + fix_pattern=false → 2 visit 作成、固定枠なし.
  10. staff_count=2 で同 (mode, weekday) を 2 回 call → upsert で slot 0/1 が
      上書きされ計 2 行のまま (visits は毎回 +2 で増える).
  11. staff_count=2 後方互換: response の visit/fixed_visit に 1 件目が入る.
  12. partial UNIQUE: staff_count=2 配置後、両 visit の (patient, date, time)
      が同じでも visit_group_id 違いで共存している (DB レベル整合性).
  13. visit_group_id ≠ NULL かつ 2 visit に同じ UUID.
"""

from __future__ import annotations

from datetime import date, time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient_fixed_visit import PatientFixedVisit

# 共通テスト週: ISO 2026-W19 (月曜 = 2026-05-04)
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEK_MONDAY = date(2026, 5, 4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str = "admin", staff_id=None) -> User:
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
    requires_multiple_staff: bool = False,
    primary_office_id=None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        requires_multiple_staff=requires_multiple_staff,
        primary_office_id=primary_office_id,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_office(db, name: str = "事業所") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


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


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _multi_payload(
    patient_id,
    *,
    course_template_ids: list[UUID] | None = None,
    course_template_id: UUID | None = None,
    iso_year: int = TEST_ISO_YEAR,
    iso_week: int = TEST_ISO_WEEK,
    weekday: int = 0,
    start_time: str = "09:00:00",
    duration_min: int = 60,
    staff_count: int = 2,
    fix_pattern: bool = True,
) -> dict:
    body: dict = {
        "patient_id": str(patient_id),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "weekday": weekday,
        "start_time": start_time,
        "duration_min": duration_min,
        "staff_count": staff_count,
        "fix_pattern": fix_pattern,
    }
    if course_template_ids is not None:
        body["course_template_ids"] = [str(t) for t in course_template_ids]
    if course_template_id is not None:
        body["course_template_id"] = str(course_template_id)
    return body


# ---------------------------------------------------------------------------
# 1. 正常系: staff_count=2 + 2 つの異なる course_template + fix_pattern=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_creates_two_visits_and_two_fixed_visits(client, db) -> None:
    admin = await _make_user(db, "paf-multi-1@example.com")
    office = await _make_office(db, "事業所MULTI1")
    patient = await _make_patient(db, "PAF-MULTI-1", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            weekday=0,
            start_time="10:00:00",
            duration_min=45,
            staff_count=2,
            fix_pattern=True,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # 後方互換: 単数フィールドにも 1 件目が入る
    assert data["visit"] is not None
    assert data["fixed_visit"] is not None
    # 新形式: 配列 2 件
    assert len(data["visits"]) == 2
    assert len(data["fixed_visits"]) == 2
    # visit_group_id は非 NULL かつ 2 visit で共有
    assert data["visit_group_id"] is not None
    vg_id = data["visit_group_id"]
    assert all(v["visit_group_id"] == vg_id for v in data["visits"])
    # 2 visit の course_id は異なる (= 別 template から派生)
    assert data["visits"][0]["course_id"] != data["visits"][1]["course_id"]
    # required_staff_count=2
    assert all(v["required_staff_count"] == 2 for v in data["visits"])

    # DB 確認
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 2
    assert visits[0].visit_group_id == visits[1].visit_group_id
    assert visits[0].visit_group_id is not None
    # 2 visit は同 visit_date / 同 start_time
    assert visits[0].visit_date == TEST_WEEK_MONDAY
    assert visits[1].visit_date == TEST_WEEK_MONDAY
    assert visits[0].start_time == time(10, 0)
    assert visits[1].start_time == time(10, 0)
    # course_id は異なる
    assert visits[0].course_id != visits[1].course_id

    # fixed visits: slot_index=0 と 1 の 2 行
    fvs = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(PatientFixedVisit.patient_id == patient.id)
            .order_by(PatientFixedVisit.slot_index)
        )
    ).all()
    assert len(fvs) == 2
    assert fvs[0].slot_index == 0
    assert fvs[1].slot_index == 1
    assert fvs[0].course_template_id != fvs[1].course_template_id
    assert {fvs[0].course_template_id, fvs[1].course_template_id} == {tpl_a.id, tpl_b.id}
    # mode は 'normal'
    assert fvs[0].mode == "normal"
    assert fvs[1].mode == "normal"


# ---------------------------------------------------------------------------
# 2. staff_count=2 で同一 course_template_id 2 つ → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_same_template_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-multi-2@example.com")
    office = await _make_office(db, "事業所MULTI2")
    patient = await _make_patient(db, "PAF-MULTI-2", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_a.id],
            staff_count=2,
        ),
    )
    assert res.status_code == 422, res.text
    assert "course_template" in res.text or "異なる" in res.text or "2 つの異なる" in res.text


# ---------------------------------------------------------------------------
# 3. staff_count=2 で 1 件のみ → 422 (件数不一致)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_count_mismatch_one_id(client, db) -> None:
    admin = await _make_user(db, "paf-multi-3@example.com")
    office = await _make_office(db, "事業所MULTI3")
    patient = await _make_patient(db, "PAF-MULTI-3", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id],
            staff_count=2,
        ),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 4. staff_count=1 で 2 件 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_count_mismatch_two_ids_with_staff_one(client, db) -> None:
    admin = await _make_user(db, "paf-multi-4@example.com")
    office = await _make_office(db, "事業所MULTI4")
    patient = await _make_patient(db, "PAF-MULTI-4", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            staff_count=1,
        ),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 5. 旧形式 course_template_id + staff_count=2 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_legacy_id_with_staff_count_2_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-multi-5@example.com")
    office = await _make_office(db, "事業所MULTI5")
    patient = await _make_patient(db, "PAF-MULTI-5", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_id=tpl_a.id,
            staff_count=2,
        ),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 6. course_template_id と course_template_ids 両方指定 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_both_id_forms_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-multi-6@example.com")
    office = await _make_office(db, "事業所MULTI6")
    patient = await _make_patient(db, "PAF-MULTI-6", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_id=tpl_a.id,
            course_template_ids=[tpl_a.id],
            staff_count=1,
        ),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 7. course_template_id / ids どちらも未指定 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_no_template_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-multi-7@example.com")
    patient = await _make_patient(db, "PAF-MULTI-7")

    body = {
        "patient_id": str(patient.id),
        "iso_year": TEST_ISO_YEAR,
        "iso_week": TEST_ISO_WEEK,
        "weekday": 0,
        "start_time": "09:00:00",
        "duration_min": 30,
        "staff_count": 1,
        "fix_pattern": True,
    }
    res = await client.post("/api/v1/schedule/place-and-fix", headers=_bearer(admin), json=body)
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 8. patient.requires_multiple_staff=true で staff_count=1 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_requires_multiple_staff_mismatch_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-multi-8@example.com")
    office = await _make_office(db, "事業所MULTI8")
    patient = await _make_patient(
        db,
        "PAF-MULTI-8",
        requires_multiple_staff=True,
        primary_office_id=office.id,
    )
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id],
            staff_count=1,
        ),
    )
    assert res.status_code == 422, res.text
    assert "requires_multiple_staff" in res.text or "staff_count=2" in res.text


# ---------------------------------------------------------------------------
# 9. staff_count=2 + fix_pattern=false → 2 visit 作成, 固定枠 0 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_no_pattern(client, db) -> None:
    admin = await _make_user(db, "paf-multi-9@example.com")
    office = await _make_office(db, "事業所MULTI9")
    patient = await _make_patient(db, "PAF-MULTI-9", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            staff_count=2,
            fix_pattern=False,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["visits"]) == 2
    assert len(data["fixed_visits"]) == 0
    assert data["fixed_visit"] is None

    # DB
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 2
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 0


# ---------------------------------------------------------------------------
# 10. 同 (mode, weekday) を 2 回 call → fixed_visit が upsert される (slot 0/1 計 2 行のまま)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_upserts_existing_slots(client, db) -> None:
    admin = await _make_user(db, "paf-multi-10@example.com")
    office = await _make_office(db, "事業所MULTI10")
    patient = await _make_patient(db, "PAF-MULTI-10", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")
    tpl_c = await _make_template(db, office.id, label="C")
    tpl_d = await _make_template(db, office.id, label="D")

    # 1 回目: A + B
    r1 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            weekday=2,
            start_time="09:00:00",
            duration_min=30,
            staff_count=2,
        ),
    )
    assert r1.status_code == 200, r1.text

    # 2 回目: 同 weekday=2 を C + D で再投入 (時刻/duration 変更)
    r2 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_c.id, tpl_d.id],
            weekday=2,
            start_time="14:00:00",
            duration_min=60,
            staff_count=2,
        ),
    )
    assert r2.status_code == 200, r2.text

    # DB: 当該 (patient, mode='normal', weekday=2) は依然 2 行 (slot 0/1)
    fvs = (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.weekday == 2,
            )
            .order_by(PatientFixedVisit.slot_index)
        )
    ).all()
    assert len(fvs) == 2
    # 最新の値 (C/D, 14:00, 60) で更新されている
    assert {fvs[0].course_template_id, fvs[1].course_template_id} == {tpl_c.id, tpl_d.id}
    assert fvs[0].start_time == time(14, 0)
    assert fvs[0].duration_min == 60

    # visits は 4 行 (1 回目 2 + 2 回目 2). visit_group_id はそれぞれ別.
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 4
    group_ids = {v.visit_group_id for v in visits}
    # 2 回呼んだので 2 つの異なる UUID が出現
    assert len(group_ids) == 2
    assert None not in group_ids


# ---------------------------------------------------------------------------
# 11. 後方互換: staff_count=1 + 旧形式 course_template_id (既存挙動が壊れない regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_legacy_single_id_still_works(client, db) -> None:
    """staff_count=1 + 旧形式 course_template_id で従来挙動どおり動く (regression)."""
    admin = await _make_user(db, "paf-multi-11@example.com")
    office = await _make_office(db, "事業所MULTI11")
    patient = await _make_patient(db, "PAF-MULTI-11", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_id=tpl_a.id,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
            staff_count=1,
            fix_pattern=True,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    # 1 visit / 1 fixed_visit
    assert len(data["visits"]) == 1
    assert len(data["fixed_visits"]) == 1
    # visit_group_id は None (1 名体制)
    assert data["visit_group_id"] is None
    assert data["visits"][0]["visit_group_id"] is None
    # 後方互換フィールドにも入っている
    assert data["visit"] is not None
    assert data["fixed_visit"] is not None

    # DB: visits 1 件, fixed_visit 1 件 (slot=0)
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 1
    assert visits[0].visit_group_id is None

    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 1
    assert fvs[0].slot_index == 0


# ---------------------------------------------------------------------------
# 12. 新形式 course_template_ids 1 件 + staff_count=1 (新形式で 1 名体制を表現)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_new_form_single_works(client, db) -> None:
    """新形式 course_template_ids=[tpl] + staff_count=1 でも 1 名体制として動く."""
    admin = await _make_user(db, "paf-multi-12@example.com")
    office = await _make_office(db, "事業所MULTI12")
    patient = await _make_patient(db, "PAF-MULTI-12", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id],
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
            staff_count=1,
            fix_pattern=True,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["visits"]) == 1
    assert data["visit_group_id"] is None


# ---------------------------------------------------------------------------
# 13. visit DELETE cascade_partner=true (default) → 2 visit 同時 soft-delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_delete_cascade_partner_default_removes_both(client, db) -> None:
    """W37 Phase 2-A: visit_group_id を共有する 2 visit を 1 つの DELETE で
    両方 soft-delete する (cascade_partner=true がデフォルト)."""
    admin = await _make_user(db, "paf-multi-del-1@example.com")
    office = await _make_office(db, "事業所DEL1")
    patient = await _make_patient(db, "PAF-DEL-1", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    # 2 名体制で配置
    r = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            staff_count=2,
            fix_pattern=False,  # 固定枠は今回スコープ外
        ),
    )
    assert r.status_code == 200, r.text
    visit_ids = [v["id"] for v in r.json()["visits"]]
    assert len(visit_ids) == 2

    # 片方 (1 件目) を DELETE — partner も巻き込まれて両方 soft-delete されるはず.
    res = await client.delete(
        f"/api/v1/visits/{visit_ids[0]}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # DB 確認: 両方とも deleted_at セット (active 0 件)
    db.expunge_all()
    active = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(active) == 0, "cascade_partner=true (default) で 2 visit 共に soft-delete"


# ---------------------------------------------------------------------------
# 14. visit DELETE cascade_partner=false → 片方のみ soft-delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_delete_cascade_partner_false_removes_one(client, db) -> None:
    """cascade_partner=false で当該 visit のみ soft-delete し、partner は残る."""
    admin = await _make_user(db, "paf-multi-del-2@example.com")
    office = await _make_office(db, "事業所DEL2")
    patient = await _make_patient(db, "PAF-DEL-2", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    r = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            staff_count=2,
            fix_pattern=False,
        ),
    )
    assert r.status_code == 200, r.text
    visit_ids = [v["id"] for v in r.json()["visits"]]

    # 片方を DELETE w/ cascade_partner=false
    res = await client.delete(
        f"/api/v1/visits/{visit_ids[0]}?cascade_partner=false",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # DB: active 1 件 (partner) のみ残る
    db.expunge_all()
    active = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(active) == 1
    assert str(active[0].id) == visit_ids[1], "partner (2 件目) のみ残るはず"


# ---------------------------------------------------------------------------
# 15. cascade_partner + cascade_fixed_visit 併用 → 2 visit + 全 slot 固定枠を一掃
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_delete_cascade_partner_and_fixed_visit(client, db) -> None:
    """cascade_partner=true & cascade_fixed_visit=true で
    2 visit + 同 weekday の固定枠 (slot 0/1, normal/special) を全削除."""
    admin = await _make_user(db, "paf-multi-del-3@example.com")
    office = await _make_office(db, "事業所DEL3")
    patient = await _make_patient(db, "PAF-DEL-3", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    r = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            weekday=0,  # Mon
            start_time="09:00:00",
            staff_count=2,
            fix_pattern=True,  # 固定枠 2 件作成
        ),
    )
    assert r.status_code == 200, r.text
    visit_ids = [v["id"] for v in r.json()["visits"]]

    # cascade 両指定
    res = await client.delete(
        f"/api/v1/visits/{visit_ids[0]}?cascade_partner=true&cascade_fixed_visit=true",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # DB: visits は 0 件 active, 固定枠も 0 件
    db.expunge_all()
    active_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient.id,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(active_visits) == 0
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 0, "cascade_fixed_visit=true で slot 0/1 全削除されるはず"


# ---------------------------------------------------------------------------
# 16. requires_multiple_staff=true 患者に staff_count=2 で配置 → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_requires_multiple_staff_with_two_succeeds(client, db) -> None:
    admin = await _make_user(db, "paf-multi-16@example.com")
    office = await _make_office(db, "事業所MULTI16")
    patient = await _make_patient(
        db,
        "PAF-MULTI-16",
        requires_multiple_staff=True,
        primary_office_id=office.id,
    )
    tpl_a = await _make_template(db, office.id, label="A")
    tpl_b = await _make_template(db, office.id, label="B")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, tpl_b.id],
            staff_count=2,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["visits"]) == 2


# ---------------------------------------------------------------------------
# 17. staff_count=2 で 1 つだけ不明な template (片方 404) → 全 rollback (整合性)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_multi_one_unknown_template_returns_404(client, db) -> None:
    """2 つの course_template のうち片方が存在しない → 404 で全 rollback."""
    admin = await _make_user(db, "paf-multi-17@example.com")
    office = await _make_office(db, "事業所MULTI17")
    patient = await _make_patient(db, "PAF-MULTI-17", primary_office_id=office.id)
    tpl_a = await _make_template(db, office.id, label="A")

    bogus_id: UUID = uuid4()
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_multi_payload(
            patient.id,
            course_template_ids=[tpl_a.id, bogus_id],
            staff_count=2,
        ),
    )
    assert res.status_code == 404, res.text

    # DB: visits / fixed_visits は何も作られていない (整合性: 全 rollback).
    # 別 session で確認 (test session の identity-map をバイパス + aiosqlite の
    # in-flight statement が残らないようにする).
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as verify_session:
        visits = (
            await verify_session.scalars(select(Visit).where(Visit.patient_id == patient.id))
        ).all()
        assert len(visits) == 0
        fvs = (
            await verify_session.scalars(
                select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
            )
        ).all()
        assert len(fvs) == 0
