"""週空間 Phase E / BE-1 — POST /api/v1/schedule/v2/substitute-candidates

正典: docs/plans/week-cockpit-design.md §2-1 (代替候補 API の契約・2026-08-22 追補)。

検証観点:
  1. ◎(ok): ハード制約すべて OK かつ 時間重なり無し
  2. ×(ng): 休み (off) / 非勤務日 / NG スタッフ / 性別 / 新人 / 拠点
  3. △(warn): イベント重なり (±15分バッファ) / 本人の別訪問との時間重なり / 同行拘束
  4. 時間重なりは既存規則と同じ = 同住所ペアは免除・座標欠損は免除しない
  5. 整列 (ok→warn→ng → score desc) / 継続性 (馴染み) 加点 / 対象スタッフ自身は含まない
  6. cancelled / deleted 訪問・cancelled_at 付きイベントは対象外
  7. コース担当フォールバック (primary_staff_id NULL) / course_id 絞り込み
  8. read-only (INSERT/UPDATE/DELETE を 1 本も出さない) / RBAC: staff は 403
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from sqlalchemy import event, func, select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.accompaniment import Accompaniment
from app.models.course import Course
from app.models.office import Office
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import StaffEvent, StaffShift, StaffWeeklyOverride
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.substitute_candidates import build_substitute_candidates

_URL = "/api/v1/schedule/v2/substitute-candidates"

# 2026-09-04 = 金曜 = ISO 2026-W36 weekday4
_ISO_YEAR, _ISO_WEEK, _WEEKDAY = 2026, 36, 4
_DATE = date.fromisocalendar(_ISO_YEAR, _ISO_WEEK, _WEEKDAY + 1)

# 稲毛あたりの座標 (同住所は .3f バケット一致 = 約 100m 以内)。
_LAT_A, _LNG_A = 35.6300, 140.1000
_LAT_A_NEAR = 35.63004  # 同住所バケット
_LAT_B, _LNG_B = 35.7000, 140.2000  # 別住所 (十分離れている)


# ---------------------------------------------------------------------------
# fixtures (test_course_move_weekday.py の作法に倣う)
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_staff(
    db,
    *,
    name: str,
    office: Office | None = None,
    sex: str | None = None,
    is_trainee: bool = False,
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4, 5),
) -> Staff:
    s = Staff(
        name=name,
        sex=sex,
        status="active",
        role="staff",
        is_trainee=is_trainee,
        primary_office_id=office.id if office is not None else None,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    for wd in work_days:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.commit()
    return s


async def _make_patient(
    db,
    *,
    code: str,
    sex_restriction: str | None = None,
    lat: float | None = _LAT_A,
    lng: float | None = _LNG_A,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        special_week_active=[],
        sex_restriction=sex_restriction,
        lat=lat,
        lng=lng,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course(
    db,
    *,
    office: Office,
    code: str = "A",
    assigned_staff: Staff | None = None,
    iso_week: int = _ISO_WEEK,
    course_status: str = "staff_assigned",
) -> Course:
    c = Course(
        iso_year=_ISO_YEAR,
        iso_week=iso_week,
        weekday=_WEEKDAY,
        code=code,
        course_status=course_status,
        office_id=office.id,
        assigned_staff_id=assigned_staff.id if assigned_staff is not None else None,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_visit(
    db,
    *,
    patient: Patient,
    course: Course | None = None,
    staff: Staff | None = None,
    start: time = time(10, 0),
    end: time = time(11, 0),
    status_value: str = "planned",
    visit_date: date | None = None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        course_id=course.id if course is not None else None,
        primary_staff_id=staff.id if staff is not None else None,
        visit_date=visit_date or _DATE,
        start_time=start,
        end_time=end,
        type="regular",
        status=status_value,
        source="auto",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _candidate(body: dict, staff: Staff, *, group_index: int = 0) -> dict:
    group = body["groups"][group_index]
    return next(c for c in group["candidates"] if c["staff_id"] == str(staff.id))


def _codes(cand: dict) -> set[str]:
    return {r["code"] for r in cand["reasons"]}


async def _post(client, user: User, *, staff: Staff, course_id=None):
    payload: dict = {"staff_id": str(staff.id), "date": _DATE.isoformat()}
    if course_id is not None:
        payload["course_id"] = str(course_id)
    return await client.post(_URL, headers=_bearer(user), json=payload)


# ---------------------------------------------------------------------------
# 1. 基本形 — ◎ が出る / 自分自身は含まれない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_ok_candidate_and_excludes_absent_staff(client, db) -> None:
    admin = await _make_user(db, email="sub-1@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB1")
    absent = await _make_staff(db, name="休む人", office=office)
    other = await _make_staff(db, name="代わりの人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-1")
    visit = await _make_visit(db, patient=patient, course=course, staff=absent)

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["absent_staff"]["id"] == str(absent.id)
    assert body["date"] == _DATE.isoformat()
    assert body["weekday"] == _WEEKDAY
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert group["course_id"] == str(course.id)
    assert group["course_label"] == "Aコース"
    assert [v["visit_id"] for v in group["visits"]] == [str(visit.id)]
    assert group["visits"][0]["start_time"] == "10:00"
    assert group["visits"][0]["end_time"] == "11:00"

    # 対象スタッフ自身は候補に含めない
    assert str(absent.id) not in [c["staff_id"] for c in group["candidates"]]
    cand = _candidate(body, other)
    assert cand["status"] == "ok"
    assert cand["reasons"] == []
    assert cand["load_today"] == 0
    assert cand["office_name"] == "稲毛SUB1"


@pytest.mark.asyncio
async def test_no_visits_returns_empty_groups_with_warning(client, db) -> None:
    admin = await _make_user(db, email="sub-2@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB2")
    absent = await _make_staff(db, name="暇な人", office=office)

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["groups"] == []
    assert body["warnings"] == ["暇な人 さんはこの日に担当の訪問がありません。"]


@pytest.mark.asyncio
async def test_course_filter_empty_uses_distinct_warning(client, db) -> None:
    """course_id 指定で空になった場合は「その日担当なし」と文言を区別する."""
    admin = await _make_user(db, email="sub-2b@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB2b")
    absent = await _make_staff(db, name="休む人", office=office)
    mine = await _make_course(db, office=office, code="A", assigned_staff=absent)
    theirs = await _make_course(db, office=office, code="B")
    patient = await _make_patient(db, code="SUB-2b")
    await _make_visit(db, patient=patient, course=mine, staff=absent)

    res = await _post(client, admin, staff=absent, course_id=theirs.id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["groups"] == []
    assert body["warnings"] == ["指定されたコースには、休む人 さんのこの日の担当訪問がありません。"]


@pytest.mark.asyncio
async def test_deleted_course_id_returns_404(client, db) -> None:
    admin = await _make_user(db, email="sub-2c@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB2c")
    absent = await _make_staff(db, name="休む人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    course.deleted_at = datetime(2026, 9, 1, 9, 0)
    await db.commit()

    res = await _post(client, admin, staff=absent, course_id=course.id)
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 2. ng 判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ng_off_and_not_working_day(client, db) -> None:
    admin = await _make_user(db, email="sub-3@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB3")
    absent = await _make_staff(db, name="休む人", office=office)
    on_leave = await _make_staff(db, name="その日休みの人", office=office)
    # 金曜 (weekday4) をシフトから外した人 = 非勤務日
    no_shift = await _make_staff(db, name="金曜非番の人", office=office, work_days=(0, 1, 2, 3))
    course = await _make_course(db, office=office, assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-3")
    await _make_visit(db, patient=patient, course=course, staff=absent)

    db.add(
        StaffWeeklyOverride(
            staff_id=on_leave.id,
            iso_year=_ISO_YEAR,
            iso_week=_ISO_WEEK,
            weekday=_WEEKDAY,
            override_type="off",
        )
    )
    await db.commit()

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    body = res.json()

    off_cand = _candidate(body, on_leave)
    assert off_cand["status"] == "ng"
    assert _codes(off_cand) == {"off"}

    nw_cand = _candidate(body, no_shift)
    assert nw_cand["status"] == "ng"
    assert _codes(nw_cand) == {"not_working_day"}


@pytest.mark.asyncio
async def test_ng_staff_and_gender_and_trainee(client, db) -> None:
    admin = await _make_user(db, email="sub-4@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB4")
    absent = await _make_staff(db, name="休む人", office=office, sex="female")
    ng_staff = await _make_staff(db, name="NGの人", office=office, sex="female")
    male = await _make_staff(db, name="男性の人", office=office, sex="male")
    trainee = await _make_staff(db, name="新人", office=office, sex="female", is_trainee=True)
    ok_staff = await _make_staff(db, name="通る人", office=office, sex="female")

    course = await _make_course(db, office=office, assigned_staff=absent)
    # 制限なしの患者 + 女性限定の患者 → gender の理由は後者の訪問を指すこと
    free_patient = await _make_patient(db, code="SUB-4a")
    restricted = await _make_patient(db, code="SUB-4b", sex_restriction="female_only")
    await _make_visit(db, patient=free_patient, course=course, staff=absent)
    restricted_visit = await _make_visit(
        db,
        patient=restricted,
        course=course,
        staff=absent,
        start=time(13, 0),
        end=time(14, 0),
    )
    db.add(PatientNgStaff(patient_id=free_patient.id, staff_id=ng_staff.id))
    await db.commit()

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    body = res.json()

    ng_cand = _candidate(body, ng_staff)
    assert ng_cand["status"] == "ng"
    assert "ng_staff" in _codes(ng_cand)
    assert any(r["visit_id"] is not None for r in ng_cand["reasons"] if r["code"] == "ng_staff")

    male_cand = _candidate(body, male)
    assert male_cand["status"] == "ng"
    gender_reasons = [r for r in male_cand["reasons"] if r["code"] == "gender"]
    assert len(gender_reasons) == 1
    # 制限を持つ患者の訪問を指す (制限なしの患者の訪問ではない)
    assert gender_reasons[0]["visit_id"] == str(restricted_visit.id)
    assert "患者SUB-4b" in gender_reasons[0]["message"]

    trainee_cand = _candidate(body, trainee)
    assert trainee_cand["status"] == "ng"
    assert "trainee" in _codes(trainee_cand)

    assert _candidate(body, ok_staff)["status"] == "ok"


@pytest.mark.asyncio
async def test_ng_office_mismatch(client, db) -> None:
    admin = await _make_user(db, email="sub-5@example.com", role="admin")
    office_a = await _make_office(db, name="稲毛SUB5a")
    office_b = await _make_office(db, name="都賀SUB5b")
    absent = await _make_staff(db, name="休む人", office=office_a)
    far = await _make_staff(db, name="他拠点の人", office=office_b)
    near = await _make_staff(db, name="同拠点の人", office=office_a)
    course = await _make_course(db, office=office_a, assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-5")
    await _make_visit(db, patient=patient, course=course, staff=absent)

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    body = res.json()

    far_cand = _candidate(body, far)
    assert far_cand["status"] == "ng"
    assert "office" in _codes(far_cand)
    assert _candidate(body, near)["status"] == "ok"


# ---------------------------------------------------------------------------
# 3. warn 判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warn_event_overlap(client, db) -> None:
    admin = await _make_user(db, email="sub-6@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB6")
    absent = await _make_staff(db, name="休む人", office=office)
    busy = await _make_staff(db, name="会議の人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-6")
    await _make_visit(db, patient=patient, course=course, staff=absent)

    db.add(
        StaffEvent(
            staff_id=busy.id,
            event_type="meeting",
            starts_at=datetime.combine(_DATE, time(10, 30)),
            ends_at=datetime.combine(_DATE, time(11, 30)),
            title="打合せ",
        )
    )
    await db.commit()

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    cand = _candidate(res.json(), busy)
    assert cand["status"] == "warn"
    assert _codes(cand) == {"event_overlap"}


@pytest.mark.asyncio
async def test_cancelled_event_is_ignored(client, db) -> None:
    """「今週だけ外した」イベント (cancelled_at) は拘束にならない (mig 0075)."""
    admin = await _make_user(db, email="sub-6b@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB6b")
    absent = await _make_staff(db, name="休む人", office=office)
    freed = await _make_staff(db, name="朝会を外した人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-6b")
    await _make_visit(db, patient=patient, course=course, staff=absent)

    db.add(
        StaffEvent(
            staff_id=freed.id,
            event_type="meeting",
            starts_at=datetime.combine(_DATE, time(10, 30)),
            ends_at=datetime.combine(_DATE, time(11, 30)),
            title="朝会",
            source="fixed",
            cancelled_at=datetime(2026, 9, 3, 8, 0),
        )
    )
    await db.commit()

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    cand = _candidate(res.json(), freed)
    assert cand["status"] == "ok"
    assert cand["reasons"] == []


@pytest.mark.asyncio
async def test_warn_time_overlap_and_load_today(client, db) -> None:
    admin = await _make_user(db, email="sub-7@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB7")
    absent = await _make_staff(db, name="休む人", office=office)
    busy = await _make_staff(db, name="先約ありの人", office=office)
    free = await _make_staff(db, name="空いてる人", office=office)

    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-7a")
    await _make_visit(db, patient=patient, course=course, staff=absent)

    # busy は 10:30-11:30 に **別住所** の訪問 (= 時間重なり)
    other_course = await _make_course(db, office=office, code="B", assigned_staff=busy)
    other_patient = await _make_patient(db, code="SUB-7b", lat=_LAT_B, lng=_LNG_B)
    clash = await _make_visit(
        db,
        patient=other_patient,
        course=other_course,
        staff=busy,
        start=time(10, 30),
        end=time(11, 30),
    )
    # free は同じ日でも十分に離れた時間 (15:00-16:00)
    free_patient = await _make_patient(db, code="SUB-7c", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(
        db,
        patient=free_patient,
        course=other_course,
        staff=free,
        start=time(15, 0),
        end=time(16, 0),
    )

    res = await _post(client, admin, staff=absent, course_id=course.id)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["groups"]) == 1  # course_id で絞られている

    busy_cand = _candidate(body, busy)
    assert busy_cand["status"] == "warn"
    assert _codes(busy_cand) == {"time_overlap"}
    assert busy_cand["load_today"] == 1
    overlap = next(r for r in busy_cand["reasons"] if r["code"] == "time_overlap")
    assert overlap["visit_id"] is not None
    assert overlap["visit_id"] != str(clash.id)  # 対象訪問側を指す

    free_cand = _candidate(body, free)
    assert free_cand["status"] == "ok"
    assert free_cand["load_today"] == 1


@pytest.mark.asyncio
async def test_same_address_pair_is_exempt_but_missing_coords_is_not(client, db) -> None:
    """同住所ペアは免除 (90分占有ルール) / 座標欠損は免除せずブロック."""
    admin = await _make_user(db, email="sub-7d@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB7d")
    absent = await _make_staff(db, name="休む人", office=office)
    same_addr = await _make_staff(db, name="同じ玄関の人", office=office)
    no_coord = await _make_staff(db, name="座標なしの人", office=office)

    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-7d", lat=_LAT_A, lng=_LNG_A)
    await _make_visit(db, patient=patient, course=course, staff=absent)

    other_course = await _make_course(db, office=office, code="B")
    # 同住所 (.3f バケット一致) × 同時刻 → 免除
    pair_patient = await _make_patient(db, code="SUB-7e", lat=_LAT_A_NEAR, lng=_LNG_A)
    await _make_visit(
        db, patient=pair_patient, course=other_course, staff=same_addr, start=time(10, 0)
    )
    # 座標なし × 同時刻 → 免除しない
    unknown_patient = await _make_patient(db, code="SUB-7f", lat=None, lng=None)
    await _make_visit(
        db, patient=unknown_patient, course=other_course, staff=no_coord, start=time(10, 0)
    )

    res = await _post(client, admin, staff=absent, course_id=course.id)
    assert res.status_code == 200, res.text
    body = res.json()

    assert _candidate(body, same_addr)["status"] == "ok"
    nc = _candidate(body, no_coord)
    assert nc["status"] == "warn"
    assert _codes(nc) == {"time_overlap"}


@pytest.mark.asyncio
async def test_warn_accompaniment(client, db) -> None:
    """同行 (メンター) で拘束されている候補は warn + code='accompaniment'."""
    admin = await _make_user(db, email="sub-7g@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB7g")
    absent = await _make_staff(db, name="休む人", office=office)
    mentor = await _make_staff(db, name="同行中の人", office=office)

    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-7g", lat=_LAT_A, lng=_LNG_A)
    await _make_visit(db, patient=patient, course=course, staff=absent)

    # mentor は別コースの 10:30-11:30 の訪問に同行で入っている (担当ではない)
    other_course = await _make_course(db, office=office, code="B")
    acc_patient = await _make_patient(db, code="SUB-7h", lat=_LAT_B, lng=_LNG_B)
    acc_visit = await _make_visit(
        db,
        patient=acc_patient,
        course=other_course,
        staff=None,
        start=time(10, 30),
        end=time(11, 30),
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=mentor.id,
            target_type="visit",
            visit_id=acc_visit.id,
            kind="support",
            source="manual",
        )
    )
    await db.commit()

    res = await _post(client, admin, staff=absent, course_id=course.id)
    assert res.status_code == 200, res.text
    cand = _candidate(res.json(), mentor)
    assert cand["status"] == "warn"
    assert _codes(cand) == {"accompaniment"}
    # 同行は「自分の担当」ではないので負荷にはカウントしない
    assert cand["load_today"] == 0


# ---------------------------------------------------------------------------
# 4. 整列 / score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidates_sorted_ok_warn_ng(client, db) -> None:
    admin = await _make_user(db, email="sub-8@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB8")
    absent = await _make_staff(db, name="休む人", office=office)
    await _make_staff(db, name="B空き", office=office)
    busy = await _make_staff(db, name="A先約", office=office)
    await _make_staff(db, name="C非番", office=office, work_days=(0, 1, 2, 3))

    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-8a")
    await _make_visit(db, patient=patient, course=course, staff=absent)
    other_course = await _make_course(db, office=office, code="B", assigned_staff=busy)
    other_patient = await _make_patient(db, code="SUB-8b", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(
        db,
        patient=other_patient,
        course=other_course,
        staff=busy,
        start=time(10, 30),
        end=time(11, 30),
    )

    res = await _post(client, admin, staff=absent, course_id=course.id)
    assert res.status_code == 200, res.text
    cands = res.json()["groups"][0]["candidates"]
    statuses = [c["status"] for c in cands]
    assert statuses == sorted(statuses, key=lambda s: {"ok": 0, "warn": 1, "ng": 2}[s])
    ok_scores = [c["score"] for c in cands if c["status"] == "ok"]
    assert ok_scores == sorted(ok_scores, reverse=True)


@pytest.mark.asyncio
async def test_continuity_bonus_prefers_familiar_staff(client, db) -> None:
    """過去週にその患者を担当した人が score で上に来る (継続性 +・患者ごとに合算)."""
    admin = await _make_user(db, email="sub-8c@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB8c")
    absent = await _make_staff(db, name="休む人", office=office)
    familiar = await _make_staff(db, name="馴染みの人", office=office)
    stranger = await _make_staff(db, name="初めての人", office=office)

    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    p1 = await _make_patient(db, code="SUB-8c1", lat=_LAT_A, lng=_LNG_A)
    p2 = await _make_patient(db, code="SUB-8c2", lat=_LAT_A, lng=_LNG_A)
    await _make_visit(db, patient=p1, course=course, staff=absent)
    await _make_visit(
        db, patient=p2, course=course, staff=absent, start=time(13, 0), end=time(14, 0)
    )

    # 前週 (staff_assigned コース) に familiar が 2 名とも担当していた
    prev_course = await _make_course(
        db, office=office, code="A", assigned_staff=familiar, iso_week=_ISO_WEEK - 1
    )
    prev_date = _DATE.fromordinal(_DATE.toordinal() - 7)
    for p in (p1, p2):
        pv = await _make_visit(
            db, patient=p, course=prev_course, staff=familiar, visit_date=prev_date
        )
        db.add(VisitStaffAssignment(visit_id=pv.id, staff_id=familiar.id))
    await db.commit()

    res = await _post(client, admin, staff=absent, course_id=course.id)
    assert res.status_code == 200, res.text
    body = res.json()
    fam = _candidate(body, familiar)
    other = _candidate(body, stranger)
    assert fam["status"] == "ok"
    # 2 患者ぶん合算 = 1000 * 2 (直近 1 回前 = COST_PATIENT_RECENT_1 / SCALE)
    assert fam["score"] == pytest.approx(2000.0)
    assert other["score"] == pytest.approx(0.0)
    assert fam["score"] > other["score"]
    # 整列でも上に来る
    ok_ids = [c["staff_id"] for c in body["groups"][0]["candidates"] if c["status"] == "ok"]
    assert ok_ids.index(str(familiar.id)) < ok_ids.index(str(stranger.id))


# ---------------------------------------------------------------------------
# 5. 対象訪問の絞り込み / 所有者判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_falls_back_to_course_assigned_staff(client, db) -> None:
    """primary_staff_id が NULL の訪問はコース担当を持ち主として扱う (盤面と同規則)."""
    admin = await _make_user(db, email="sub-9b@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB9b")
    absent = await _make_staff(db, name="休む人", office=office)
    loaded = await _make_staff(db, name="コース持ちの人", office=office)

    course = await _make_course(db, office=office, code="A", assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-9b")
    # primary_staff_id は NULL、コース担当が absent
    visit = await _make_visit(db, patient=patient, course=course, staff=None)

    # loaded も primary NULL のコース担当訪問を持つ (= load_today に乗る)
    other_course = await _make_course(db, office=office, code="B", assigned_staff=loaded)
    other_patient = await _make_patient(db, code="SUB-9c", lat=_LAT_B, lng=_LNG_B)
    await _make_visit(
        db,
        patient=other_patient,
        course=other_course,
        staff=None,
        start=time(15, 0),
        end=time(16, 0),
    )

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [v["visit_id"] for v in body["groups"][0]["visits"]] == [str(visit.id)]
    assert _candidate(body, loaded)["load_today"] == 1


@pytest.mark.asyncio
async def test_cancelled_and_deleted_visits_are_excluded(client, db) -> None:
    admin = await _make_user(db, email="sub-9@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB9")
    absent = await _make_staff(db, name="休む人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    p_ok = await _make_patient(db, code="SUB-9a")
    p_cancelled = await _make_patient(db, code="SUB-9d")
    p_deleted = await _make_patient(db, code="SUB-9e")

    kept = await _make_visit(db, patient=p_ok, course=course, staff=absent)
    await _make_visit(
        db,
        patient=p_cancelled,
        course=course,
        staff=absent,
        start=time(13, 0),
        end=time(14, 0),
        status_value="cancelled",
    )
    gone = await _make_visit(
        db, patient=p_deleted, course=course, staff=absent, start=time(15, 0), end=time(16, 0)
    )
    gone.deleted_at = datetime(2026, 9, 3, 12, 0)
    await db.commit()

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    visit_ids = [v["visit_id"] for v in res.json()["groups"][0]["visits"]]
    assert visit_ids == [str(kept.id)]


@pytest.mark.asyncio
async def test_visits_without_course_are_grouped_separately(client, db) -> None:
    admin = await _make_user(db, email="sub-10@example.com", role="admin")
    office = await _make_office(db, name="稲毛SUB10")
    absent = await _make_staff(db, name="休む人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    p1 = await _make_patient(db, code="SUB-10a")
    p2 = await _make_patient(db, code="SUB-10b")
    await _make_visit(db, patient=p1, course=course, staff=absent)
    await _make_visit(db, patient=p2, course=None, staff=absent, start=time(14, 0), end=time(15, 0))

    res = await _post(client, admin, staff=absent)
    assert res.status_code == 200, res.text
    groups = res.json()["groups"]
    assert len(groups) == 2
    assert groups[0]["course_id"] == str(course.id)
    assert groups[1]["course_id"] is None
    assert groups[1]["course_label"] == "臨時・未所属"


# ---------------------------------------------------------------------------
# 6. read-only / RBAC / 入力検証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_issues_no_write_statements(db) -> None:
    """read-only の証明: 実行中に INSERT / UPDATE / DELETE を 1 本も出さない.

    HTTP 経由だと監査ログ middleware が audit_logs へ 1 行書くため、BE-1 の責務
    (= サービス層) を直接叩いて DBAPI レベルで観測する。
    """
    from app.db import session as db_session

    office = await _make_office(db, name="稲毛SUB13")
    absent = await _make_staff(db, name="休む人", office=office)
    other = await _make_staff(db, name="代わりの人", office=office)
    course = await _make_course(db, office=office, assigned_staff=absent)
    patient = await _make_patient(db, code="SUB-13")
    await _make_visit(db, patient=patient, course=course, staff=absent)
    visits_before = await db.scalar(select(func.count()).select_from(Visit))

    captured: list[str] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        captured.append(statement)

    engine = db_session.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        res = await build_substitute_candidates(db, staff_id=absent.id, target_date=_DATE)
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert _candidate(res.model_dump(mode="json"), other)["status"] == "ok"
    writes = [s for s in captured if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert writes == [], writes
    db.expire_all()
    assert await db.scalar(select(func.count()).select_from(Visit)) == visits_before


@pytest.mark.asyncio
async def test_staff_role_is_forbidden(client, db) -> None:
    office = await _make_office(db, name="稲毛SUB11")
    absent = await _make_staff(db, name="休む人", office=office)
    staff_user = await _make_user(db, email="sub-11@example.com", role="staff", staff_id=absent.id)

    res = await _post(client, staff_user, staff=absent)
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_unknown_staff_returns_404(client, db) -> None:
    admin = await _make_user(db, email="sub-12@example.com", role="admin")
    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": "00000000-0000-0000-0000-0000000000ff",
            "date": _DATE.isoformat(),
        },
    )
    assert res.status_code == 404, res.text
