"""Tests for P3-① 当日欠勤の代替スタッフ提案 (Commit 1: BE-core).

対象:
    - GET  /api/v1/schedule/staff-substitute/candidates
    - POST /api/v1/schedule/staff-substitute/apply
    - engine: app.services.scheduling.staff_substitute

設計書 docs/plans/p3-1-staff-substitute-design.md §6 Commit 1 のテスト計画:
    - 6 ハード制約それぞれの除外 + 理由コード集計 (N-6)
    - 時間衝突の境界 (移動 + バッファ込みで衝突 / 非衝突)
    - スコア順位 (continuity)
    - 2 名体制 primary / secondary 両ケースの VSA + Visit + partner 同期
    - POST 再検証 (status 遷移済 = 409 / 制約 violation = 422 / all-or-nothing)
    - register_absence
    - RBAC (staff 403 / no-auth 401)

ローカル SQLite のみ (本番 DB 禁止). 座標は千葉市近辺 (test_propose_slots_api と整合).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.staff import Staff, StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.visit import (
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
from app.services.scheduling.proposal_solver import _travel_buffer_between

ISO_YEAR = 2026
ISO_WEEK = 20
TARGET_DATE = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon), weekday 0
WEEKDAY = 0

BASE = (35.6000, 140.1000)
FAR = (35.6300, 140.1400)

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


async def _vsa_staff_ids(db, visit_id: uuid.UUID) -> set[uuid.UUID]:
    rows = (
        await db.scalars(
            select(VisitStaffAssignment.staff_id).where(
                VisitStaffAssignment.visit_id == visit_id
            )
        )
    ).all()
    return set(rows)


# ---------------------------------------------------------------------------
# 1. 6 ハード制約それぞれの除外 + 理由コード集計 (N-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hard_constraints_exclude_and_aggregate_reasons(client, db) -> None:
    admin = await _make_user(db, email="sub-a1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    o2 = await _office(db, name="O2", code="O2")

    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    # 対象患者は女性限定 (sex_mismatch を male 候補で誘発).
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1],
                             sex_restriction="female_only")
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0),
                         end=time(10, 30), primary=absent)
    await _vsa(db, visit=visit, staff=absent)

    # --- 各候補は狙った 1 制約だけ破る ---
    # no_work_day: シフト無し.
    await _staff(db, name="非番", office=o1)
    # sex_mismatch: 男性 (患者 female_only).
    c_sex = await _staff(db, name="男性", office=o1, sex="male")
    await _shift(db, staff=c_sex)
    # office_mismatch: 別拠点 O2.
    c_office = await _staff(db, name="他拠点", office=o2)
    await _shift(db, staff=c_office)
    # event_overlap: visit と重なる予定.
    c_event = await _staff(db, name="予定あり", office=o1)
    await _shift(db, staff=c_event)
    await _event(db, staff=c_event, start=time(10, 0), end=time(10, 30))
    # trainee_solo: 新人 + 1 名体制.
    c_trainee = await _staff(db, name="新人", office=o1, is_trainee=True)
    await _shift(db, staff=c_trainee)
    # time_conflict: 当日既存 visit が時間衝突.
    c_conflict = await _staff(db, name="埋まってる", office=o1)
    await _shift(db, staff=c_conflict)
    p_far = await _patient(db, office=o1, code="FAR", lat=FAR[0], lng=FAR[1])
    v_far = await _visit(db, patient=p_far, course=None, start=time(9, 50), end=time(10, 20),
                         primary=c_conflict)
    await _vsa(db, visit=v_far, staff=c_conflict)

    await db.commit()

    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(absent.id), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["visits"]) == 1
    v = body["visits"][0]
    assert v["candidates"] == []  # 全候補が除外
    reasons = {r["code"]: r["count"] for r in v["no_candidate_reasons"]}
    assert reasons == {
        "no_work_day": 1,
        "sex_mismatch": 1,
        "office_mismatch": 1,
        "event_overlap": 1,
        "trainee_solo": 1,
        "time_conflict": 1,
    }


# ---------------------------------------------------------------------------
# 2. 時間衝突の境界 (移動 + バッファ込みで衝突 / 非衝突)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_time_conflict_boundary(client, db) -> None:
    admin = await _make_user(db, email="sub-b1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    # 対象 visit は 10:00 開始.
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0),
                         end=time(10, 30), primary=absent)
    await _vsa(db, visit=visit, staff=absent)

    # 移動 + バッファ (BASE<->FAR) を実関数で算出し、境界ちょうど / +1 分をつくる.
    travel = _travel_buffer_between(
        BASE[0], BASE[1], FAR[0], FAR[1], config=DEFAULT_SCHEDULING_CONFIG
    )
    p_start_min = 10 * 60  # 600
    # 非衝突: 既存 end + travel == p_start (条件は strict > のため衝突しない).
    ok_end_min = p_start_min - travel
    ok_end = time(ok_end_min // 60, ok_end_min % 60)
    ok_start = time((ok_end_min - 30) // 60, (ok_end_min - 30) % 60)
    # 衝突: 既存 end を 1 分後ろ倒し → end + travel > p_start.
    ng_end_min = p_start_min - travel + 1
    ng_end = time(ng_end_min // 60, ng_end_min % 60)
    ng_start = time((ng_end_min - 30) // 60, (ng_end_min - 30) % 60)

    p_far = await _patient(db, office=o1, code="FAR", lat=FAR[0], lng=FAR[1])

    c_ok = await _staff(db, name="境界OK", office=o1)
    await _shift(db, staff=c_ok)
    v_ok = await _visit(db, patient=p_far, course=None, start=ok_start, end=ok_end,
                        primary=c_ok)
    await _vsa(db, visit=v_ok, staff=c_ok)

    c_ng = await _staff(db, name="境界NG", office=o1)
    await _shift(db, staff=c_ng)
    v_ng = await _visit(db, patient=p_far, course=None, start=ng_start, end=ng_end,
                        primary=c_ng)
    await _vsa(db, visit=v_ng, staff=c_ng)

    await db.commit()

    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(absent.id), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    v = res.json()["visits"][0]
    cand_ids = {c["staff_id"] for c in v["candidates"]}
    assert str(c_ok.id) in cand_ids  # 境界ちょうどは非衝突 → 候補に残る
    assert str(c_ng.id) not in cand_ids  # 1 分超過は衝突 → 除外
    reasons = {r["code"]: r["count"] for r in v["no_candidate_reasons"]}
    assert reasons.get("time_conflict") == 1


# ---------------------------------------------------------------------------
# 3. スコア順位 (continuity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_score_ranking_by_continuity(client, db) -> None:
    admin = await _make_user(db, email="sub-c1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0),
                         end=time(10, 30), primary=absent)
    await _vsa(db, visit=visit, staff=absent)

    # C1: 患者の直近担当 (continuity index 0 → +100).
    c1 = await _staff(db, name="継続担当", office=o1)
    await _shift(db, staff=c1)
    # C2: 継続なし (score 0).
    c2 = await _staff(db, name="新規", office=o1)
    await _shift(db, staff=c2)

    # C1 を患者の過去担当にする (week_monday より前 + staff_assigned course).
    past_date = TARGET_DATE - timedelta(days=7)
    past_course = await _course(db, office=o1, staff=c1, code="B")
    past_visit = await _visit(db, patient=patient, course=past_course, start=time(10, 0),
                              end=time(10, 30), visit_date=past_date, primary=c1)
    await _vsa(db, visit=past_visit, staff=c1)

    await db.commit()

    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(absent.id), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    cands = res.json()["visits"][0]["candidates"]
    assert [c["staff_id"] for c in cands] == [str(c1.id), str(c2.id)]
    assert cands[0]["score_breakdown"]["continuity_bonus"] == 100.0
    assert cands[0]["score"] == 100.0
    assert cands[1]["score_breakdown"]["continuity_bonus"] == 0.0


# ---------------------------------------------------------------------------
# 4. 2 名体制 primary / secondary の VSA + Visit + partner 同期
# ---------------------------------------------------------------------------


async def _seed_two_person(db, o1: Office):
    """2 名体制 (visit_group) を作る. A=primary of V1 / B=primary of V2, 相互 secondary."""
    a = await _staff(db, name="A", office=o1)
    b = await _staff(db, name="B", office=o1)
    await _shift(db, staff=a)
    await _shift(db, staff=b)
    patient = await _patient(db, office=o1, code="TP", lat=BASE[0], lng=BASE[1])
    group_id = uuid.uuid4()
    course_a = await _course(db, office=o1, staff=a, code="A")
    course_b = await _course(db, office=o1, staff=b, code="B")
    v1 = await _visit(db, patient=patient, course=course_a, start=time(10, 0), end=time(10, 30),
                      required_staff_count=2, visit_group_id=group_id, primary=a, secondary=b)
    v2 = await _visit(db, patient=patient, course=course_b, start=time(10, 0), end=time(10, 30),
                      required_staff_count=2, visit_group_id=group_id, primary=b, secondary=a)
    await _vsa(db, visit=v1, staff=a)
    await _vsa(db, visit=v1, staff=b)
    await _vsa(db, visit=v2, staff=b)
    await _vsa(db, visit=v2, staff=a)
    return a, b, v1, v2


@pytest.mark.asyncio
async def test_two_person_primary_absence_syncs_partner(client, db) -> None:
    admin = await _make_user(db, email="sub-d1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    a, b, v1, v2 = await _seed_two_person(db, o1)
    # 代替候補 C.
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    # A (V1 の primary) を欠勤 → V1 を差替え.
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(a.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [{"visit_id": str(v1.id), "substitute_staff_id": str(c.id)}],
        },
    )
    assert res.status_code == 200, res.text
    applied = res.json()["applied"]
    assert len(applied) == 1
    assert applied[0]["visit_group_partner_visit_id"] == str(v2.id)

    db.expunge_all()  # endpoint は別セッションで commit 済 → 識別マップを破棄して再読込.
    # VSA: V1={C,B}, V2={B,C}. A は両行から消える.
    assert await _vsa_staff_ids(db, v1.id) == {c.id, b.id}
    assert await _vsa_staff_ids(db, v2.id) == {b.id, c.id}
    # Visit primary/secondary 同期 + 保護フラグ.
    v1r = await db.get(Visit, v1.id)
    v2r = await db.get(Visit, v2.id)
    assert v1r.primary_staff_id == c.id and v1r.secondary_staff_id == b.id
    assert v2r.primary_staff_id == b.id and v2r.secondary_staff_id == c.id
    assert v1r.manual_staff_override is True
    assert v2r.manual_staff_override is True


@pytest.mark.asyncio
async def test_two_person_secondary_absence_syncs_partner(client, db) -> None:
    admin = await _make_user(db, email="sub-d2@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    a, b, v1, v2 = await _seed_two_person(db, o1)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    # A は V2 の secondary. V2 を指定して差替え (secondary 欠勤ケース).
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(a.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [{"visit_id": str(v2.id), "substitute_staff_id": str(c.id)}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"][0]["visit_group_partner_visit_id"] == str(v1.id)

    db.expunge_all()
    assert await _vsa_staff_ids(db, v1.id) == {c.id, b.id}
    assert await _vsa_staff_ids(db, v2.id) == {b.id, c.id}
    v1r = await db.get(Visit, v1.id)
    v2r = await db.get(Visit, v2.id)
    # A は V1 の primary / V2 の secondary. group 全行で A→C 同期される.
    assert v1r.primary_staff_id == c.id
    assert v2r.secondary_staff_id == c.id


@pytest.mark.asyncio
async def test_two_person_get_excludes_partner_and_marks_count(client, db) -> None:
    admin = await _make_user(db, email="sub-d3@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    a, b, v1, v2 = await _seed_two_person(db, o1)
    await db.commit()

    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(a.id), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 200, res.text
    visits = res.json()["visits"]
    # group は代表 1 行に集約される.
    assert len(visits) == 1
    v = visits[0]
    assert v["required_staff_count"] == 2
    assert v["visit_group_id"] is not None
    cand_ids = {c["staff_id"] for c in v["candidates"]}
    assert str(b.id) not in cand_ids  # 相方 B は候補から除外


# ---------------------------------------------------------------------------
# 5. POST 再検証 (409 / 422 / all-or-nothing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_status_transitioned_returns_409(client, db) -> None:
    admin = await _make_user(db, email="sub-e1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    # status を in_progress にする (planned でない → 409).
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         status=VISIT_STATUS_IN_PROGRESS, primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [{"visit_id": str(visit.id), "substitute_staff_id": str(c.id)}],
        },
    )
    assert res.status_code == 409, res.text
    # 書込されていない.
    assert await _vsa_staff_ids(db, visit.id) == {absent.id}


@pytest.mark.asyncio
async def test_apply_constraint_violation_returns_422_all_or_nothing(client, db) -> None:
    admin = await _make_user(db, email="sub-e2@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    o2 = await _office(db, name="O2", code="O2")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    patient1 = await _patient(db, office=o1, code="T1", lat=BASE[0], lng=BASE[1])
    patient2 = await _patient(db, office=o1, code="T2", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    v1 = await _visit(db, patient=patient1, course=course, start=time(10, 0), end=time(10, 30),
                      primary=absent)
    v2 = await _visit(db, patient=patient2, course=course, start=time(11, 0), end=time(11, 30),
                      primary=absent)
    await _vsa(db, visit=v1, staff=absent)
    await _vsa(db, visit=v2, staff=absent)
    # valid substitute.
    c_ok = await _staff(db, name="OK", office=o1)
    await _shift(db, staff=c_ok)
    # invalid substitute (別拠点 → office_mismatch).
    c_bad = await _staff(db, name="NG", office=o2)
    await _shift(db, staff=c_bad)
    await db.commit()

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [
                {"visit_id": str(v1.id), "substitute_staff_id": str(c_ok.id)},
                {"visit_id": str(v2.id), "substitute_staff_id": str(c_bad.id)},
            ],
        },
    )
    assert res.status_code == 422, res.text
    db.expunge_all()
    # all-or-nothing: 有効な方も適用されていない.
    assert await _vsa_staff_ids(db, v1.id) == {absent.id}
    assert await _vsa_staff_ids(db, v2.id) == {absent.id}
    v1r = await db.get(Visit, v1.id)
    assert v1r.manual_staff_override is False


@pytest.mark.asyncio
async def test_apply_single_success(client, db) -> None:
    admin = await _make_user(db, email="sub-e3@example.com", role="admin")
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

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [{"visit_id": str(visit.id), "substitute_staff_id": str(c.id)}],
        },
    )
    assert res.status_code == 200, res.text
    db.expunge_all()
    assert await _vsa_staff_ids(db, visit.id) == {c.id}
    vr = await db.get(Visit, visit.id)
    assert vr.primary_staff_id == c.id
    assert vr.manual_staff_override is True


# ---------------------------------------------------------------------------
# 6. register_absence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_register_absence_creates_override(client, db) -> None:
    admin = await _make_user(db, email="sub-f1@example.com", role="admin")
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

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [{"visit_id": str(visit.id), "substitute_staff_id": str(c.id)}],
            "register_absence": True,
        },
    )
    assert res.status_code == 200, res.text
    ov = await db.scalar(
        select(StaffWeeklyOverride).where(
            StaffWeeklyOverride.staff_id == absent.id,
            StaffWeeklyOverride.iso_year == ISO_YEAR,
            StaffWeeklyOverride.iso_week == ISO_WEEK,
            StaffWeeklyOverride.weekday == WEEKDAY,
        )
    )
    assert ov is not None
    assert ov.override_type == "off"


# ---------------------------------------------------------------------------
# 7. RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_staff_forbidden_and_no_auth_unauthorized(client, db) -> None:
    staff_user = await _make_user(db, email="sub-g1@example.com", role="staff")
    manager = await _make_user(db, email="sub-g2@example.com", role="manager")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await db.commit()

    params = {"absent_staff_id": str(absent.id), "target_date": TARGET_DATE.isoformat()}

    # staff は 403.
    res = await client.get(CANDIDATES_URL, headers=_bearer(staff_user), params=params)
    assert res.status_code == 403, res.text

    # no-auth は 401.
    res = await client.get(CANDIDATES_URL, params=params)
    assert res.status_code == 401, res.text

    # manager は許可 (200).
    res = await client.get(CANDIDATES_URL, headers=_bearer(manager), params=params)
    assert res.status_code == 200, res.text

    # POST も staff 403.
    res = await client.post(
        APPLY_URL,
        headers=_bearer(staff_user),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [
                {"visit_id": str(uuid.uuid4()), "substitute_staff_id": str(uuid.uuid4())}
            ],
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 8. intra-batch 相互衝突 (extra_others 経路)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_intra_batch_mutual_conflict_returns_422(client, db) -> None:
    """同一 substitute に時間が重なる 2 件を割り当てる単一 POST → 422 + time_conflict."""
    admin = await _make_user(db, email="sub-h1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    # 2 患者: BASE / FAR — 必ず移動時間が発生.
    p1 = await _patient(db, office=o1, code="I1", lat=BASE[0], lng=BASE[1])
    p2 = await _patient(db, office=o1, code="I2", lat=FAR[0], lng=FAR[1])
    course = await _course(db, office=o1, staff=absent)
    # v1: 10:00-10:30, v2: 10:15-10:45 → 直接重複 (どんな移動時間でも衝突).
    v1 = await _visit(db, patient=p1, course=course, start=time(10, 0), end=time(10, 30),
                      primary=absent)
    v2 = await _visit(db, patient=p2, course=course, start=time(10, 15), end=time(10, 45),
                      primary=absent)
    await _vsa(db, visit=v1, staff=absent)
    await _vsa(db, visit=v2, staff=absent)
    # 代替候補 C: 当日他の visit なし → 単独なら両制約を満たすが、2 件同時は衝突.
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [
                {"visit_id": str(v1.id), "substitute_staff_id": str(c.id)},
                {"visit_id": str(v2.id), "substitute_staff_id": str(c.id)},
            ],
        },
    )
    assert res.status_code == 422, res.text
    assert "time_conflict" in res.json().get("detail", "")
    # all-or-nothing: どちらも適用されていない.
    db.expunge_all()
    assert await _vsa_staff_ids(db, v1.id) == {absent.id}
    assert await _vsa_staff_ids(db, v2.id) == {absent.id}


# ---------------------------------------------------------------------------
# 9. register_absence=True + 既存 StaffWeeklyOverride → skip + warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_register_absence_skips_existing_override_with_warning(client, db) -> None:
    admin = await _make_user(db, email="sub-f2@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    await _shift(db, staff=absent)
    # 既存 override を事前登録.
    db.add(
        StaffWeeklyOverride(
            staff_id=absent.id,
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            weekday=WEEKDAY,
            override_type="off",
            reason="既存",
        )
    )
    patient = await _patient(db, office=o1, code="TGT", lat=BASE[0], lng=BASE[1])
    course = await _course(db, office=o1, staff=absent)
    visit = await _visit(db, patient=patient, course=course, start=time(10, 0), end=time(10, 30),
                         primary=absent)
    await _vsa(db, visit=visit, staff=absent)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [{"visit_id": str(visit.id), "substitute_staff_id": str(c.id)}],
            "register_absence": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert any("スキップ" in w for w in body["warnings"]), f"warnings={body['warnings']}"


# ---------------------------------------------------------------------------
# 10. GET 404 / POST 404 / 2 回目 POST 422 (競合検知)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_unknown_absent_staff_returns_404(client, db) -> None:
    admin = await _make_user(db, email="sub-i1@example.com", role="admin")
    await db.commit()
    res = await client.get(
        CANDIDATES_URL,
        headers=_bearer(admin),
        params={"absent_staff_id": str(uuid.uuid4()), "target_date": TARGET_DATE.isoformat()},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_post_unknown_visit_id_returns_404(client, db) -> None:
    admin = await _make_user(db, email="sub-i2@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    absent = await _staff(db, name="欠勤者", office=o1)
    c = await _staff(db, name="C", office=o1)
    await db.commit()

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(absent.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [
                {"visit_id": str(uuid.uuid4()), "substitute_staff_id": str(c.id)}
            ],
        },
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_post_second_apply_same_substitution_returns_422(client, db) -> None:
    """同一差替えの 2 回目 POST は 422 (欠勤スタッフが visit の primary/secondary にいない)."""
    admin = await _make_user(db, email="sub-i3@example.com", role="admin")
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

    payload = {
        "absent_staff_id": str(absent.id),
        "target_date": TARGET_DATE.isoformat(),
        "substitutions": [{"visit_id": str(visit.id), "substitute_staff_id": str(c.id)}],
    }
    # 1 回目: 成功.
    res = await client.post(APPLY_URL, headers=_bearer(admin), json=payload)
    assert res.status_code == 200, res.text
    # 2 回目: primary が C に変わっているため absent が割当になく 422.
    res = await client.post(APPLY_URL, headers=_bearer(admin), json=payload)
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 11. 同一 visit_group_id を複数リクエスト → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_duplicate_visit_group_id_returns_422(client, db) -> None:
    """同一 visit_group の v1 / v2 を両方 substitutions に含む → 422."""
    admin = await _make_user(db, email="sub-j1@example.com", role="admin")
    o1 = await _office(db, name="O1", code="O1")
    a, b, v1, v2 = await _seed_two_person(db, o1)
    c = await _staff(db, name="C", office=o1)
    await _shift(db, staff=c)
    await db.commit()

    # v1 (primary=a) と v2 (secondary=a) を同時に送信 → visit_group 重複で 422.
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "absent_staff_id": str(a.id),
            "target_date": TARGET_DATE.isoformat(),
            "substitutions": [
                {"visit_id": str(v1.id), "substitute_staff_id": str(c.id)},
                {"visit_id": str(v2.id), "substitute_staff_id": str(c.id)},
            ],
        },
    )
    assert res.status_code == 422, res.text
