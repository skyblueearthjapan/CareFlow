"""Tests for GET /api/v1/acceptance-matrix (受け入れ枠マトリックス, P1).

拠点 × 曜日 × 時間帯の ○△× 自動算出 + 常設手動上書き (effective) を検証する
read-only endpoint。ローカル SQLite のみ (本番 DB 禁止)。
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.acceptance_calendar import AcceptanceCalendar
from app.models.city import City
from app.models.course import COURSE_STATUS_PROPOSED, COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import OfficeCity
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # Mon


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(db, *, name="稲毛拠点", code="INAGE") -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_patient(db, *, office, code, lat=35.6, lng=140.1) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(
    db, *, office, staff, weekday=0, code="A", status=COURSE_STATUS_STAFF_ASSIGNED
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=status,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(db, *, patient, course, start: time, end: time) -> Visit:
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


def _url(**params) -> str:
    base = f"/api/v1/acceptance-matrix?iso_year={ISO_YEAR}&iso_week={ISO_WEEK}"
    for k, v in params.items():
        base += f"&{k}={v}"
    return base


def _monday(office_block: dict) -> dict:
    return next(d for d in office_block["days"] if d["weekday"] == 0)


def _cell(day: dict, hhmm: str) -> dict:
    return next(c for c in day["cells"] if c["time_slot"].startswith(hhmm))


@pytest.mark.asyncio
async def test_matrix_basic_structure(client, db) -> None:
    admin = await _make_user(db, email="am-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p = await _seed_patient(db, office=office, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_generated_any"] is True
    assert len(body["slots"]) == 8  # 10:00..17:00
    assert len(body["offices"]) == 1
    off = body["offices"][0]
    assert off["week_generated"] is True
    assert off["operating_weekdays"] == [0, 1, 2, 3, 4, 5]
    assert len(off["days"]) == 7
    mon = _monday(off)
    assert len(mon["cells"]) == 8
    # 11:00 は完全空き → ○、12:00 は昼休み → ×.
    assert _cell(mon, "11:00")["auto_status"] == "available"
    assert _cell(mon, "12:00")["auto_status"] == "unavailable"
    # source は auto.
    assert _cell(mon, "11:00")["source"] == "auto"


@pytest.mark.asyncio
async def test_effective_manual_override(client, db) -> None:
    admin = await _make_user(db, email="am-admin2@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p = await _seed_patient(db, office=office, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    # 常設手動: Mon 11:00 を × に上書き.
    db.add(
        AcceptanceCalendar(
            office_id=office.id, weekday=0, time_slot=time(11, 0), status="unavailable"
        )
    )
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    body = res.json()
    cell = _cell(_monday(body["offices"][0]), "11:00")
    assert cell["auto_status"] == "available"
    assert cell["manual_status"] == "unavailable"
    assert cell["effective_status"] == "unavailable"
    assert cell["source"] == "manual_standing"


@pytest.mark.asyncio
async def test_closed_day_all_unavailable(client, db) -> None:
    admin = await _make_user(db, email="am-admin3@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_course(db, office=office, staff=staff)
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    off = res.json()["offices"][0]
    sun = next(d for d in off["days"] if d["weekday"] == 6)  # 日曜は operating 外
    assert sun["office_closed"] is True
    assert all(c["auto_status"] == "unavailable" for c in sun["cells"])
    assert sun["cells"][0]["metrics"]["reasons"] == ["定休日"]


@pytest.mark.asyncio
async def test_week_not_generated(client, db) -> None:
    admin = await _make_user(db, email="am-admin4@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # proposed のみ (確定コースなし) → 週未生成扱い.
    await _seed_course(db, office=office, staff=staff, status=COURSE_STATUS_PROPOSED)
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    body = res.json()
    assert body["week_generated_any"] is False
    off = body["offices"][0]
    assert off["week_generated"] is False
    mon = _monday(off)
    assert _cell(mon, "11:00")["auto_status"] == "unavailable"
    assert _cell(mon, "11:00")["metrics"]["reasons"] == ["週未生成"]


@pytest.mark.asyncio
async def test_setup_state_diagnosis(client, db) -> None:
    """設定漏れ診断 (PO 要望 2026-08-10): なぜ○×が出ないのかを setup_state で返す。

    - proposed のみ (マネージャー在籍) → assignment_pending (自動スタッフ割当が未実行)
    - コースゼロ + マネージャー不在 → no_manager
    - コースゼロ + マネージャー在籍 → not_generated
    - 確定コースあり → null (正常)
    """
    admin = await _make_user(db, email="am-admin-setup@example.com", role="admin")

    # 拠点1: proposed のみ + マネージャー在籍 → assignment_pending
    office1, staff1 = await _seed_office_staff(db, name="拠点1", code="OF1")
    mgr1 = Staff(name="主任1", role="manager", is_trainee=False, primary_office_id=office1.id)
    db.add(mgr1)
    await _seed_course(db, office=office1, staff=staff1, status=COURSE_STATUS_PROPOSED)

    # 拠点2: コースゼロ + マネージャー不在 → no_manager
    office2 = Office(name="拠点2", code="OF2")
    db.add(office2)
    await db.flush()

    # 拠点3: コースゼロ + マネージャー在籍 → not_generated
    office3 = Office(name="拠点3", code="OF3")
    db.add(office3)
    await db.flush()
    mgr3 = Staff(name="主任3", role="manager", is_trainee=False, primary_office_id=office3.id)
    db.add(mgr3)

    # 拠点4: 確定コースあり → null
    office4, staff4 = await _seed_office_staff(db, name="拠点4", code="OF4")
    await _seed_course(db, office=office4, staff=staff4)
    await db.commit()

    res = await client.get(_url(), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    by_name = {o["office_name"]: o for o in res.json()["offices"]}
    assert by_name["拠点1"]["setup_state"] == "assignment_pending"
    assert by_name["拠点2"]["setup_state"] == "no_manager"
    assert by_name["拠点3"]["setup_state"] == "not_generated"
    assert by_name["拠点4"]["setup_state"] is None


@pytest.mark.asyncio
async def test_city_names_included(client, db) -> None:
    admin = await _make_user(db, email="am-admin5@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    city = City(prefecture="千葉県", name="稲毛区")
    db.add(city)
    await db.flush()
    db.add(OfficeCity(office_id=office.id, city_id=city.id))
    await _seed_course(db, office=office, staff=staff)
    await db.commit()

    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    off = res.json()["offices"][0]
    assert "稲毛区" in off["city_names"]


@pytest.mark.asyncio
async def test_all_offices_when_office_id_omitted(client, db) -> None:
    admin = await _make_user(db, email="am-admin6@example.com", role="admin")
    # 稲毛: 確定コース + visit あり / 都賀: 確定コースなし (週未生成).
    inage, inage_staff = await _seed_office_staff(db, name="稲毛拠点", code="INAGE")
    tsuga, _tsuga_staff = await _seed_office_staff(db, name="都賀拠点", code="TSUGA")
    course = await _seed_course(db, office=inage, staff=inage_staff)
    p = await _seed_patient(db, office=inage, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    await db.commit()

    res = await client.get(_url(), headers=_bearer(admin))  # office_id 未指定
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["week_generated_any"] is True
    by_name = {o["office_name"]: o for o in body["offices"]}
    assert {"稲毛拠点", "都賀拠点"} <= set(by_name)
    # 拠点ごとに独立判定: 稲毛は生成済み (11:00 ○), 都賀は週未生成 (×).
    assert by_name["稲毛拠点"]["week_generated"] is True
    assert _cell(_monday(by_name["稲毛拠点"]), "11:00")["auto_status"] == "available"
    assert by_name["都賀拠点"]["week_generated"] is False
    assert _cell(_monday(by_name["都賀拠点"]), "11:00")["auto_status"] == "unavailable"


@pytest.mark.asyncio
async def test_week_override_layering(client, db) -> None:
    admin = await _make_user(db, email="am-admin7@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p = await _seed_patient(db, office=office, code="EX1")
    await _seed_visit(db, patient=p, course=course, start=time(10, 0), end=time(10, 30))
    # 常設手動: Mon 11:00 = consult
    db.add(
        AcceptanceCalendar(office_id=office.id, weekday=0, time_slot=time(11, 0), status="consult")
    )
    await db.commit()

    # 初期: auto=available だが 常設 consult が effective。
    res = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    cell = _cell(_monday(res.json()["offices"][0]), "11:00")
    assert cell["auto_status"] == "available"
    assert cell["manual_status"] == "consult"
    assert cell["week_status"] is None
    assert cell["effective_status"] == "consult"
    assert cell["source"] == "manual_standing"

    # 週別上書き: Mon 11:00 = unavailable (常設より優先)。
    put = await client.put(
        "/api/v1/acceptance-matrix/week-override",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 0,
            "time_slot": "11:00:00",
            "status": "unavailable",
        },
    )
    assert put.status_code == 200, put.text

    res2 = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    cell2 = _cell(_monday(res2.json()["offices"][0]), "11:00")
    assert cell2["week_status"] == "unavailable"
    assert cell2["effective_status"] == "unavailable"
    assert cell2["source"] == "week_override"

    # upsert (再 PUT で更新) → available。
    put2 = await client.put(
        "/api/v1/acceptance-matrix/week-override",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 0,
            "time_slot": "11:00:00",
            "status": "available",
        },
    )
    assert put2.status_code == 200
    res3 = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    assert _cell(_monday(res3.json()["offices"][0]), "11:00")["week_status"] == "available"

    # 解除 → 常設 consult に戻る。
    dele = await client.delete(
        f"/api/v1/acceptance-matrix/week-override?office_id={office.id}"
        f"&iso_year={ISO_YEAR}&iso_week={ISO_WEEK}&weekday=0&time_slot=11:00:00",
        headers=_bearer(admin),
    )
    assert dele.status_code == 204
    res4 = await client.get(_url(office_id=office.id), headers=_bearer(admin))
    cell4 = _cell(_monday(res4.json()["offices"][0]), "11:00")
    assert cell4["week_status"] is None
    assert cell4["effective_status"] == "consult"
    assert cell4["source"] == "manual_standing"


@pytest.mark.asyncio
async def test_week_override_rbac_staff_forbidden(client, db) -> None:
    staffu = await _make_user(db, email="am-staff1@example.com", role="staff")
    office, _staff = await _seed_office_staff(db)
    await db.commit()
    res = await client.put(
        "/api/v1/acceptance-matrix/week-override",
        headers=_bearer(staffu),
        json={
            "office_id": str(office.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 0,
            "time_slot": "11:00:00",
            "status": "available",
        },
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_week_override_unknown_office_404(client, db) -> None:
    admin = await _make_user(db, email="am-admin8@example.com", role="admin")
    await db.commit()
    res = await client.put(
        "/api/v1/acceptance-matrix/week-override",
        headers=_bearer(admin),
        json={
            "office_id": "00000000-0000-0000-0000-0000000000ff",
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 0,
            "time_slot": "11:00:00",
            "status": "available",
        },
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_standing_override_all_offices_and_cross_week(client, db) -> None:
    admin = await _make_user(db, email="am-admin9@example.com", role="admin")
    await _seed_office_staff(db, name="稲毛拠点", code="INAGE")
    await _seed_office_staff(db, name="都賀拠点", code="TSUGA")
    await db.commit()

    def mon_cell(body, name):
        off = next(o for o in body["offices"] if o["office_name"] == name)
        return _cell(_monday(off), "11:00")

    # 全拠点に常設(毎週)上書き: Mon 11:00 = unavailable + コメント。
    put = await client.put(
        "/api/v1/acceptance-matrix/standing-override",
        headers=_bearer(admin),
        json={"weekday": 0, "time_slot": "11:00:00", "status": "unavailable", "notes": "全体会議"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["applied_office_count"] == 2

    # 当週: 両拠点に effective + note。
    res = await client.get(_url(), headers=_bearer(admin))
    for name in ("稲毛拠点", "都賀拠点"):
        c = mon_cell(res.json(), name)
        assert c["manual_status"] == "unavailable"
        assert c["effective_status"] == "unavailable"
        assert c["source"] == "manual_standing"
        assert c["note"] == "全体会議"

    # 別の週でも常設は効く (毎週共通・全週一律)。
    res2 = await client.get(
        f"/api/v1/acceptance-matrix?iso_year={ISO_YEAR}&iso_week=25", headers=_bearer(admin)
    )
    assert mon_cell(res2.json(), "稲毛拠点")["source"] == "manual_standing"

    # 全拠点まとめて解除。
    dele = await client.delete(
        "/api/v1/acceptance-matrix/standing-override?weekday=0&time_slot=11:00:00",
        headers=_bearer(admin),
    )
    assert dele.status_code == 204
    res3 = await client.get(_url(), headers=_bearer(admin))
    assert mon_cell(res3.json(), "稲毛拠点")["manual_status"] is None


@pytest.mark.asyncio
async def test_standing_override_staff_forbidden(client, db) -> None:
    staffu = await _make_user(db, email="am-staff2@example.com", role="staff")
    await db.commit()
    res = await client.put(
        "/api/v1/acceptance-matrix/standing-override",
        headers=_bearer(staffu),
        json={"weekday": 0, "time_slot": "11:00:00", "status": "available"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_standing_override_unknown_office_404(client, db) -> None:
    admin = await _make_user(db, email="am-admin10@example.com", role="admin")
    await db.commit()
    res = await client.put(
        "/api/v1/acceptance-matrix/standing-override",
        headers=_bearer(admin),
        json={
            "office_id": "00000000-0000-0000-0000-0000000000ee",
            "weekday": 0,
            "time_slot": "11:00:00",
            "status": "available",
        },
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_requires_auth(client, db) -> None:
    office, _staff = await _seed_office_staff(db)
    await db.commit()
    res = await client.get(_url(office_id=office.id))
    assert res.status_code == 401
