"""2026-10-13 (PO): 保留プールの選び方と G-21 Before の修正.

A. 今週の回数が実際の訪問で足りている患者はプールに入れない (サブ拠点経由の患者・
   固定訪問の表が無い患者も同じ). その日にもう訪問がある曜日には提案しない.
B. G-21 Before は取消・削除した訪問を 「入っている」 と見なさない.
C. G-21 Before は希望曜日を全部足さず、 希望回数に届くまでだけ足す.
D. 実際にある訪問は拠点の休業曜日でも Before に残し、 「休みの日」 の注意書きを
   出さない (新しい提案は休業曜日に置かない).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.models import Office, Patient
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_STATUS_CANCELLED, VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.auto_allocator_v2 import (
    G21_NEW_ALGORITHM_FEATURE_KEY,
    _load_before_visits_v2,
    run_v2_pipeline,
)

# 2026 W20: Mon=05-11 ... Sat=05-16
_W20 = {wd: date(2026, 5, 11 + wd) for wd in range(7)}


async def _office(db, *, name: str, staffed_weekdays: list[int], operating=None) -> Office:
    o = Office(name=name)
    if operating is not None:
        o.operating_weekdays = operating
    db.add(o)
    await db.flush()
    s = Staff(name=f"{name}-staff", role="staff", is_trainee=False, primary_office_id=o.id)
    db.add(s)
    await db.flush()
    for wd in staffed_weekdays:
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.flush()
    return o


async def _patient(db, *, code: str, office: Office, weekly_pattern: dict | None) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.flush()
    return p


def _visit(p: Patient, wd: int, hh: int, mm: int = 0, *, status: str = VISIT_STATUS_PLANNED):
    return Visit(
        patient_id=p.id,
        visit_date=_W20[wd],
        start_time=time(hh, mm),
        end_time=time(hh, mm + 30) if mm + 30 < 60 else time(hh + 1, mm - 30),
        type="regular",
        status=status,
        source="auto_alloc",
        required_staff_count=1,
    )


def _pfv(p: Patient, wd: int, hh: int, *, sub_office_id=None, is_pinned: bool = False):
    return PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=wd,
        start_time=time(hh, 0),
        duration_min=30,
        slot_index=0,
        sub_office_id=sub_office_id,
        is_pinned=is_pinned,
    )


async def _enable_g21(db, office: Office) -> None:
    db.add(
        OfficeFeatureFlag(
            office_id=office.id,
            feature_key=G21_NEW_ALGORITHM_FEATURE_KEY,
            enabled_at=datetime.now(tz=UTC),
        )
    )
    await db.flush()


async def _diff_add(db, office: Office) -> dict:
    return await run_v2_pipeline(
        db, iso_year=2026, iso_week=20, office_ids=[office.id], mode="diff_add"
    )


# ---------------------------------------------------------------------------
# A. 固定訪問の表が無い患者
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_pfv_patient_count_satisfied_not_in_pool(db) -> None:
    """週 1 回・希望木曜の方が今週木曜に訪問済み → プールに入らず提案も出ない."""
    office = await _office(db, name="a-nopfv-sat", staffed_weekdays=[0, 1, 2, 3, 4])
    p = await _patient(
        db,
        code="A-NOPFV-SAT",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Thu"],
            "preferred_start": "09:30",
            "time_type": "時間帯",
            "frequency_per_week": 1,
        },
    )
    db.add(_visit(p, 3, 14))
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert pv == [], f"回数が足りている固定なし患者は提案しない: {pv}"
    unassigned = [u for u in result["unassigned_patients"] if u.get("patient_id") == p.id]
    assert unassigned == [], f"回数が足りている患者は未配置にも出さない: {unassigned}"


@pytest.mark.asyncio
async def test_no_pfv_patient_count_short_skips_visited_weekday(db) -> None:
    """週 2 回・希望 月水 で月曜だけ訪問済み → 水曜だけ提案 (月曜は 2 回目なので出さない)."""
    office = await _office(db, name="a-nopfv-short", staffed_weekdays=[0, 2])
    p = await _patient(
        db,
        code="A-NOPFV-SHORT",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed"],
            "preferred_start": "10:30",
            "time_type": "固定",
            "frequency_per_week": 2,
        },
    )
    db.add(_visit(p, 0, 9))
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert {v.weekday for v in pv} == {2}, [v.weekday for v in pv]


@pytest.mark.asyncio
async def test_no_pfv_patient_two_visits_a_day_allowed_when_explicit(db) -> None:
    """希望が月曜に 2 回 (entries で 2 件) を明示 → 月曜 1 件訪問済みでも 2 回目を残す."""
    office = await _office(db, name="a-nopfv-twice", staffed_weekdays=[0])
    p = await _patient(
        db,
        code="A-NOPFV-TWICE",
        office=office,
        weekly_pattern={
            "entries": [
                {"weekday": "Mon", "preferred_start": "09:30", "time_type": "固定"},
                {"weekday": "Mon", "preferred_start": "15:00", "time_type": "固定"},
            ],
            "frequency_per_week": 2,
        },
    )
    db.add(_visit(p, 0, 9, 30))
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert pv, "1 日 2 回を明示した希望は、 1 回目が入っていても 2 回目を提案に残す"
    assert all(v.weekday == 0 for v in pv), [v.weekday for v in pv]


# ---------------------------------------------------------------------------
# A. サブ拠点経由の患者
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sub_office_patient_count_satisfied_not_in_pool(db) -> None:
    """主担当は別拠点・この拠点はサブ拠点. 週 2 回で今週 月水金 に訪問済み → 提案しない."""
    main = await _office(db, name="a-sub-main", staffed_weekdays=[0, 2, 4])
    sub = await _office(db, name="a-sub-sub", staffed_weekdays=[0, 2, 4])
    p = await _patient(
        db,
        code="A-SUB-SAT",
        office=main,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Fri"],
            "preferred_start": "09:30",
            "time_type": "固定",
            "frequency_per_week": 2,
        },
    )
    db.add(_pfv(p, 0, 9, sub_office_id=sub.id))
    db.add(_pfv(p, 4, 9, sub_office_id=sub.id))
    for wd in (0, 2, 4):
        db.add(_visit(p, wd, 9, 30))
    await db.commit()

    result = await _diff_add(db, sub)
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert pv == [], f"回数が足りているサブ拠点の患者は提案しない: {pv}"


@pytest.mark.asyncio
async def test_sub_office_patient_count_short_only_missing_weekday(db) -> None:
    """サブ拠点の患者で週 3 回・今週 月金 だけ訪問済み → 未訪問の水曜だけ提案."""
    main = await _office(db, name="a-sub2-main", staffed_weekdays=[0, 2, 4])
    sub = await _office(db, name="a-sub2-sub", staffed_weekdays=[0, 2, 4])
    p = await _patient(
        db,
        code="A-SUB-SHORT",
        office=main,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "09:30",
            "time_type": "固定",
            "frequency_per_week": 3,
        },
    )
    for wd in (0, 2, 4):
        db.add(_pfv(p, wd, 9, sub_office_id=sub.id))
    for wd in (0, 4):
        db.add(_visit(p, wd, 9, 30))
    await db.commit()

    result = await _diff_add(db, sub)
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert pv, "回数が足りないサブ拠点の患者は提案に残る"
    assert {v.weekday for v in pv} == {2}, [v.weekday for v in pv]


@pytest.mark.asyncio
async def test_cancelled_visit_does_not_count_toward_week(db) -> None:
    """取消した訪問は回数に数えない (固定なし・週 1 回・木曜が取消 → 木曜を提案)."""
    office = await _office(db, name="a-cancel", staffed_weekdays=[3])
    p = await _patient(
        db,
        code="A-CANCEL",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Thu"],
            "preferred_start": "09:30",
            "time_type": "固定",
            "frequency_per_week": 1,
        },
    )
    db.add(_visit(p, 3, 9, 30, status=VISIT_STATUS_CANCELLED))
    await db.commit()

    result = await _diff_add(db, office)
    pv = [v for v in result["pool_visits"] if v.patient_id == p.id]
    assert {v.weekday for v in pv} == {3}, [v.weekday for v in pv]


# ---------------------------------------------------------------------------
# B / C / D. G-21 Before
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g21_before_ignores_cancelled_and_deleted_visits(db) -> None:
    office = await _office(db, name="b-cancel", staffed_weekdays=[0, 1])
    p = await _patient(db, code="B-CANCEL", office=office, weekly_pattern=None)
    db.add(_visit(p, 0, 9, status=VISIT_STATUS_CANCELLED))
    deleted = _visit(p, 1, 9)
    deleted.deleted_at = datetime.now(tz=UTC)
    db.add(deleted)
    await db.commit()

    visits = await _load_before_visits_v2(db, patients_by_id={p.id: p}, iso_year=2026, iso_week=20)
    assert visits == [], f"取消・削除した訪問は Before に出さない: {visits}"


@pytest.mark.asyncio
async def test_g21_before_weekly_preferences_fill_only_up_to_frequency(db) -> None:
    """週 2 回・希望 月水金 で実際の訪問が 月水 → Before は 月水 の 2 件だけ (金は足さない)."""
    office = await _office(db, name="c-freq", staffed_weekdays=[0, 2, 4])
    p = await _patient(
        db,
        code="C-FREQ",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "09:30",
            "time_type": "固定",
            "frequency_per_week": 2,
        },
    )
    db.add(_visit(p, 0, 10))
    db.add(_visit(p, 2, 10))
    await db.commit()

    visits = await _load_before_visits_v2(db, patients_by_id={p.id: p}, iso_year=2026, iso_week=20)
    assert sorted(v.weekday for v in visits) == [0, 2], [v.weekday for v in visits]


@pytest.mark.asyncio
async def test_g21_before_weekly_preferences_fill_shortfall(db) -> None:
    """週 2 回・希望 月水金 で実際の訪問が 月 だけ → 希望から 1 件だけ足して 2 件."""
    office = await _office(db, name="c-fill", staffed_weekdays=[0, 2, 4])
    p = await _patient(
        db,
        code="C-FILL",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Wed", "Fri"],
            "preferred_start": "09:30",
            "time_type": "固定",
            "frequency_per_week": 2,
        },
    )
    db.add(_visit(p, 0, 10))
    await db.commit()

    visits = await _load_before_visits_v2(db, patients_by_id={p.id: p}, iso_year=2026, iso_week=20)
    assert len(visits) == 2, [v.weekday for v in visits]
    assert 0 in {v.weekday for v in visits}


@pytest.mark.asyncio
async def test_g21_before_keeps_actual_visit_on_closed_weekday(db) -> None:
    """拠点は月〜金. 土曜に実際の訪問がある → Before に残し、 休みの注意書きを出さない."""
    office = await _office(
        db, name="d-sat", staffed_weekdays=[0, 1, 2, 3, 4], operating=[0, 1, 2, 3, 4]
    )
    p = await _patient(db, code="D-SAT", office=office, weekly_pattern=None)
    db.add(_visit(p, 5, 15))
    await db.commit()

    warnings: list = []
    visits = await _load_before_visits_v2(
        db,
        patients_by_id={p.id: p},
        iso_year=2026,
        iso_week=20,
        warnings=warnings,
        op_weekdays_by_office={office.id: {0, 1, 2, 3, 4}},
    )
    assert [v.weekday for v in visits] == [5]
    assert [w for w in warnings if w.type == "office_closed"] == []


@pytest.mark.asyncio
async def test_legacy_before_keeps_pfv_on_closed_weekday_when_actual_visit_exists(db) -> None:
    """旧方式 (G-21 OFF): 土曜の固定枠と土曜の実際の訪問がある → Before に残し注意書きなし.

    新しい提案は休業曜日に置かない (= プール候補の土曜は出ない).
    """
    office = await _office(
        db, name="d-legacy-sat", staffed_weekdays=[0, 1, 2, 3, 4], operating=[0, 1, 2, 3, 4]
    )
    kept = await _patient(db, code="D-LEG-KEEP", office=office, weekly_pattern=None)
    db.add(_pfv(kept, 5, 15))
    db.add(_visit(kept, 5, 15))
    newp = await _patient(
        db,
        code="D-LEG-NEW",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Sat"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await db.commit()

    result = await _diff_add(db, office)
    before_sat = [v for v in result["before_visits"] if v.patient_id == kept.id]
    assert [v.weekday for v in before_sat] == [5], before_sat
    closed_for_kept = [
        w for w in result["warnings"] if w.type == "office_closed" and w.patient_id == kept.id
    ]
    assert closed_for_kept == [], closed_for_kept
    assert [v for v in result["pool_visits"] if v.patient_id == newp.id] == []


@pytest.mark.asyncio
async def test_g21_pipeline_keeps_actual_saturday_visit(db) -> None:
    """G-21 ON の拠点でも土曜の実際の訪問は Before に残り、 注意書きを出さない."""
    office = await _office(
        db, name="d-g21-sat", staffed_weekdays=[0, 1, 2, 3, 4], operating=[0, 1, 2, 3, 4]
    )
    await _enable_g21(db, office)
    p = await _patient(db, code="D-G21-SAT", office=office, weekly_pattern=None)
    db.add(_visit(p, 5, 15))
    await db.commit()

    result = await _diff_add(db, office)
    assert [v.weekday for v in result["before_visits"] if v.patient_id == p.id] == [5]
    assert [w for w in result["warnings"] if w.type == "office_closed"] == []
