"""Tests for GET /api/v1/schedule/v2/board (Phase2-3a).

週ボード read API: 対象週 × 拠点の実 Visit を「モバイル現場ボード /m」が描画
しやすい office × weekday × course 構造で返す read-only endpoint.

検証内容:
    - (office, weekday, course) 別に visits が start 昇順・実時刻で返る.
    - 容量集計 (filled / max / total_minutes / remaining).
    - 同住所 group が 2 名以上で付与される (📍同住所連結).
    - 複数拠点を offices[] / board[] に展開.
    - 休講曜日 (staff 0) は courses 空 + closed=True.
    - 空ボード (visit なし).
    - insurance (med/care) / patient_kana の伝播.
    - 認証 (staff 403 / no-auth 401).

ローカル SQLite のみ (本番 DB 禁止).

座標メモ (千葉市近辺, test_propose_slots_api.py と整合):
    BASE   = (35.6000, 140.1000)
    NEAR   = (35.6010, 140.1010)  → BASE から ~0.14km
    FAR    = (35.6300, 140.1400)  → BASE から ~4.5km
    SAME   = (35.60005, 140.10005) → BASE と同住所 (<=100m)
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)
SAME = (35.60005, 140.10005)


# ---------------------------------------------------------------------------
# Helpers
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


async def _seed_office(db, *, name: str = "稲毛", code: str | None = "INAGE") -> Office:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    return office


async def _seed_staff(
    db,
    *,
    office: Office,
    name: str = "担当看護師",
    role: str = "staff",
    on_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> Staff:
    """staff + StaffShift (on_weekdays を is_on=True) を作る.

    count_active_staff_per_weekday が当該 weekday を稼働とみなすため、
    StaffShift を明示的に seed する.
    """
    staff = Staff(
        name=name,
        role=role,
        is_trainee=False,
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=wd in on_weekdays))
    await db.flush()
    return staff


async def _seed_patient(
    db,
    *,
    office: Office,
    code: str,
    lat: float,
    lng: float,
    name: str | None = None,
    kana: str | None = None,
    insurance: str | None = None,
    address: str | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"P-{code}",
        kana=kana,
        insurance=insurance,
        address=address,
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
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


async def _seed_visit(
    db,
    *,
    patient: Patient,
    course: Course,
    start: time,
    end: time,
    weekday_offset: int = 0,
) -> Visit:
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


def _cell(body: dict[str, Any], office_id: str, weekday: int) -> dict[str, Any]:
    for c in body["board"]:
        if c["office_id"] == office_id and c["weekday"] == weekday:
            return c
    raise AssertionError(f"cell not found office={office_id} weekday={weekday}")


# ---------------------------------------------------------------------------
# 実 Visit のボード構造
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_returns_visits_sorted_with_real_times_and_capacity(client, db) -> None:
    admin = await _make_user(db, email="board-admin1@example.com", role="admin")
    office = await _seed_office(db, name="稲毛")
    staff = await _seed_staff(db, office=office)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    # 既存訪問 2 件 (start 昇順を逆順に insert して並び替えを検証).
    p1 = await _seed_patient(
        db,
        office=office,
        code="EX1",
        lat=NEAR[0],
        lng=NEAR[1],
        insurance="medical",
        kana="エックスイチ",
    )
    p2 = await _seed_patient(
        db, office=office, code="EX2", lat=FAR[0], lng=FAR[1], insurance="care"
    )
    await _seed_visit(db, patient=p2, course=course, start=time(13, 0), end=time(13, 30))
    await _seed_visit(db, patient=p1, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["iso_year"] == ISO_YEAR
    assert body["course_max"] == 6
    assert len(body["offices"]) == 1
    assert body["offices"][0]["office_name"] == "稲毛"

    cell = _cell(body, str(office.id), 0)
    assert cell["closed"] is False
    assert cell["staff_count"] == 1
    assert cell["patient_count"] == 2
    assert len(cell["courses"]) == 1
    crs = cell["courses"][0]
    assert crs["course_code"] == "A"
    assert crs["course_label"] == "稲A"  # 拠点短縮 (稲毛→稲) + code
    assert crs["staff_name"] == "担当看護師"

    visits = crs["visits"]
    assert len(visits) == 2
    # start 昇順 + 実時刻.
    assert [v["start_time"] for v in visits] == ["09:30", "13:00"]
    assert visits[0]["end_time"] == "10:00"
    assert visits[0]["service_minutes"] == 30
    assert visits[0]["slot_index"] == 0
    assert visits[1]["slot_index"] == 1
    # 保険正規化 + kana.
    assert visits[0]["insurance"] == "med"
    assert visits[0]["patient_kana"] == "エックスイチ"
    assert visits[1]["insurance"] == "care"
    # 容量集計.
    cap = crs["capacity"]
    assert cap["filled"] == 2
    assert cap["max"] == 6
    assert cap["total_minutes"] == 60
    assert cap["remaining"] == 4


@pytest.mark.asyncio
async def test_board_assigns_same_address_group(client, db) -> None:
    """同住所 (BASE / SAME) 2 名は同一 group_id を持ち、異住所 (FAR) は None."""
    admin = await _make_user(db, email="board-admin2@example.com", role="admin")
    office = await _seed_office(db, name="稲毛")
    staff = await _seed_staff(db, office=office)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    pa = await _seed_patient(
        db, office=office, code="SA1", lat=BASE[0], lng=BASE[1], name="同居者A"
    )
    pb = await _seed_patient(
        db, office=office, code="SA2", lat=SAME[0], lng=SAME[1], name="同居者B"
    )
    pf = await _seed_patient(db, office=office, code="FAR1", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=pa, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=pb, course=course, start=time(9, 30), end=time(10, 0))
    await _seed_visit(db, patient=pf, course=course, start=time(13, 0), end=time(13, 30))
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    cell = _cell(body, str(office.id), 0)
    by_code = {v["patient_id"]: v for v in cell["courses"][0]["visits"]}
    gid_a = by_code[str(pa.id)]["same_address_group_id"]
    gid_b = by_code[str(pb.id)]["same_address_group_id"]
    gid_f = by_code[str(pf.id)]["same_address_group_id"]
    assert gid_a is not None
    assert gid_a == gid_b  # 同住所ペアは同じ group.
    assert gid_f is None  # 異住所は付与されない.


@pytest.mark.asyncio
async def test_board_multiple_offices(client, db) -> None:
    """複数拠点を offices[] と board[] に展開する."""
    admin = await _make_user(db, email="board-admin3@example.com", role="admin")
    o1 = await _seed_office(db, name="稲毛", code="INAGE")
    o2 = await _seed_office(db, name="都賀", code="TSUGA")
    s1 = await _seed_staff(db, office=o1, name="稲担当")
    s2 = await _seed_staff(db, office=o2, name="都担当")
    c1 = await _seed_course(db, office=o1, staff=s1, weekday=0, code="A")
    c2 = await _seed_course(db, office=o2, staff=s2, weekday=0, code="A")
    p1 = await _seed_patient(db, office=o1, code="O1P", lat=NEAR[0], lng=NEAR[1])
    p2 = await _seed_patient(db, office=o2, code="O2P", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=p1, course=c1, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=c2, start=time(9, 0), end=time(9, 30))
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    office_ids = {o["office_id"] for o in body["offices"]}
    assert {str(o1.id), str(o2.id)} <= office_ids
    # 都賀 (TSUGA) のラベルは office_code 基準の正準短縮で「津A」.
    cell2 = _cell(body, str(o2.id), 0)
    assert cell2["courses"][0]["course_label"] == "津A"
    # 稲毛 (INAGE) は「稲A」.
    cell1 = _cell(body, str(o1.id), 0)
    assert cell1["courses"][0]["course_label"] == "稲A"


@pytest.mark.asyncio
async def test_board_closed_weekday_has_no_courses(client, db) -> None:
    """土曜 (スタッフ 0) は closed=True かつ courses 空."""
    admin = await _make_user(db, email="board-admin4@example.com", role="admin")
    office = await _seed_office(db, name="稲毛")
    # staff は月-金のみ稼働 (土=5 は off).
    staff = await _seed_staff(db, office=office, on_weekdays=(0, 1, 2, 3, 4))
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    p = await _seed_patient(db, office=office, code="C1", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 0), end=time(9, 30))
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 月曜 (0): 稼働.
    mon = _cell(body, str(office.id), 0)
    assert mon["closed"] is False
    assert mon["staff_count"] == 1
    # 土曜 (5): 休講.
    sat = _cell(body, str(office.id), 5)
    assert sat["closed"] is True
    assert sat["staff_count"] == 0
    assert sat["courses"] == []


@pytest.mark.asyncio
async def test_board_empty_when_no_visits(client, db) -> None:
    """visit が無くても (拠点 + スタッフのみ) 200 + 空ボード構造を返す."""
    admin = await _make_user(db, email="board-admin5@example.com", role="admin")
    office = await _seed_office(db, name="稲毛")
    await _seed_staff(db, office=office)
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["offices"]) == 1
    assert len(body["weekdays"]) == 7
    # 全 cell の courses 空, patient_count 0.
    for c in body["board"]:
        assert c["courses"] == []
        assert c["patient_count"] == 0
    # 平日は稼働 (closed=False), 土日は休講.
    assert _cell(body, str(office.id), 0)["closed"] is False
    assert _cell(body, str(office.id), 6)["closed"] is True


@pytest.mark.asyncio
async def test_board_weekday_headers(client, db) -> None:
    """weekdays[] は 7 件 + 日付 + 全拠点合計患者数を返す."""
    admin = await _make_user(db, email="board-admin6@example.com", role="admin")
    office = await _seed_office(db, name="稲毛")
    staff = await _seed_staff(db, office=office)
    course = await _seed_course(db, office=office, staff=staff, weekday=2, code="A")
    p = await _seed_patient(db, office=office, code="W1", lat=NEAR[0], lng=NEAR[1])
    # 水曜 (weekday=2) の visit.
    wed = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 3)
    v = Visit(
        patient_id=p.id,
        visit_date=wed,
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=staff.id,
    )
    db.add(v)
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["weekdays"]) == 7
    wd_map = {w["weekday"]: w for w in body["weekdays"]}
    assert wd_map[0]["weekday_code"] == "Mon"
    assert wd_map[0]["date"] == WEEK_MONDAY.isoformat()
    assert wd_map[2]["date"] == wed.isoformat()
    assert wd_map[2]["patient_count"] == 1
    assert wd_map[0]["patient_count"] == 0


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="board-staff@example.com", role="staff")
    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(staff_user),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_board_rejects_no_auth(client, db) -> None:
    res = await client.get(
        "/api/v1/schedule/v2/board",
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 401
