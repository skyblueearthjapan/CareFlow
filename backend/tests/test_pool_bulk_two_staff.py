"""保留プールの一括投入で 2 名で訪問の方を置く (biweekly-monthly-design-2026-10-30.md §9).

PO 決定 2026-10-30: 2 名で訪問の方 (requires_multiple_staff) も、保留プールの一括投入で
同じ時刻に 2 人そろう組 (別のコース 2 つ) を探して置く. 以前は two_staff_pending で対象外.

検証内容:
    - 2 人とも空いている時刻に、2 コースの組で 1 件として置かれる (担当 2 人の名前つき).
    - apply で固定訪問が 2 行 (slot_index 0/1・別のコースのひな形) になり、今週の訪問が
      同じ visit_group_id の 2 件 (required_staff_count=2) になる.
    - どちらかの人が NG スタッフ / 性別の決まりに合わない / 休み → その人の組は使わない.
    - NG 曜日・隔週のお休みの週 → 置かない.
    - 組ができない → 保留プールに残り、理由は no_pair.
    - 1 名の方は今までどおり (partner_course_code なし).
    - simulate は何度回しても同じ結果で、apply はその結果どおりに書く.

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffEvent, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 23
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)

SIMULATE_URL = "/api/v1/schedule/v2/pool-bulk-simulate"
APPLY_URL = "/api/v1/schedule/v2/pool-bulk-apply"


async def _admin(db, email: str) -> User:
    user = User(email=email, password_hash=hash_password("x"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _office(db, code: str) -> Office:
    office = Office(name=f"稲{code}", code=code)
    db.add(office)
    await db.flush()
    return office


async def _course(
    db,
    office: Office,
    code: str,
    *,
    sex: str | None = "female",
    shift_on: bool = True,
    staff_name: str | None = None,
    weekday: int = 0,
    staff: Staff | None = None,
    template_id: Any = None,
) -> tuple[Course, Staff]:
    """その曜日 (既定は月曜) のコース (ひな形・担当・近くの既存訪問 1 件つき) を作る.

    ``staff`` を渡すとその人を担当にする (同じ人が 2 コースを持つ場合の試験用).
    """
    if staff is None:
        staff = Staff(
            name=staff_name or f"看護師{code}",
            role="staff",
            is_trainee=False,
            primary_office_id=office.id,
            sex=sex,
        )
        db.add(staff)
        await db.flush()
        if shift_on:
            db.add(StaffShift(staff_id=staff.id, weekday=weekday, is_on=True))
    if template_id is None:
        tpl = CourseTemplate(office_id=office.id, label=code)
        db.add(tpl)
        await db.flush()
        template_id = tpl.id
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
        template_id=template_id,
    )
    db.add(course)
    await db.flush()
    anchor = Patient(
        code=f"ANC{code}{uuid4().hex[:6]}",
        name=f"既存{code}",
        status="active",
        lat=NEAR[0],
        lng=NEAR[1],
        primary_office_id=office.id,
    )
    db.add(anchor)
    await db.flush()
    db.add(
        Visit(
            patient_id=anchor.id,
            visit_date=WEEK_MONDAY + timedelta(days=weekday),
            start_time=time(9, 30),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
            primary_staff_id=staff.id,
        )
    )
    await db.flush()
    return course, staff


def _wp(**extra: Any) -> dict[str, Any]:
    wp: dict[str, Any] = {
        "service_minutes": 30,
        "time_type": "終日",
        "preferred_weekdays": ["Mon"],
        "frequency_per_week": 1,
    }
    wp.update(extra)
    return wp


async def _patient(
    db,
    office: Office,
    code: str,
    *,
    multi: bool = True,
    wp: dict[str, Any] | None = None,
    sex_restriction: str | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        lat=BASE[0],
        lng=BASE[1],
        primary_office_id=office.id,
        requires_multiple_staff=multi,
        weekly_pattern=wp if wp is not None else _wp(),
        sex_restriction=sex_restriction,
    )
    db.add(p)
    await db.flush()
    return p


async def _simulate(client, admin: User, office: Office, patients: list[Patient]) -> dict:
    res = await client.post(
        SIMULATE_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(p.id) for p in patients],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _unplaced_reason(body: dict, patient: Patient) -> str | None:
    for u in body["unplaced"]:
        if u["patient_id"] == str(patient.id):
            return u["reason"]
    return None


# ---------------------------------------------------------------------------
# 置ける / apply の形
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pair_placed_and_applied_as_grouped_visits(client, db) -> None:
    admin = await _admin(db, "2s-bulk-ok@example.com")
    office = await _office(db, "TS1")
    course_a, staff_a = await _course(db, office, "A", staff_name="佐藤")
    course_b, staff_b = await _course(db, office, "B", staff_name="鈴木")
    p = await _patient(db, office, "TS-OK")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert len(body["placements"]) == 1, body
    pl = body["placements"][0]
    assert pl["patient_id"] == str(p.id)
    assert {pl["course_code"], pl["partner_course_code"]} == {"A", "B"}
    assert {pl["staff_name"], pl["partner_staff_name"]} == {"佐藤", "鈴木"}
    assert "two_staff_not_guaranteed" not in pl["warnings"]
    assert body["kpi"]["placed_patients"] == 1 and body["kpi"]["placed_slots"] == 1
    assert _unplaced_reason(body, p) is None

    # 決定性: もう一度回しても同じ結果.
    again = await _simulate(client, admin, office, [p])
    assert again["placements"] == body["placements"]
    assert again["state_token"] == body["state_token"]

    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "placements": body["placements"],
            "state_token": body["state_token"],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied_slots"] == 1

    tpl_by_code = {course_a.code: course_a.template_id, course_b.code: course_b.template_id}
    pfvs = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == p.id, PatientFixedVisit.mode == "normal"
            )
        )
    ).all()
    by_slot = {r.slot_index: r for r in pfvs}
    assert set(by_slot) == {0, 1}, pfvs
    assert by_slot[0].course_template_id == tpl_by_code[pl["course_code"]]
    assert by_slot[1].course_template_id == tpl_by_code[pl["partner_course_code"]]
    assert by_slot[0].weekday == by_slot[1].weekday == 0
    assert by_slot[0].start_time == by_slot[1].start_time
    assert by_slot[0].start_time.strftime("%H:%M:%S") == pl["start_time"]

    visits = (
        await db.scalars(select(Visit).where(Visit.patient_id == p.id, Visit.deleted_at.is_(None)))
    ).all()
    assert len(visits) == 2, visits
    assert visits[0].visit_group_id is not None
    assert visits[0].visit_group_id == visits[1].visit_group_id
    assert {v.required_staff_count for v in visits} == {2}
    assert {v.course_id for v in visits} == {course_a.id, course_b.id}
    assert {v.primary_staff_id for v in visits} == {staff_a.id, staff_b.id}
    assert {v.start_time.strftime("%H:%M:%S") for v in visits} == {pl["start_time"]}


@pytest.mark.asyncio
async def test_single_staff_patient_unchanged(client, db) -> None:
    """1 名の方は今までどおり 1 コースに置かれ、partner_* は null."""
    admin = await _admin(db, "2s-bulk-single@example.com")
    office = await _office(db, "TS2")
    await _course(db, office, "A")
    await _course(db, office, "B")
    p = await _patient(db, office, "TS-SINGLE", multi=False)
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert len(body["placements"]) == 1, body
    pl = body["placements"][0]
    assert pl["partner_course_code"] is None
    assert pl["partner_staff_name"] is None
    assert pl["course_code"] in {"A", "B"}


# ---------------------------------------------------------------------------
# 決まりはどちらの人にも効く
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ng_staff_on_either_side_excludes_pair(client, db) -> None:
    admin = await _admin(db, "2s-bulk-ng@example.com")
    office = await _office(db, "TS3")
    await _course(db, office, "A")
    _, staff_b = await _course(db, office, "B")
    _, staff_c = await _course(db, office, "C")
    p = await _patient(db, office, "TS-NG")
    db.add(PatientNgStaff(patient_id=p.id, staff_id=staff_b.id))
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert len(body["placements"]) == 1, body
    pl = body["placements"][0]
    assert {pl["course_code"], pl["partner_course_code"]} == {"A", "C"}

    # C の人も NG にすると組ができない.
    db.add(PatientNgStaff(patient_id=p.id, staff_id=staff_c.id))
    await db.commit()
    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "no_pair"


@pytest.mark.asyncio
async def test_sex_restriction_applies_to_both_staff(client, db) -> None:
    admin = await _admin(db, "2s-bulk-sex@example.com")
    office = await _office(db, "TS4")
    await _course(db, office, "A", sex="female")
    await _course(db, office, "B", sex="male")
    await _course(db, office, "C", sex="female")
    p = await _patient(db, office, "TS-SEX", sex_restriction="female_only")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert len(body["placements"]) == 1, body
    pl = body["placements"][0]
    assert {pl["course_code"], pl["partner_course_code"]} == {"A", "C"}
    assert "staff_sex_mismatch" not in pl["warnings"]


@pytest.mark.asyncio
async def test_sex_restriction_without_two_matching_staff_is_no_pair(client, db) -> None:
    admin = await _admin(db, "2s-bulk-sex2@example.com")
    office = await _office(db, "TS5")
    await _course(db, office, "A", sex="female")
    await _course(db, office, "B", sex="male")
    p = await _patient(db, office, "TS-SEX2", sex_restriction="female_only")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "no_pair"


@pytest.mark.asyncio
async def test_staff_off_duty_on_either_side_is_no_pair(client, db) -> None:
    """2 人目の担当がその曜日に勤務でない (シフトなし) → 組を作らない."""
    admin = await _admin(db, "2s-bulk-off@example.com")
    office = await _office(db, "TS6")
    await _course(db, office, "A")
    await _course(db, office, "B", shift_on=False)
    p = await _patient(db, office, "TS-OFF")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "no_pair"


@pytest.mark.asyncio
async def test_staff_event_on_either_side_is_no_pair(client, db) -> None:
    """2 人目の担当に午後のイベントがある → 午後希望の方は組を作れない (午後は A だけ空く)."""
    admin = await _admin(db, "2s-bulk-event@example.com")
    office = await _office(db, "TS11")
    await _course(db, office, "A")
    _, staff_b = await _course(db, office, "B")
    db.add(
        StaffEvent(
            staff_id=staff_b.id,
            event_type="meeting",
            starts_at=datetime.combine(WEEK_MONDAY, time(12, 0)),
            ends_at=datetime.combine(WEEK_MONDAY, time(19, 0)),
            title="研修",
            source="kaipoke",
            external_id=f"test:{staff_b.id}:pair-event",
            blocking=False,
        )
    )
    p = await _patient(db, office, "TS-EVENT", wp=_wp(time_type="午後"))
    # 対照: 1 名の午後希望の方は A に入る (イベントは B の人だけのもの).
    single = await _patient(db, office, "TS-EVENT1", multi=False, wp=_wp(time_type="午後"))
    await db.commit()

    body = await _simulate(client, admin, office, [p, single])
    assert [(pl["patient_id"], pl["course_code"]) for pl in body["placements"]] == [
        (str(single.id), "A")
    ], body
    assert _unplaced_reason(body, p) == "no_pair"


@pytest.mark.asyncio
async def test_ng_weekday_not_placed(client, db) -> None:
    admin = await _admin(db, "2s-bulk-ngwd@example.com")
    office = await _office(db, "TS7")
    await _course(db, office, "A")
    await _course(db, office, "B")
    p = await _patient(db, office, "TS-NGWD", wp=_wp(ng_weekdays=["Mon"]))
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "ng_weekday"


@pytest.mark.asyncio
async def test_biweekly_off_week_not_placed(client, db) -> None:
    """隔週のお休みの週 (起点の翌週) は 2 名の方も置かない."""
    admin = await _admin(db, "2s-bulk-offwk@example.com")
    office = await _office(db, "TS8")
    await _course(db, office, "A")
    await _course(db, office, "B")
    anchor = (WEEK_MONDAY - timedelta(weeks=1)).isoformat()
    p = await _patient(
        db,
        office,
        "TS-BIWK",
        wp=_wp(visit_frequency="biweekly", biweekly_anchor=anchor),
    )
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "off_week"


@pytest.mark.asyncio
async def test_no_second_course_stays_in_pool_with_no_pair(client, db) -> None:
    admin = await _admin(db, "2s-bulk-nopair@example.com")
    office = await _office(db, "TS9")
    await _course(db, office, "A")
    p = await _patient(db, office, "TS-NOPAIR")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "no_pair"


# ---------------------------------------------------------------------------
# apply の守り
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_rejects_two_staff_placement_without_partner(client, db) -> None:
    admin = await _admin(db, "2s-bulk-guard@example.com")
    office = await _office(db, "TS10")
    await _course(db, office, "A")
    await _course(db, office, "B")
    p = await _patient(db, office, "TS-GUARD")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    pl = dict(body["placements"][0])
    pl["partner_course_code"] = None
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "placements": [pl],
            "state_token": body["state_token"],
        },
    )
    assert res.status_code == 422, res.text
    assert "two_staff_partner_required" in res.text
    pfvs = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    assert pfvs == []


async def _apply(client, admin: User, office: Office, body: dict, placements: list[dict]):
    return await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "placements": placements,
            "state_token": body["state_token"],
        },
    )


async def _pfv_count(db, p: Patient) -> int:
    return len(
        (
            await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
        ).all()
    )


@pytest.mark.asyncio
async def test_same_staff_two_courses_not_used_as_pair(client, db) -> None:
    """同じ人が担当する 2 つのコースは組にしない (1 人で 2 人分にはならない)."""
    admin = await _admin(db, "2s-bulk-samestaff@example.com")
    office = await _office(db, "TS12")
    _, staff_a = await _course(db, office, "A")
    await _course(db, office, "B", staff=staff_a)
    p = await _patient(db, office, "TS-SAME")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert body["placements"] == []
    assert _unplaced_reason(body, p) == "no_pair"

    # 別の人の C があれば、A か B と C の組になる (A+B は選ばない).
    await _course(db, office, "C")
    await db.commit()
    body = await _simulate(client, admin, office, [p])
    assert len(body["placements"]) == 1, body
    pl = body["placements"][0]
    assert "C" in {pl["course_code"], pl["partner_course_code"]}


@pytest.mark.asyncio
async def test_apply_rejects_pair_when_courses_became_same_staff(client, db) -> None:
    """simulate の後に 2 人目のコースの担当が 1 人目と同じ人になった → 422 (何も書かない)."""
    admin = await _admin(db, "2s-bulk-samestaff2@example.com")
    office = await _office(db, "TS13")
    _, staff_a = await _course(db, office, "A")
    course_b, _ = await _course(db, office, "B")
    p = await _patient(db, office, "TS-SAME2")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    assert len(body["placements"]) == 1, body
    course_b.assigned_staff_id = staff_a.id
    await db.commit()

    res = await _apply(client, admin, office, body, body["placements"])
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "two_staff_same_staff"
    assert "担当が同じ人" in res.json()["detail"]["message"]
    assert await _pfv_count(db, p) == 0


@pytest.mark.asyncio
async def test_apply_rejects_same_course_partner(client, db) -> None:
    admin = await _admin(db, "2s-bulk-samecourse@example.com")
    office = await _office(db, "TS14")
    await _course(db, office, "A")
    await _course(db, office, "B")
    p = await _patient(db, office, "TS-SAMEC")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    pl = dict(body["placements"][0])
    pl["partner_course_code"] = pl["course_code"]
    res = await _apply(client, admin, office, body, [pl])
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "two_staff_same_course"
    assert await _pfv_count(db, p) == 0


@pytest.mark.asyncio
async def test_apply_rejects_missing_partner_course(client, db) -> None:
    admin = await _admin(db, "2s-bulk-missing@example.com")
    office = await _office(db, "TS15")
    await _course(db, office, "A")
    await _course(db, office, "B")
    p = await _patient(db, office, "TS-MISS")
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    pl = dict(body["placements"][0])
    pl["partner_course_code"] = "Z"
    res = await _apply(client, admin, office, body, [pl])
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "two_staff_course_missing"
    assert await _pfv_count(db, p) == 0


# ---------------------------------------------------------------------------
# 足りない回数の数え方 (2 名の方は曜日の数)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_when_only_one_weekday_has_pair(client, db) -> None:
    """週 2 回の方で、月曜だけ組ができる → 月曜の 1 件を置き、火曜は no_pair で足りない 1."""
    admin = await _admin(db, "2s-bulk-partial@example.com")
    office = await _office(db, "TS16")
    course_a, staff_a = await _course(db, office, "A")
    await _course(db, office, "B")
    # 火曜は A の人のコースだけ (2 人目がいない).
    db.add(StaffShift(staff_id=staff_a.id, weekday=1, is_on=True))
    await _course(db, office, "A", weekday=1, staff=staff_a, template_id=course_a.template_id)
    p = await _patient(
        db, office, "TS-PART", wp=_wp(preferred_weekdays=["Mon", "Tue"], frequency_per_week=2)
    )
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    got = [(pl["weekday"], pl["partner_course_code"] is not None) for pl in body["placements"]]
    assert got == [(0, True)], body
    partial = [x for x in body["partial"] if x["patient_id"] == str(p.id)]
    assert len(partial) == 1, body
    assert partial[0]["placed_days"] == 1
    assert partial[0]["missing_days"] == 1
    assert partial[0]["unplaced_reasons"] == {"1": "no_pair"}


@pytest.mark.asyncio
async def test_existing_pair_counts_as_one_visit_for_shortage(client, db) -> None:
    """週 2 回の方で月曜に 2 名の組 (2 行) が既にある → 足りないのは 1 回. 火曜に組を置く.

    2 行を 2 回と数えると「足りている」になり、火曜が置かれない.
    """
    admin = await _admin(db, "2s-bulk-existing@example.com")
    office = await _office(db, "TS17")
    course_a, staff_a = await _course(db, office, "A")
    course_b, staff_b = await _course(db, office, "B")
    for st in (staff_a, staff_b):
        db.add(StaffShift(staff_id=st.id, weekday=1, is_on=True))
    await _course(db, office, "A", weekday=1, staff=staff_a, template_id=course_a.template_id)
    await _course(db, office, "B", weekday=1, staff=staff_b, template_id=course_b.template_id)
    p = await _patient(
        db, office, "TS-EXIST", wp=_wp(preferred_weekdays=["Mon", "Tue"], frequency_per_week=2)
    )
    group = uuid4()
    for c, st in ((course_a, staff_a), (course_b, staff_b)):
        db.add(
            Visit(
                patient_id=p.id,
                visit_date=WEEK_MONDAY,
                start_time=time(11, 0),
                end_time=time(11, 30),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="auto",
                required_staff_count=2,
                visit_group_id=group,
                course_id=c.id,
                primary_staff_id=st.id,
            )
        )
    await db.commit()

    body = await _simulate(client, admin, office, [p])
    got = [(pl["weekday"], pl["partner_course_code"] is not None) for pl in body["placements"]]
    assert got == [(1, True)], body
    assert not [x for x in body["partial"] if x["patient_id"] == str(p.id)]


# ---------------------------------------------------------------------------
# 1 名の方: 模擬バケットのコピーで担当のイベントを落とさない (個別提案と同じ物差し)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_patient_avoids_course_whose_staff_has_event(client, db) -> None:
    """A の担当に午後のイベントがある → 午後希望の 1 名の方は B に入る.

    以前はコピーでイベントが落ち、並びの先の A に置かれていた (個別提案は B).
    """
    admin = await _admin(db, "2s-bulk-single-event@example.com")
    office = await _office(db, "TS18")
    _, staff_a = await _course(db, office, "A")
    await _course(db, office, "B")
    db.add(
        StaffEvent(
            staff_id=staff_a.id,
            event_type="meeting",
            starts_at=datetime.combine(WEEK_MONDAY, time(12, 0)),
            ends_at=datetime.combine(WEEK_MONDAY, time(19, 0)),
            title="研修",
            source="kaipoke",
            external_id=f"test:{staff_a.id}:single-event",
            blocking=False,
        )
    )
    single = await _patient(db, office, "TS-SEV", multi=False, wp=_wp(time_type="午後"))
    await db.commit()

    body = await _simulate(client, admin, office, [single])
    assert [(pl["patient_id"], pl["course_code"]) for pl in body["placements"]] == [
        (str(single.id), "B")
    ], body
