"""2026-10-13 (PO 一本化): 今週の姿 (Before) を全拠点で G-21 の作り方にしたあとの
「提案がコース未定 (M) になる」 の根治と、 職員の時刻の重なり・注意書きの重複.

1. 実際の訪問から作る今週の姿にコース (Course.code / Course.office_id) を残す.
   旧実装はコースを捨てていたので Stage 5 が出勤人数ぶんの A〜 しか発番できず、
   あふれた組 (= プールの提案を含む) がコース未定になっていた.
2. 入らない提案 (コースあふれ・移動時間不足など) は pool_visits に残さず未割当に回す
   (画面で 「M」 と出ていたもの).
3. 提案先コースの担当職員が同じ時刻に別の訪問 (他拠点のコースを含む) を持つ提案は出さない.
4. 「休みの日」 の注意書きは同じ患者・曜日で 1 回だけ.
5. 固定訪問の表が無いプールの患者の希望を、 今週の姿から自分自身とぶつけて消さない.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from app.models import Office, Patient
from app.models.course import Course
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.auto_allocator_v2 import (
    V2Warning,
    _emit_office_closed_warning,
    _load_before_visits_v2,
    run_v2_pipeline,
)

# 2026 W20: Mon=05-11
_MON = date(2026, 5, 11)


async def _office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.flush()
    return o


async def _staff(db, *, office: Office, name: str, weekdays: list[int]) -> Staff:
    s = Staff(name=name, role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    for wd in weekdays:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return s


async def _patient(
    db,
    *,
    code: str,
    office: Office,
    lat: float = 35.65,
    lng: float = 140.10,
    weekly_pattern: dict | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.flush()
    return p


async def _course(db, *, office: Office, code: str, staff: Staff | None = None) -> Course:
    c = Course(
        iso_year=2026,
        iso_week=20,
        weekday=0,
        code=code,
        office_id=office.id,
        assigned_staff_id=staff.id if staff is not None else None,
    )
    db.add(c)
    await db.flush()
    return c


def _visit(p: Patient, c: Course | None, start: time, end: time, *, staff: Staff | None = None):
    return Visit(
        patient_id=p.id,
        visit_date=_MON,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
        course_id=c.id if c is not None else None,
        primary_staff_id=staff.id if staff is not None else None,
    )


async def _diff_add(db, office: Office) -> dict:
    return await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )


# ---------------------------------------------------------------------------
# 1. 今週の姿にコースが残る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_v2_actual_visit_keeps_course_code_and_course_office(db) -> None:
    """実際の訪問が入っているコースの code と拠点が今週の姿に残る (他拠点のコースも)."""
    home = await _office(db, name="cc-home")
    other = await _office(db, name="cc-other")
    p = await _patient(db, code="CC-1", office=home)
    q = await _patient(db, code="CC-2", office=home)
    c_home = await _course(db, office=home, code="C")
    c_other = await _course(db, office=other, code="B")
    db.add(_visit(p, c_home, time(9, 0), time(9, 30)))
    db.add(
        Visit(
            patient_id=q.id,
            visit_date=date(2026, 5, 12),  # Tue
            start_time=time(10, 0),
            end_time=time(10, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc",
            required_staff_count=1,
            course_id=c_other.id,
        )
    )
    await db.commit()

    visits = await _load_before_visits_v2(
        db, patients_by_id={p.id: p, q.id: q}, iso_year=2026, iso_week=20
    )
    by_pid = {v.patient_id: v for v in visits}
    assert by_pid[p.id].course_code == "C"
    assert by_pid[p.id].office_id == home.id
    # 他拠点のコースに入っている訪問は、 そのコースの拠点で数える (G-24 と同じ規約).
    assert by_pid[q.id].course_code == "B"
    assert by_pid[q.id].office_id == other.id


@pytest.mark.asyncio
async def test_diff_add_pool_proposal_joins_real_course_instead_of_m(db) -> None:
    """出勤 1 名の曜日に実際のコースが 2 つ (A と B・離れた地域) ある週.

    旧実装は今週の姿のコースを捨てていたので、 2 つ目の組は出勤人数を超えて
    未割当 → その組に入ったプールの提案はコース未定 (画面の 「M」) だった.
    今は実際のコース B の組に入り、 提案のコースは B になる.
    """
    office = await _office(db, name="cc-m-root")
    s1 = await _staff(db, office=office, name="cc-m-staff-a", weekdays=[0])
    s2 = await _staff(db, office=office, name="cc-m-staff-b", weekdays=[])
    c_a = await _course(db, office=office, code="A", staff=s1)
    c_b = await _course(db, office=office, code="B", staff=s2)
    # A の地域 (35.65, 140.10) と B の地域 (35.80, 140.30) は離れている.
    pa = await _patient(db, code="CC-A1", office=office, lat=35.65, lng=140.10)
    pb = await _patient(db, code="CC-B1", office=office, lat=35.80, lng=140.30)
    db.add(_visit(pa, c_a, time(9, 0), time(9, 30)))
    db.add(_visit(pb, c_b, time(9, 0), time(9, 30)))
    # プールの患者: B の地域の近く、 月曜 10:00〜12:00 の時間帯.
    pool = await _patient(
        db,
        code="CC-POOL",
        office=office,
        lat=35.801,
        lng=140.301,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "preferred_end": "12:00",
            "time_type": "時間帯",
        },
    )
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == pool.id]
    assert len(pv) == 1, (pv, [w.message for w in result["warnings"]])
    assert pv[0].course_code == "B", pv[0]


# ---------------------------------------------------------------------------
# 2. 入らない提案は 「M」 として出さず未割当に回す
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_add_unplaceable_pool_visit_is_unassigned_not_m(db) -> None:
    """移動時間が足りず入らない固定時刻の提案は pool_visits に出さず、 未割当 (理由付き).

    旧実装は course_code=None のまま pool_visits に残し、 endpoint が 「M」 の提案と
    して出していた (実際には入らない).
    """
    office = await _office(db, name="cc-unplaceable")
    s1 = await _staff(db, office=office, name="cc-unplaceable-staff", weekdays=[0])
    c_a = await _course(db, office=office, code="A", staff=s1)
    pa = await _patient(db, code="CC-U1", office=office, lat=35.65, lng=140.10)
    db.add(_visit(pa, c_a, time(10, 0), time(10, 30)))
    pool = await _patient(
        db,
        code="CC-U-POOL",
        office=office,
        lat=35.66,
        lng=140.11,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    await db.commit()

    result = await _diff_add(db, office)
    assert [v for v in result["pool_visits"] if v.patient_id == pool.id] == []
    assert all(v.course_code is not None for v in result["pool_visits"])
    unassigned = {u["patient_id"]: u for u in result["unassigned_patients"]}
    assert pool.id in unassigned
    assert unassigned[pool.id]["reason"] not in (None, "unknown"), unassigned[pool.id]


# ---------------------------------------------------------------------------
# 3. 職員の時刻の重なり
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_add_drops_proposal_when_course_staff_busy_in_other_office(db) -> None:
    """提案先コースの担当職員が、 同じ時刻に他拠点のコースで訪問している → 提案しない."""
    office = await _office(db, name="cc-staff-home")
    other = await _office(db, name="cc-staff-other")
    s1 = await _staff(db, office=office, name="cc-staff-shared", weekdays=[0])
    c_a = await _course(db, office=office, code="A", staff=s1)
    c_other = await _course(db, office=other, code="A", staff=s1)
    pa = await _patient(db, code="CC-S1", office=office)
    db.add(_visit(pa, c_a, time(9, 0), time(9, 30), staff=s1))
    # 同じ職員が 14:00-14:35 に他拠点のコースで訪問している.
    po = await _patient(db, code="CC-S-OTHER", office=other, lat=35.70, lng=140.20)
    db.add(_visit(po, c_other, time(14, 0), time(14, 35), staff=s1))
    pool = await _patient(
        db,
        code="CC-S-POOL",
        office=office,
        lat=35.651,
        lng=140.101,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    await db.commit()

    result = await _diff_add(db, office)
    assert [v for v in result["pool_visits"] if v.patient_id == pool.id] == []
    msgs = [
        w.message
        for w in result["warnings"]
        if w.type == "diff_add_conflict" and w.patient_id == pool.id
    ]
    assert any("担当職員が同じ時刻に別の訪問" in m for m in msgs), msgs
    assert pool.id in {u["patient_id"] for u in result["unassigned_patients"]}


@pytest.mark.asyncio
async def test_diff_add_keeps_proposal_when_course_staff_free(db) -> None:
    """担当職員の訪問と時刻が重ならなければ提案は残る (回帰)."""
    office = await _office(db, name="cc-staff-free")
    s1 = await _staff(db, office=office, name="cc-staff-free-1", weekdays=[0])
    c_a = await _course(db, office=office, code="A", staff=s1)
    pa = await _patient(db, code="CC-F1", office=office)
    db.add(_visit(pa, c_a, time(9, 0), time(9, 30), staff=s1))
    pool = await _patient(
        db,
        code="CC-F-POOL",
        office=office,
        lat=35.651,
        lng=140.101,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "14:00",
            "time_type": "固定",
        },
    )
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == pool.id]
    assert len(pv) == 1
    assert pv[0].course_code == "A"


# ---------------------------------------------------------------------------
# 4. 「休みの日」 の注意書きは 1 回だけ
# ---------------------------------------------------------------------------


def test_office_closed_warning_not_duplicated_across_loaders() -> None:
    """Before ローダと pool 展開は別々の closed_warned を持つ. 同じ患者・曜日の
    注意書きは warnings に 1 件だけ."""
    import uuid

    warnings: list[V2Warning] = []
    pid = uuid.uuid4()
    oid = uuid.uuid4()
    for _ in range(2):
        _emit_office_closed_warning(
            warnings=warnings,
            closed_warned=set(),  # ローダごとに別の set
            patient_id=pid,
            patient_name="x",
            weekday=5,
            office_id=oid,
            office_name_by_id={oid: "拠点X"},
        )
    assert len([w for w in warnings if w.type == "office_closed"]) == 1


# ---------------------------------------------------------------------------
# 5. 固定訪問の表が無いプールの患者の希望で、 自分の提案を消さない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_add_no_pfv_pool_patient_not_blocked_by_own_preferred_before(db) -> None:
    """今週の姿は希望からも作るので、 プールの患者の希望が今週の姿に入る. それを
    after に残すと自分の提案とぶつかって提案が消え、 しかも配置済みに見えていた."""
    office = await _office(db, name="cc-own-pref")
    await _staff(db, office=office, name="cc-own-pref-staff", weekdays=[0])
    pool = await _patient(
        db,
        code="CC-OWN",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == pool.id]
    assert len(pv) == 1, [w.message for w in result["warnings"]]
    assert result["proposal_meta_by_patient"][pool.id]["proposal_source"] == "preferred"
    # 自分自身との衝突の注意書きは出ない.
    assert not [
        w for w in result["warnings"] if w.type == "diff_add_conflict" and w.patient_id == pool.id
    ]
