"""新人同行 (trainee accompaniment) Phase 1 BE テスト — 設計 §10.

検証観点:
- 既定 GET/PUT (trainee 以外 409 / 曜日重複 422 / テンプレ不正 422 / RBAC)
- 週 PUT の重複 422 (同時刻・部分交差・コース×個別・別日 OK) / 週不一致 422 / trainee 以外 409
- 既定展開の冪等性 (2 回展開・proposed 除外・manual 不上書き)
- 孤立リンク掃除 (soft-delete 済み course/visit)
- 可視性 3 経路 (list / GET by id / checkin)・他人に漏れない
- 射影の非破壊追加 (VisitRead.accompaniment / モニター accompaniment_staff_name)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import (
    Course,
    CourseTemplate,
    Office,
    Patient,
    Staff,
    TraineeAccompaniment,
    TraineeAccompanimentDefault,
    User,
    Visit,
)
from app.services.trainee_accompaniment import expand_accompaniment_defaults

JST = ZoneInfo("Asia/Tokyo")

ISO_YEAR = 2026
ISO_WEEK = 29


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(db, name: str, *, is_trainee: bool = False, role: str = "staff") -> Staff:
    s = Staff(name=name, role=role, status="active", is_trainee=is_trainee)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_office(db, name: str = "事業所") -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_template(db, office: Office, label: str) -> CourseTemplate:
    t = CourseTemplate(office_id=office.id, label=label)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _make_patient(
    db, name: str, *, lat: float | None = None, lng: float | None = None
) -> Patient:
    p = Patient(code=f"P-{uuid.uuid4().hex[:8]}", name=name, lat=lat, lng=lng)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course(
    db,
    office: Office,
    *,
    weekday: int,
    code: str,
    template_id=None,
    status: str = "course_fixed",
    assigned_staff_id=None,
    iso_year: int = ISO_YEAR,
    iso_week: int = ISO_WEEK,
    deleted: bool = False,
) -> Course:
    c = Course(
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        office_id=office.id,
        template_id=template_id,
        course_status=status,
        assigned_staff_id=assigned_staff_id,
        deleted_at=datetime.now(tz=JST) if deleted else None,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_visit(
    db,
    patient: Patient,
    *,
    visit_date: date,
    start: time,
    end: time,
    course_id=None,
    primary_staff_id=None,
    status: str = "planned",
    deleted: bool = False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=primary_staff_id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="care",
        status=status,
        source="auto",
        course_id=course_id,
        deleted_at=datetime.now(tz=JST) if deleted else None,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _week_date(weekday: int, iso_year: int = ISO_YEAR, iso_week: int = ISO_WEEK) -> date:
    """ISO 週内の指定曜日 (0=Mon) の日付."""
    return date.fromisocalendar(iso_year, iso_week, weekday + 1)


# ---------------------------------------------------------------------------
# 既定 GET/PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_defaults_and_get(client, db) -> None:
    admin = await _make_user(db, "ta-def1@example.com", "admin")
    trainee = await _make_staff(db, "新人A", is_trainee=True)
    office = await _make_office(db, "拠点A")
    t = await _make_template(db, office, "A")

    res = await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "items": [{"weekday": 0, "course_template_id": str(t.id)}],
        },
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 1
    assert res.json()[0]["weekday"] == 0

    get = await client.get(
        f"/api/v1/trainee-accompaniment-defaults?trainee_staff_id={trainee.id}",
        headers=_bearer(admin),
    )
    assert get.status_code == 200, get.text
    assert len(get.json()) == 1
    assert get.json()[0]["course_template_id"] == str(t.id)


@pytest.mark.asyncio
async def test_put_defaults_not_trainee_409(client, db) -> None:
    admin = await _make_user(db, "ta-def2@example.com", "admin")
    non_trainee = await _make_staff(db, "ベテラン", is_trainee=False)
    office = await _make_office(db)
    t = await _make_template(db, office, "A")

    res = await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(non_trainee.id),
            "items": [{"weekday": 0, "course_template_id": str(t.id)}],
        },
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_put_defaults_duplicate_weekday_422(client, db) -> None:
    admin = await _make_user(db, "ta-def3@example.com", "admin")
    trainee = await _make_staff(db, "新人B", is_trainee=True)
    office = await _make_office(db)
    t = await _make_template(db, office, "A")

    res = await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "items": [
                {"weekday": 1, "course_template_id": str(t.id)},
                {"weekday": 1, "course_template_id": str(t.id)},
            ],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_defaults_bad_template_422(client, db) -> None:
    admin = await _make_user(db, "ta-def4@example.com", "admin")
    trainee = await _make_staff(db, "新人C", is_trainee=True)

    res = await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "items": [{"weekday": 0, "course_template_id": str(uuid.uuid4())}],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_defaults_rbac(client, db) -> None:
    """GET は staff 可・PUT は staff 不可 (403)."""
    office = await _make_office(db)
    t = await _make_template(db, office, "A")
    trainee = await _make_staff(db, "新人D", is_trainee=True)
    staff_user = await _make_user(db, "ta-def5-staff@example.com", "staff", staff_id=trainee.id)

    # GET は staff でも 200
    get = await client.get(
        f"/api/v1/trainee-accompaniment-defaults?trainee_staff_id={trainee.id}",
        headers=_bearer(staff_user),
    )
    assert get.status_code == 200, get.text

    # PUT は staff → 403
    put = await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(staff_user),
        json={
            "trainee_staff_id": str(trainee.id),
            "items": [{"weekday": 0, "course_template_id": str(t.id)}],
        },
    )
    assert put.status_code == 403, put.text


# ---------------------------------------------------------------------------
# 週 PUT / GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_week_course_link_and_get(client, db) -> None:
    admin = await _make_user(db, "ta-w1@example.com", "admin")
    trainee = await _make_staff(db, "新人E", is_trainee=True)
    office = await _make_office(db, "拠点E")
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "山田")
    await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
            "visit_ids": [],
        },
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["target_type"] == "course"
    assert items[0]["source"] == "manual"
    assert items[0]["course"]["code"] == "A"
    assert items[0]["trainee_staff_name"] == "新人E"

    get = await client.get(
        f"/api/v1/trainee-accompaniments?iso_year={ISO_YEAR}&iso_week={ISO_WEEK}",
        headers=_bearer(admin),
    )
    assert get.status_code == 200, get.text
    assert len(get.json()["items"]) == 1


@pytest.mark.asyncio
async def test_put_week_not_trainee_409(client, db) -> None:
    admin = await _make_user(db, "ta-w2@example.com", "admin")
    non_trainee = await _make_staff(db, "ベテランF", is_trainee=False)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(non_trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
        },
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_put_week_course_week_mismatch_422(client, db) -> None:
    admin = await _make_user(db, "ta-w3@example.com", "admin")
    trainee = await _make_staff(db, "新人G", is_trainee=True)
    office = await _make_office(db)
    # 別の週のコース
    course = await _make_course(db, office, weekday=0, code="A", iso_week=ISO_WEEK + 1)

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_week_visit_week_mismatch_422(client, db) -> None:
    admin = await _make_user(db, "ta-w4@example.com", "admin")
    trainee = await _make_staff(db, "新人H", is_trainee=True)
    patient = await _make_patient(db, "佐藤")
    # 翌週の visit
    v = await _make_visit(
        db,
        patient,
        visit_date=_week_date(0, iso_week=ISO_WEEK + 1),
        start=time(10, 0),
        end=time(11, 0),
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "visit_ids": [str(v.id)],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_week_overlap_same_time_422(client, db) -> None:
    """2 コースの訪問が同一日同時刻に重なる → 確定ブロック 422 (重複ペア詳細)."""
    admin = await _make_user(db, "ta-ov1@example.com", "admin")
    trainee = await _make_staff(db, "新人I", is_trainee=True)
    office = await _make_office(db)
    c1 = await _make_course(db, office, weekday=0, code="A")
    c2 = await _make_course(db, office, weekday=0, code="C")
    p1 = await _make_patient(db, "山田")
    p2 = await _make_patient(db, "佐藤")
    await _make_visit(
        db, p1, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c1.id
    )
    await _make_visit(
        db, p2, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c2.id
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(c1.id), str(c2.id)],
        },
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert "overlaps" in detail
    assert len(detail["overlaps"]) == 1


@pytest.mark.asyncio
async def test_put_week_overlap_same_address_pair_allowed(client, db) -> None:
    """同住所×同時刻ペア (座標バケット一致) は 90 分占有ルールの正当な同時刻
    → 重複扱いせず 200 (PO報告 2026-07-12)。異住所の重複は従来どおり 422。"""
    admin = await _make_user(db, "ta-ov-sa@example.com", "admin")
    trainee = await _make_staff(db, "新人SA", is_trainee=True)
    office = await _make_office(db)
    c1 = await _make_course(db, office, weekday=0, code="A")
    c2 = await _make_course(db, office, weekday=0, code="C")
    # 同住所ペア (夫婦想定・同一座標)。
    p1 = await _make_patient(db, "本名(夫)", lat=35.6001, lng=140.1001)
    p2 = await _make_patient(db, "本名(妻)", lat=35.6001, lng=140.1001)
    await _make_visit(
        db, p1, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c1.id
    )
    await _make_visit(
        db, p2, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c2.id
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(c1.id), str(c2.id)],
        },
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 2


@pytest.mark.asyncio
async def test_put_week_overlap_different_address_still_422(client, db) -> None:
    """座標があっても住所が異なれば従来どおり 422 (免除は同一バケットのみ)。"""
    admin = await _make_user(db, "ta-ov-da@example.com", "admin")
    trainee = await _make_staff(db, "新人DA", is_trainee=True)
    office = await _make_office(db)
    c1 = await _make_course(db, office, weekday=0, code="A")
    c2 = await _make_course(db, office, weekday=0, code="C")
    p1 = await _make_patient(db, "遠方A", lat=35.6001, lng=140.1001)
    p2 = await _make_patient(db, "遠方B", lat=35.7001, lng=140.2001)
    await _make_visit(
        db, p1, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c1.id
    )
    await _make_visit(
        db, p2, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c2.id
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(c1.id), str(c2.id)],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_week_overlap_partial_422(client, db) -> None:
    """部分交差 (10:00-11:00 × 10:30-11:30) も 422."""
    admin = await _make_user(db, "ta-ov2@example.com", "admin")
    trainee = await _make_staff(db, "新人J", is_trainee=True)
    office = await _make_office(db)
    c1 = await _make_course(db, office, weekday=0, code="A")
    c2 = await _make_course(db, office, weekday=0, code="C")
    p1 = await _make_patient(db, "A様")
    p2 = await _make_patient(db, "B様")
    await _make_visit(
        db, p1, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c1.id
    )
    await _make_visit(
        db, p2, visit_date=_week_date(0), start=time(10, 30), end=time(11, 30), course_id=c2.id
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(c1.id), str(c2.id)],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_week_overlap_course_x_visit_422(client, db) -> None:
    """コースリンク訪問 × 個別リンク訪問の交差も 422."""
    admin = await _make_user(db, "ta-ov3@example.com", "admin")
    trainee = await _make_staff(db, "新人K", is_trainee=True)
    office = await _make_office(db)
    c1 = await _make_course(db, office, weekday=0, code="A")
    p1 = await _make_patient(db, "C様")
    p2 = await _make_patient(db, "D様")
    await _make_visit(
        db, p1, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c1.id
    )
    v2 = await _make_visit(db, p2, visit_date=_week_date(0), start=time(10, 30), end=time(11, 30))

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(c1.id)],
            "visit_ids": [str(v2.id)],
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_week_different_days_ok(client, db) -> None:
    """別日の別コースは許可 (時間帯が交差しない) → 200."""
    admin = await _make_user(db, "ta-ov4@example.com", "admin")
    trainee = await _make_staff(db, "新人L", is_trainee=True)
    office = await _make_office(db)
    c1 = await _make_course(db, office, weekday=0, code="A")
    c2 = await _make_course(db, office, weekday=1, code="A")
    p1 = await _make_patient(db, "E様")
    p2 = await _make_patient(db, "F様")
    await _make_visit(
        db, p1, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=c1.id
    )
    await _make_visit(
        db, p2, visit_date=_week_date(1), start=time(10, 0), end=time(11, 0), course_id=c2.id
    )

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(c1.id), str(c2.id)],
        },
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 2


@pytest.mark.asyncio
async def test_put_week_replace_scope(client, db) -> None:
    """PUT はその新人×その週のみ置換する (空 course_ids で当該週リンクが消える)."""
    admin = await _make_user(db, "ta-w5@example.com", "admin")
    trainee = await _make_staff(db, "新人M", is_trainee=True)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    p = await _make_patient(db, "G様")
    await _make_visit(
        db, p, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), course_id=course.id
    )

    # まず 1 件張る
    r1 = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
        },
    )
    assert r1.status_code == 200, r1.text
    assert len(r1.json()["items"]) == 1

    # 空で置換 → 0 件
    r2 = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [],
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["items"] == []


@pytest.mark.asyncio
async def test_put_week_defaults_untouched_when_omitted(client, db) -> None:
    """defaults キー省略時は既定に触れない (§6.2 曖昧性排除)."""
    admin = await _make_user(db, "ta-w6@example.com", "admin")
    trainee = await _make_staff(db, "新人N", is_trainee=True)
    office = await _make_office(db)
    t = await _make_template(db, office, "A")

    # 既定を先に PUT
    await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "items": [{"weekday": 0, "course_template_id": str(t.id)}],
        },
    )
    # 週 PUT (defaults 省略) → 既定は残る
    await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [],
        },
    )
    get = await client.get(
        f"/api/v1/trainee-accompaniment-defaults?trainee_staff_id={trainee.id}",
        headers=_bearer(admin),
    )
    assert len(get.json()) == 1, get.text


@pytest.mark.asyncio
async def test_put_week_staff_forbidden(client, db) -> None:
    """PUT は staff → 403."""
    trainee = await _make_staff(db, "新人O", is_trainee=True)
    staff_user = await _make_user(db, "ta-w7-staff@example.com", "staff", staff_id=trainee.id)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(staff_user),
        json={
            "trainee_staff_id": str(trainee.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 既定展開 (サービス直接)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_idempotent(db) -> None:
    trainee = await _make_staff(db, "新人P", is_trainee=True)
    office = await _make_office(db)
    t = await _make_template(db, office, "A")
    course = await _make_course(db, office, weekday=0, code="A", template_id=t.id)
    db.add(
        TraineeAccompanimentDefault(trainee_staff_id=trainee.id, weekday=0, course_template_id=t.id)
    )
    await db.commit()

    n1 = await expand_accompaniment_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    n2 = await expand_accompaniment_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert n1 == 1
    assert n2 == 0  # 冪等

    rows = (await db.scalars(select_ta(trainee.id, course.id))).all()
    assert len(rows) == 1
    assert rows[0].source == "default"


@pytest.mark.asyncio
async def test_expand_skips_proposed(db) -> None:
    trainee = await _make_staff(db, "新人Q", is_trainee=True)
    office = await _make_office(db)
    t = await _make_template(db, office, "A")
    # proposed コースは展開対象外
    await _make_course(db, office, weekday=0, code="A", template_id=t.id, status="proposed")
    db.add(
        TraineeAccompanimentDefault(trainee_staff_id=trainee.id, weekday=0, course_template_id=t.id)
    )
    await db.commit()

    n = await expand_accompaniment_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert n == 0


@pytest.mark.asyncio
async def test_expand_manual_not_overwritten(db) -> None:
    """手動リンク (manual) が既にあれば展開は上書き/重複しない."""
    trainee = await _make_staff(db, "新人R", is_trainee=True)
    office = await _make_office(db)
    t = await _make_template(db, office, "A")
    course = await _make_course(db, office, weekday=0, code="A", template_id=t.id)
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id,
            target_type="course",
            course_id=course.id,
            source="manual",
        )
    )
    db.add(
        TraineeAccompanimentDefault(trainee_staff_id=trainee.id, weekday=0, course_template_id=t.id)
    )
    await db.commit()

    n = await expand_accompaniment_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert n == 0
    rows = (await db.scalars(select_ta(trainee.id, course.id))).all()
    assert len(rows) == 1
    assert rows[0].source == "manual"  # 上書きされない


@pytest.mark.asyncio
async def test_orphan_cleanup(db) -> None:
    """soft-delete 済み course/visit を参照する同行リンクは展開時に物理削除される."""
    trainee = await _make_staff(db, "新人S", is_trainee=True)
    office = await _make_office(db)
    dead_course = await _make_course(db, office, weekday=0, code="A", deleted=True)
    p = await _make_patient(db, "H様")
    dead_visit = await _make_visit(
        db, p, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0), deleted=True
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id,
            target_type="course",
            course_id=dead_course.id,
            source="manual",
        )
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id,
            target_type="visit",
            visit_id=dead_visit.id,
            source="manual",
        )
    )
    await db.commit()

    await expand_accompaniment_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()

    from sqlalchemy import func, select

    remaining = await db.scalar(select(func.count()).select_from(TraineeAccompaniment))
    assert remaining == 0


# ---------------------------------------------------------------------------
# 可視性 3 経路
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visibility_list_via_course_link(client, db) -> None:
    """新人本人の GET /visits にコースリンク経由の同行訪問が出る・他人には漏れない."""
    trainee = await _make_staff(db, "新人T", is_trainee=True)
    mentor = await _make_staff(db, "先輩T")
    other = await _make_staff(db, "無関係T")
    trainee_user = await _make_user(db, "ta-vis1@example.com", "staff", staff_id=trainee.id)
    other_user = await _make_user(db, "ta-vis1-other@example.com", "staff", staff_id=other.id)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A", assigned_staff_id=mentor.id)
    p = await _make_patient(db, "I様")
    v = await _make_visit(
        db,
        p,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
        primary_staff_id=mentor.id,
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id, target_type="course", course_id=course.id, source="manual"
        )
    )
    await db.commit()

    wk = _week_date(0)
    # 新人本人は同行訪問が見える
    res = await client.get(
        f"/api/v1/visits?week_start={wk}&week_end={wk}", headers=_bearer(trainee_user)
    )
    assert res.status_code == 200, res.text
    ids = {r["id"] for r in res.json()}
    assert str(v.id) in ids
    # accompaniment が非破壊で載る
    row = next(r for r in res.json() if r["id"] == str(v.id))
    assert row["accompaniment"] is not None
    assert row["accompaniment"]["staff_id"] == str(trainee.id)

    # 無関係スタッフには漏れない
    res2 = await client.get(
        f"/api/v1/visits?week_start={wk}&week_end={wk}", headers=_bearer(other_user)
    )
    assert res2.status_code == 200, res2.text
    assert str(v.id) not in {r["id"] for r in res2.json()}


@pytest.mark.asyncio
async def test_visibility_get_by_id_via_visit_link(client, db) -> None:
    """直リンク経由で GET /visits/{id} が 200・無関係スタッフは 404."""
    trainee = await _make_staff(db, "新人U", is_trainee=True)
    mentor = await _make_staff(db, "先輩U")
    other = await _make_staff(db, "無関係U")
    trainee_user = await _make_user(db, "ta-vis2@example.com", "staff", staff_id=trainee.id)
    other_user = await _make_user(db, "ta-vis2-other@example.com", "staff", staff_id=other.id)
    p = await _make_patient(db, "J様")
    v = await _make_visit(
        db,
        p,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        primary_staff_id=mentor.id,
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id, target_type="visit", visit_id=v.id, source="manual"
        )
    )
    await db.commit()

    ok = await client.get(f"/api/v1/visits/{v.id}", headers=_bearer(trainee_user))
    assert ok.status_code == 200, ok.text
    assert ok.json()["accompaniment"]["staff_id"] == str(trainee.id)

    nf = await client.get(f"/api/v1/visits/{v.id}", headers=_bearer(other_user))
    assert nf.status_code == 404, nf.text


@pytest.mark.asyncio
async def test_visibility_checkin_via_visit_link(client, db) -> None:
    """新人本人が同行訪問 (今日) にチェックインできる (可視性 404 を通過)."""
    trainee = await _make_staff(db, "新人V", is_trainee=True)
    mentor = await _make_staff(db, "先輩V")
    trainee_user = await _make_user(db, "ta-vis3@example.com", "staff", staff_id=trainee.id)
    p = await _make_patient(db, "K様")
    today = datetime.now(tz=JST).date()
    v = await _make_visit(
        db, p, visit_date=today, start=time(10, 0), end=time(11, 0), primary_staff_id=mentor.id
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id, target_type="visit", visit_id=v.id, source="manual"
        )
    )
    await db.commit()

    res = await client.post(
        f"/api/v1/visits/{v.id}/checkin", headers=_bearer(trainee_user), json={}
    )
    # 可視性を通過 (404 でない)。今日の visit のため judge も通り 200。
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# モニター射影
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_accompaniment_field(db) -> None:
    """build_monitor が MonitorVisit.accompaniment_staff_name をコースリンクから解決する."""
    from app.services.checkin.monitor import build_monitor

    trainee = await _make_staff(db, "新人W", is_trainee=True)
    mentor = await _make_staff(db, "先輩W")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A", assigned_staff_id=mentor.id)
    p = await _make_patient(db, "L様")
    target = _week_date(0)
    v = await _make_visit(
        db,
        p,
        visit_date=target,
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
        primary_staff_id=mentor.id,
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id, target_type="course", course_id=course.id, source="manual"
        )
    )
    await db.commit()

    resp = await build_monitor(db, target)
    found = None
    for row in resp.staff:
        for mv in row.visits:
            if mv.visit_id == v.id:
                found = mv
    assert found is not None
    assert found.accompaniment_staff_name == "新人W"


# ---------------------------------------------------------------------------
# §7.5 / §8-4: defaults label 解決 / future 削除 / コース担当ガード
# ---------------------------------------------------------------------------


def _cur_week() -> tuple[int, int]:
    iso = date.today().isocalendar()
    return iso[0], iso[1]


@pytest.mark.asyncio
async def test_defaults_get_includes_template_label(client, db) -> None:
    """§7.5: 既定 GET は course_template_label / office_id を解決して返す (サマリ用)."""
    admin = await _make_user(db, "ta-lbl@example.com", "admin")
    trainee = await _make_staff(db, "新人ラベル", is_trainee=True)
    office = await _make_office(db, "拠点ラベル")
    t = await _make_template(db, office, "K")

    put = await client.put(
        "/api/v1/trainee-accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(trainee.id),
            "items": [{"weekday": 1, "course_template_id": str(t.id)}],
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()[0]["course_template_label"] == "K"
    assert put.json()[0]["office_id"] == str(office.id)

    get = await client.get(
        f"/api/v1/trainee-accompaniment-defaults?trainee_staff_id={trainee.id}",
        headers=_bearer(admin),
    )
    assert get.status_code == 200, get.text
    assert get.json()[0]["course_template_label"] == "K"


@pytest.mark.asyncio
async def test_course_guard_reports_current_and_future_courses(client, db) -> None:
    """§8-4: 新人が今週以降のコース担当に残っていれば count>0 を返す。過去週は含めない。"""
    admin = await _make_user(db, "ta-guard@example.com", "admin")
    trainee = await _make_staff(db, "新人ガード", is_trainee=True)
    office = await _make_office(db, "拠点ガード")
    cy, cw = _cur_week()

    # 今週のコースを新人担当に (レガシー状態を模す)
    await _make_course(
        db,
        office,
        weekday=0,
        code="A",
        iso_year=cy,
        iso_week=cw,
        assigned_staff_id=trainee.id,
    )
    # 過去週 (前年) のコース担当 — ガード対象外
    await _make_course(
        db,
        office,
        weekday=1,
        code="B",
        iso_year=cy - 1,
        iso_week=1,
        assigned_staff_id=trainee.id,
    )

    res = await client.get(
        f"/api/v1/trainee-accompaniments/course-guard?trainee_staff_id={trainee.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 1
    assert body["courses"][0]["code"] == "A"


@pytest.mark.asyncio
async def test_delete_future_removes_future_links_and_defaults_keeps_past(client, db) -> None:
    """§7.5: future 削除は今週以降のリンク + 既定を消し、過去週リンクは残す (冪等)."""
    admin = await _make_user(db, "ta-future@example.com", "admin")
    trainee = await _make_staff(db, "新人フューチャ", is_trainee=True)
    office = await _make_office(db, "拠点フューチャ")
    t = await _make_template(db, office, "F")
    cy, cw = _cur_week()

    future_course = await _make_course(
        db,
        office,
        weekday=0,
        code="A",
        template_id=t.id,
        iso_year=cy,
        iso_week=cw,
    )
    past_course = await _make_course(
        db,
        office,
        weekday=1,
        code="B",
        iso_year=cy - 1,
        iso_week=1,
    )

    # 週リンク 2 件 (今週 + 過去週) を直接 INSERT。
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id,
            target_type="course",
            course_id=future_course.id,
            source="manual",
        )
    )
    db.add(
        TraineeAccompaniment(
            trainee_staff_id=trainee.id,
            target_type="course",
            course_id=past_course.id,
            source="manual",
        )
    )
    # 既定も 1 件。
    db.add(
        TraineeAccompanimentDefault(
            trainee_staff_id=trainee.id,
            weekday=0,
            course_template_id=t.id,
        )
    )
    await db.commit()

    res = await client.delete(
        f"/api/v1/trainee-accompaniments/future?trainee_staff_id={trainee.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted_links"] == 1
    assert body["deleted_defaults"] == 1

    # 過去週リンクは残る。
    from sqlalchemy import func, select

    remaining_links = await db.scalar(
        select(func.count())
        .select_from(TraineeAccompaniment)
        .where(TraineeAccompaniment.trainee_staff_id == trainee.id)
    )
    assert remaining_links == 1
    remaining_defaults = await db.scalar(
        select(func.count())
        .select_from(TraineeAccompanimentDefault)
        .where(TraineeAccompanimentDefault.trainee_staff_id == trainee.id)
    )
    assert remaining_defaults == 0

    # 冪等: 2 回目も成功 (0 件)。
    res2 = await client.delete(
        f"/api/v1/trainee-accompaniments/future?trainee_staff_id={trainee.id}",
        headers=_bearer(admin),
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["deleted_defaults"] == 0


@pytest.mark.asyncio
async def test_delete_future_rbac_staff_forbidden(client, db) -> None:
    """future 削除は admin/manager のみ (staff は 403)."""
    trainee = await _make_staff(db, "新人RBAC", is_trainee=True)
    staff_user = await _make_user(db, "ta-future-staff@example.com", "staff", staff_id=trainee.id)
    res = await client.delete(
        f"/api/v1/trainee-accompaniments/future?trainee_staff_id={trainee.id}",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# helper: ORM select for TA rows
# ---------------------------------------------------------------------------


def select_ta(trainee_id, course_id):
    from sqlalchemy import select

    return select(TraineeAccompaniment).where(
        TraineeAccompaniment.trainee_staff_id == trainee_id,
        TraineeAccompaniment.course_id == course_id,
    )
