"""Tests for P5 BE-1 引き継ぎプランエンジン (層 1-4).

設計書 docs/plans/p5-course-substitute-design.md §1-2 / §4 (#1-19).

GET /api/v1/schedule/staff-substitute/candidates のレスポンスに追加された
``plans[]`` (SubstitutePlan) を検証する. 訪問単位 candidates[] の出力は不変
(= test_staff_substitute.py が全 pass) が前提.

対象:
    - 層 1: 丸ごと引き継ぎ (一般スタッフ. 例外 ≦ 2 で成立)
    - 層 2: AM/PM 分担 (一般 2 名. コースが AM/PM 両方に跨るときのみ)
    - 層 3: マネージャー丸ごと (層 1-2 が 0 件のときのみ)
    - 層 4: 分散 (層 1-3 全滅時. 貪欲・移動最小・unassignable 明示)
    - NULL コースは層 4 のみ / 複数コース独立 / 上限 (層毎 2) / 2 名体制 partner 除外
    - プラン → substitutions[] 展開 apply 統合 / 後方互換

ローカル SQLite のみ. 座標は千葉市近辺 (test_staff_substitute と整合).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.staff import Staff, StaffEvent, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

ISO_YEAR = 2026
ISO_WEEK = 20
TARGET_DATE = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon), weekday 0
WEEKDAY = 0

BASE = (35.6000, 140.1000)
FAR = (35.6300, 140.1400)
FAR2 = (35.5700, 140.0600)
FAR3 = (35.6400, 140.0500)

CANDIDATES_URL = "/api/v1/schedule/staff-substitute/candidates"
APPLY_URL = "/api/v1/schedule/staff-substitute/apply"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _office(db, *, name: str, code: str) -> Office:
    o = Office(name=name, code=code)
    db.add(o)
    await db.flush()
    return o


async def _staff(
    db,
    *,
    name: str,
    office: Office,
    sex: str | None = "female",
    is_trainee: bool = False,
    role: str = "staff",
) -> Staff:
    s = Staff(
        name=name,
        sex=sex,
        role=role,
        is_trainee=is_trainee,
        status="active",
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    return s


async def _shift(db, *, staff: Staff, weekday: int = WEEKDAY, is_on: bool = True) -> None:
    db.add(StaffShift(staff_id=staff.id, weekday=weekday, is_on=is_on))
    await db.flush()


async def _patient(
    db, *, office: Office, code: str, lat: float, lng: float, sex_restriction: str | None = None
) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        sex_restriction=sex_restriction,
    )
    db.add(p)
    await db.flush()
    return p


async def _course(
    db, *, office: Office, staff: Staff | None, weekday: int = WEEKDAY, code: str = "A"
) -> Course:
    c = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(c)
    await db.flush()
    return c


async def _visit(
    db,
    *,
    patient: Patient,
    course: Course | None,
    start: time,
    end: time,
    visit_date: date = TARGET_DATE,
    status: str = VISIT_STATUS_PLANNED,
    required_staff_count: int = 1,
    visit_group_id: uuid.UUID | None = None,
    primary: Staff | None = None,
    secondary: Staff | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        source="auto",
        required_staff_count=required_staff_count,
        course_id=course.id if course is not None else None,
        visit_group_id=visit_group_id,
        primary_staff_id=primary.id if primary is not None else None,
        secondary_staff_id=secondary.id if secondary is not None else None,
    )
    db.add(v)
    await db.flush()
    return v


async def _vsa(db, *, visit: Visit, staff: Staff) -> None:
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=staff.id))
    await db.flush()


async def _event(db, *, staff: Staff, start: time, end: time) -> None:
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="meeting",
            starts_at=datetime.combine(TARGET_DATE, start),
            ends_at=datetime.combine(TARGET_DATE, end),
        )
    )
    await db.flush()


async def _get_plans(client, admin, absent) -> list[dict]:
    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(absent.id), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    return res.json()["plans"]


def _tier(plans: list[dict], tier: int) -> list[dict]:
    return [p for p in plans if p["tier"] == tier]


async def _vsa_ids(db, visit_id) -> set:
    rows = await db.scalars(
        select(VisitStaffAssignment.staff_id).where(
            VisitStaffAssignment.visit_id == visit_id
        )
    )
    return set(rows.all())


# ---------------------------------------------------------------------------
# #1 層 1: 一般スタッフ丸ごと引き継ぎ (例外 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_whole_handover_single_general(client, db) -> None:
    admin = await _make_user(db, email="p5-1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    p1 = await _patient(db, office=o1, code="T1", lat=BASE[0], lng=BASE[1])
    p2 = await _patient(db, office=o1, code="T2", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    v1 = await _visit(db, patient=p1, course=course, start=time(10, 0), end=time(10, 30),
                      primary=absent)
    v2 = await _visit(db, patient=p2, course=course, start=time(11, 0), end=time(11, 30),
                      primary=absent)
    await _vsa(db, visit=v1, staff=absent)
    await _vsa(db, visit=v2, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier1 = _tier(plans, 1)
    assert len(tier1) == 1
    plan = tier1[0]
    assert plan["tier_label"] == "丸ごと引き継ぎ"
    assert plan["course_id"] == str(course.id)
    assert plan["course_code"] == "A"
    assert plan["exception_count"] == 0
    assert plan["total_visits"] == 2
    assert len(plan["assignees"]) == 1
    a = plan["assignees"][0]
    assert a["staff_id"] == str(c.id)
    assert a["block"] == "full"
    assert set(a["visit_ids"]) == {str(v1.id), str(v2.id)}
    assert a["visit_count"] == 2
    # 例外 0 / 移動 0 / 継続 0 / 負荷 0 → TIER_SCORE(1)=10000.
    assert plan["score"] == 10000.0


# ---------------------------------------------------------------------------
# #2 層 1: 例外 = EXCEPTION_THRESHOLD (2) は「例外付き」で成立
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_with_exceptions_within_threshold(client, db) -> None:
    admin = await _make_user(db, email="p5-2@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    # 1 open + 2 female_only. 唯一の候補 C は男性 → female 2 件が例外.
    p_open = await _patient(db, office=o1, code="OPEN", lat=BASE[0], lng=BASE[1])
    p_f1 = await _patient(db, office=o1, code="F1", lat=BASE[0], lng=BASE[1],
                          sex_restriction="female_only")
    p_f2 = await _patient(db, office=o1, code="F2", lat=BASE[0], lng=BASE[1],
                          sex_restriction="female_only")
    course = await _course(db, office=o1, staff=absent)
    v_open = await _visit(db, patient=p_open, course=course, start=time(9, 0), end=time(9, 30),
                          primary=absent)
    vf1 = await _visit(db, patient=p_f1, course=course, start=time(10, 0), end=time(10, 30),
                       primary=absent)
    vf2 = await _visit(db, patient=p_f2, course=course, start=time(11, 0), end=time(11, 30),
                       primary=absent)
    for v in (v_open, vf1, vf2):
        await _vsa(db, visit=v, staff=absent)
    c = await _staff(db, name="C", office=o1, sex="male")
    await _shift(db, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier1 = _tier(plans, 1)
    assert len(tier1) == 1
    plan = tier1[0]
    assert plan["exception_count"] == 2
    assert plan["assignees"][0]["visit_ids"] == [str(v_open.id)]
    exc_visit_ids = {e["visit_id"] for e in plan["exceptions"]}
    assert exc_visit_ids == {str(vf1.id), str(vf2.id)}
    assert all(e["reason"] == "sex_mismatch" for e in plan["exceptions"])
    # score = 10000 + 2*(-500) = 9000.
    assert plan["score"] == 9000.0


# ---------------------------------------------------------------------------
# #3 層 1: 例外 > 閾値 → tier1 は生成されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_rejected_when_exceptions_exceed_threshold(client, db) -> None:
    admin = await _make_user(db, email="p5-3@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    p_open = await _patient(db, office=o1, code="OPEN", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    v_open = await _visit(db, patient=p_open, course=course, start=time(9, 0), end=time(9, 30),
                          primary=absent)
    await _vsa(db, visit=v_open, staff=absent)
    # 3 female_only → 男性候補は 3 件例外 (>2).
    for i in range(3):
        pf = await _patient(db, office=o1, code=f"F{i}", lat=BASE[0], lng=BASE[1],
                            sex_restriction="female_only")
        vf = await _visit(db, patient=pf, course=course, start=time(10 + i, 0),
                          end=time(10 + i, 30), primary=absent)
        await _vsa(db, visit=vf, staff=absent)
    c = await _staff(db, name="C", office=o1, sex="male")
    await _shift(db, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert _tier(plans, 1) == []


# ---------------------------------------------------------------------------
# #4 AM/PM 分類: 12:00 台開始は AM / 13:00 は PM
# ---------------------------------------------------------------------------


async def _seed_ampm_course(db, o1):
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    p_am = await _patient(db, office=o1, code="AM", lat=BASE[0], lng=BASE[1])
    p_pm = await _patient(db, office=o1, code="PM", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    v_am = await _visit(db, patient=p_am, course=course, start=time(12, 0), end=time(12, 30),
                        primary=absent)
    v_pm = await _visit(db, patient=p_pm, course=course, start=time(13, 0), end=time(13, 30),
                        primary=absent)
    await _vsa(db, visit=v_am, staff=absent)
    await _vsa(db, visit=v_pm, staff=absent)
    return absent, course, v_am, v_pm


@pytest.mark.asyncio
async def test_ampm_classification_noon_is_am(client, db) -> None:
    admin = await _make_user(db, email="p5-4@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent, course, v_am, v_pm = await _seed_ampm_course(db, o1)
    c = await _staff(db, name="C", office=o1)
    d = await _staff(db, name="D", office=o1)
    await _shift(db, staff=c)
    await _shift(db, staff=d)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier2 = _tier(plans, 2)
    assert tier2, "AM/PM に跨るので層 2 が生成される"
    plan = tier2[0]
    assert len(plan["assignees"]) == 2
    am_assignee = next(a for a in plan["assignees"] if a["block"] == "am")
    pm_assignee = next(a for a in plan["assignees"] if a["block"] == "pm")
    assert am_assignee["visit_ids"] == [str(v_am.id)]  # 12:00 → AM
    assert pm_assignee["visit_ids"] == [str(v_pm.id)]  # 13:00 → PM


# ---------------------------------------------------------------------------
# #5 層 2: AM/PM 両方に跨るとき 2 名プランを生成 (別人ペア)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_generated_when_course_spans_ampm(client, db) -> None:
    admin = await _make_user(db, email="p5-5@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent, course, v_am, v_pm = await _seed_ampm_course(db, o1)
    c = await _staff(db, name="C", office=o1)
    d = await _staff(db, name="D", office=o1)
    await _shift(db, staff=c)
    await _shift(db, staff=d)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier2 = _tier(plans, 2)
    # (C am, D pm) / (D am, C pm) の 2 通り (層毎上限 2).
    assert len(tier2) == 2
    for plan in tier2:
        staff_ids = {a["staff_id"] for a in plan["assignees"]}
        assert len(staff_ids) == 2  # AM/PM は別人
        blocks = {a["block"] for a in plan["assignees"]}
        assert blocks == {"am", "pm"}


# ---------------------------------------------------------------------------
# #6 層 2: コースが AM のみ (跨らない) → 生成されない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier2_not_generated_when_only_am(client, db) -> None:
    admin = await _make_user(db, email="p5-6@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    p1 = await _patient(db, office=o1, code="T1", lat=BASE[0], lng=BASE[1])
    p2 = await _patient(db, office=o1, code="T2", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    v1 = await _visit(db, patient=p1, course=course, start=time(10, 0), end=time(10, 30),
                      primary=absent)
    v2 = await _visit(db, patient=p2, course=course, start=time(11, 0), end=time(11, 30),
                      primary=absent)
    await _vsa(db, visit=v1, staff=absent)
    await _vsa(db, visit=v2, staff=absent)
    c = await _staff(db, name="C", office=o1)
    d = await _staff(db, name="D", office=o1)
    await _shift(db, staff=c)
    await _shift(db, staff=d)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert _tier(plans, 2) == []


# ---------------------------------------------------------------------------
# #7 層 3: マネージャー丸ごとは層 1-2 が 0 件のときのみ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier3_manager_only_when_lower_tiers_empty(client, db) -> None:
    admin = await _make_user(db, email="p5-7@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    # 一般スタッフは居ない. マネージャー M のみ.
    mgr = await _staff(db, name="M", office=o1, role="manager")
    await _shift(db, staff=mgr)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert _tier(plans, 1) == []
    assert _tier(plans, 2) == []
    tier3 = _tier(plans, 3)
    assert len(tier3) == 1
    assert tier3[0]["tier_label"] == "マネージャー丸ごと"
    assert tier3[0]["assignees"][0]["staff_id"] == str(mgr.id)
    # TIER_SCORE(3) = 2000, 例外/移動/負荷 0.
    assert tier3[0]["score"] == 2000.0


@pytest.mark.asyncio
async def test_tier3_not_generated_when_tier1_exists(client, db) -> None:
    admin = await _make_user(db, email="p5-8@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    mgr = await _staff(db, name="M", office=o1, role="manager")
    await _shift(db, staff=mgr)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert len(_tier(plans, 1)) == 1  # 一般 C が丸ごと
    assert _tier(plans, 3) == []  # 一般優先 → Mgr 層は生成しない


# ---------------------------------------------------------------------------
# #8 層 4: 分散は層 1-3 全滅時. 貪欲・受け手ごとに束ね時系列順
# ---------------------------------------------------------------------------


async def _seed_four_conflicting(db, o1, absent, *, coords):
    """同時刻 (10:00-11:00) の 4 visit を 4 拠点に作る (単独では 1 件しか担えない)."""
    course = await _course(db, office=o1, staff=absent)
    visits = []
    for i, (lat, lng) in enumerate(coords):
        p = await _patient(db, office=o1, code=f"C{i}", lat=lat, lng=lng)
        v = await _visit(db, patient=p, course=course, start=time(10, 0), end=time(11, 0),
                         primary=absent)
        await _vsa(db, visit=v, staff=absent)
        visits.append(v)
    return course, visits


@pytest.mark.asyncio
async def test_tier4_distribution_greedy_bundles(client, db) -> None:
    admin = await _make_user(db, email="p5-9@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    course, visits = await _seed_four_conflicting(
        db, o1, absent, coords=[BASE, FAR, FAR2, FAR3]
    )
    # 4 一般スタッフ → 各 1 件に分散可能.
    for name in ("C", "D", "E", "F"):
        s = await _staff(db, name=name, office=o1)
        await _shift(db, staff=s)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert _tier(plans, 1) == []
    assert _tier(plans, 2) == []
    assert _tier(plans, 3) == []
    tier4 = _tier(plans, 4)
    assert len(tier4) == 1
    plan = tier4[0]
    assert plan["tier_label"] == "分散"
    assert plan["exception_count"] == 0
    assert plan["total_visits"] == 4
    # 4 件が 4 受け手に分散 (同時刻なので 1 人 1 件).
    assert len(plan["assignees"]) == 4
    all_covered = [vid for a in plan["assignees"] for vid in a["visit_ids"]]
    assert sorted(all_covered) == sorted(str(v.id) for v in visits)
    assert all(a["visit_count"] == 1 for a in plan["assignees"])


# ---------------------------------------------------------------------------
# #9 層 4: 受け手不足 → unassignable を例外 + warning で明示
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier4_unassignable_explicit(client, db) -> None:
    admin = await _make_user(db, email="p5-10@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    course, visits = await _seed_four_conflicting(
        db, o1, absent, coords=[BASE, FAR, FAR2, FAR3]
    )
    # 3 スタッフしか居ない → 4 件中 1 件は割当不能.
    for name in ("C", "D", "E"):
        s = await _staff(db, name=name, office=o1)
        await _shift(db, staff=s)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier4 = _tier(plans, 4)
    assert len(tier4) == 1
    plan = tier4[0]
    assert len(plan["assignees"]) == 3
    assert plan["exception_count"] == 1
    assert plan["exceptions"][0]["reason"] == "time_conflict"
    assert any("割当不能" in w for w in plan["warnings"])
    assert plan["total_visits"] == 4


# ---------------------------------------------------------------------------
# #10 NULL コースの visit は層 4 のみ対象
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_course_only_tier4(client, db) -> None:
    admin = await _make_user(db, email="p5-11@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    visit = await _visit(db, patient=patient, course=None, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    # NULL コースは層 1-3 を生成せず層 4 のみ.
    assert {p["tier"] for p in plans} == {4}
    plan = plans[0]
    assert plan["course_id"] is None
    assert plan["course_code"] is None
    assert plan["assignees"][0]["staff_id"] == str(c.id)


# ---------------------------------------------------------------------------
# #11 上限: 層毎 2 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier_cap_two_per_tier(client, db) -> None:
    admin = await _make_user(db, email="p5-12@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    # 3 一般候補 → tier1 は上限 2 に制限される.
    for name in ("C", "D", "E"):
        s = await _staff(db, name=name, office=o1)
        await _shift(db, staff=s)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert len(_tier(plans, 1)) == 2


# ---------------------------------------------------------------------------
# #12 2 名体制: partner は受け手から除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_person_course_excludes_partner(client, db) -> None:
    admin = await _make_user(db, email="p5-13@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    a = await _staff(db, name="A", office=o1)
    b = await _staff(db, name="B", office=o1)
    await _shift(db, staff=a)
    await _shift(db, staff=b)
    patient = await _patient(db, office=o1, code="TP", lat=BASE[0], lng=BASE[1])
    group_id = uuid.uuid4()
    course = await _course(db, office=o1, staff=a)
    v = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                     required_staff_count=2, visit_group_id=group_id, primary=a, secondary=b)
    await _vsa(db, visit=v, staff=a)
    await _vsa(db, visit=v, staff=b)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    # A (primary) 欠勤 → partner B は候補から除外, C が引き継ぐ.
    plans = await _get_plans(client, admin, a)
    tier1 = _tier(plans, 1)
    assert len(tier1) == 1
    assignee_ids = {x["staff_id"] for x in tier1[0]["assignees"]}
    assert assignee_ids == {str(c.id)}
    assert str(b.id) not in assignee_ids


# ---------------------------------------------------------------------------
# #13 複数コースは独立にプラン生成
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_courses_independent(client, db) -> None:
    admin = await _make_user(db, email="p5-14@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    course_a = await _course(db, office=o1, staff=absent, code="A")
    course_b = await _course(db, office=o1, staff=absent, code="B")
    pa = await _patient(db, office=o1, code="A1", lat=BASE[0], lng=BASE[1])
    pb = await _patient(db, office=o1, code="B1", lat=BASE[0], lng=BASE[1])
    va = await _visit(db, patient=pa, course=course_a, start=time(10, 0), end=time(10, 30),
                      primary=absent)
    vb = await _visit(db, patient=pb, course=course_b, start=time(14, 0), end=time(14, 30),
                      primary=absent)
    await _vsa(db, visit=va, staff=absent)
    await _vsa(db, visit=vb, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier1 = _tier(plans, 1)
    course_ids = {p["course_id"] for p in tier1}
    assert course_ids == {str(course_a.id), str(course_b.id)}
    codes = {p["course_code"] for p in tier1}
    assert codes == {"A", "B"}


# ---------------------------------------------------------------------------
# #14 プラン → substitutions[] 展開 apply 統合 (既存フロー再利用)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_expansion_apply_integration(client, db) -> None:
    admin = await _make_user(db, email="p5-15@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    p1 = await _patient(db, office=o1, code="T1", lat=BASE[0], lng=BASE[1])
    p2 = await _patient(db, office=o1, code="T2", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    v1 = await _visit(db, patient=p1, course=course, start=time(10, 0), end=time(10, 30),
                      primary=absent)
    v2 = await _visit(db, patient=p2, course=course, start=time(11, 0), end=time(11, 30),
                      primary=absent)
    await _vsa(db, visit=v1, staff=absent)
    await _vsa(db, visit=v2, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    plan = _tier(plans, 1)[0]
    # プラン assignees を visit_id × staff_id に展開.
    substitutions = [
        {"visit_id": vid, "substitute_staff_id": a["staff_id"]}
        for a in plan["assignees"]
        for vid in a["visit_ids"]
    ]
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": substitutions,
        },
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["applied"]) == 2

    db.expunge_all()
    for v in (v1, v2):
        assert await _vsa_ids(db, v.id) == {c.id}


# ---------------------------------------------------------------------------
# #15 後方互換: plans キーが常に存在し visits[] 出力は不変
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backward_compat_plans_present_and_visits_intact(client, db) -> None:
    admin = await _make_user(db, email="p5-16@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(absent.id), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # plans キーは常に存在.
    assert "plans" in body
    # visits[] 出力は従来どおり (candidates ランキングが健在).
    assert len(body["visits"]) == 1
    vout = body["visits"][0]
    assert vout["visit_id"] == str(visit.id)
    cand_ids = {x["staff_id"] for x in vout["candidates"]}
    assert str(c.id) in cand_ids


# ---------------------------------------------------------------------------
# #16 空欠勤 (対象 visit なし) → plans も空
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_visits_yields_empty_plans(client, db) -> None:
    admin = await _make_user(db, email="p5-17@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    assert plans == []


# ---------------------------------------------------------------------------
# #17 exception 個別選択 + プラン展開の合流 apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_individual_selection_merged_apply(client, db) -> None:
    admin = await _make_user(db, email="p5-18@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    # open + female. 男性 C が open を担当, female は例外 → 女性 D が個別に担当.
    p_open = await _patient(db, office=o1, code="OPEN", lat=BASE[0], lng=BASE[1])
    p_f = await _patient(db, office=o1, code="F", lat=BASE[0], lng=BASE[1],
                         sex_restriction="female_only")
    course = await _course(db, office=o1, staff=absent)
    v_open = await _visit(db, patient=p_open, course=course, start=time(9, 0), end=time(9, 30),
                          primary=absent)
    v_f = await _visit(db, patient=p_f, course=course, start=time(10, 0), end=time(10, 30),
                       primary=absent)
    await _vsa(db, visit=v_open, staff=absent)
    await _vsa(db, visit=v_f, staff=absent)
    c = await _staff(db, name="C", office=o1, sex="male")
    d = await _staff(db, name="D", office=o1, sex="female")
    await _shift(db, staff=c)
    await _shift(db, staff=d)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    # C の tier1 プラン (例外 = female visit) を選ぶ.
    plan = next(p for p in _tier(plans, 1) if p["assignees"][0]["staff_id"] == str(c.id))
    assert plan["exception_count"] == 1
    assert plan["exceptions"][0]["visit_id"] == str(v_f.id)

    # プラン展開 (C→open) + 例外個別選択 (D→female) を合流.
    substitutions = [
        {"visit_id": vid, "substitute_staff_id": a["staff_id"]}
        for a in plan["assignees"]
        for vid in a["visit_ids"]
    ]
    substitutions.append({"visit_id": str(v_f.id), "substitute_staff_id": str(d.id)})

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": substitutions,
        },
    )
    assert res.status_code == 200, res.text
    db.expunge_all()
    assert await _vsa_ids(db, v_open.id) == {c.id}
    assert await _vsa_ids(db, v_f.id) == {d.id}


# ---------------------------------------------------------------------------
# #18 スコア順: 高継続の丸ごとプランが上位
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_plans_sorted_by_score(client, db) -> None:
    admin = await _make_user(db, email="p5-19@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    # C: 患者の直近担当 (継続 +100). D: 継続なし.
    c = await _staff(db, name="継続C", office=o1)
    await _shift(db, staff=c)
    d = await _staff(db, name="新規D", office=o1)
    await _shift(db, staff=d)
    past_date = TARGET_DATE - timedelta(days=7)
    past_course = await _course(db, office=o1, staff=c, code="B")
    past_visit = await _visit(db, patient=patient, course=past_course, start=time(10, 0),
                              end=time(10, 30), visit_date=past_date, primary=c)
    await _vsa(db, visit=past_visit, staff=c)
    await db.commit()

    plans = await _get_plans(client, admin, absent)
    tier1 = _tier(plans, 1)
    assert len(tier1) == 2
    # 継続ボーナスで C が上位 (score 降順).
    assert tier1[0]["assignees"][0]["staff_id"] == str(c.id)
    assert tier1[0]["score"] > tier1[1]["score"]
    assert tier1[0]["score"] == 10100.0  # 10000 + 継続 100
