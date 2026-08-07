"""PO 決定 (2026-08-07): 可動域=完全固定 の枠は自動割当も時刻を動かさない.

背景:
    ``PFV.movability`` は「提案を出してよいか」を制御するフラグとして導入され、
    改善提案 (``improvement_engine``) / 範囲最適化 (``scope_optimizer``) /
    詰まり解消 (``unblock_search``) / プール一括投入 (``pool_bulk_inserter``) の
    4 エンジンが ``movability == 'locked'`` を不可侵として尊重していた。

    ところが **自動割当本体 (``auto_allocator_v2``) は ``movability`` を一切
    参照していなかった**。凍結フェンス (``_apply_corrections_to_visits`` の
    snapshot / post-restore) が engage する条件は ``is_pinned`` のみだったため、

        「一括ピン解除して提案を出させる → その間に週生成 / 移動時間補正が走る」

    という実運用で、可動域=完全固定 の枠の実配置時刻が動いていた。

修正:
    - ``V2Visit.is_movability_locked`` を新設 (``is_pinned`` とは別フラグ)。
    - 凍結フェンスを ``is_pinned or is_movability_locked`` の和集合に拡張。
    - PFV 行から V2Visit を組む全経路で ``_pfv_movability_locked`` により populate。

検証観点:
    1. 凍結フェンスが ``is_movability_locked`` で engage する (核心 / 単体).
    2. フラグが無い visit は従来どおり補正で動く (= 1 が空振りでない証明 / 単体).
    3. legacy パイプラインが PFV.movability='locked' をフラグへ伝播する (結合).
    4. movability='unknown' の PFV はフラグが立たない (regression / 結合).
"""

from __future__ import annotations

import uuid
from datetime import time
from uuid import UUID

import pytest

from app.models import Office, Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    V2Visit,
    V2Warning,
    _add_minutes,
    _apply_corrections_to_visits,
    _pfv_movability_locked,
    run_v2_pipeline,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_v2(
    *,
    patient_id: UUID,
    name: str,
    lat: float,
    lng: float,
    start_h: int,
    start_m: int,
    office_id: UUID,
    duration_min: int = 30,
    is_pinned: bool = False,
    is_movability_locked: bool = False,
    course_code: str = "M",
) -> V2Visit:
    """test 用 V2Visit factory (test_g21_final_checks._make_v2 と同型)."""
    st = time(start_h, start_m)
    return V2Visit(
        patient_id=patient_id,
        patient_name=name,
        patient_code=None,
        weekday=0,
        start_time=st,
        end_time=_add_minutes(st, duration_min),
        service_minutes=duration_min,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
        course_code=course_code,
        time_type="固定",
        preferred_start=f"{start_h:02d}:{start_m:02d}",
        is_pinned=is_pinned,
        is_movability_locked=is_movability_locked,
    )


async def _make_office(db, *, name: str) -> Office:
    o = Office(name=name)
    db.add(o)
    await db.flush()
    return o


async def _make_patient(
    db,
    *,
    code: str,
    name: str,
    office: Office,
    lat: float,
    lng: float,
    preferred_start: str,
) -> Patient:
    p = Patient(
        code=code,
        name=name,
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": preferred_start,
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.flush()
    return p


async def _make_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start_hhmm: tuple[int, int],
    is_pinned: bool = False,
    movability: str = "unknown",
    duration_min: int = 30,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(*start_hhmm),
        duration_min=duration_min,
        slot_index=0,
        is_pinned=is_pinned,
        movability=movability,
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _seed_staff_and_shift(db, *, office: Office, weekday: int, name: str) -> Staff:
    s = Staff(name=name, role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=weekday, is_on=True))
    await db.flush()
    return s


# ---------------------------------------------------------------------------
# 0) 判定 helper 自体
# ---------------------------------------------------------------------------


def test_pfv_movability_locked_predicate() -> None:
    """``_pfv_movability_locked`` は 'locked' のみ True. 属性欠落は False."""

    class _Row:
        def __init__(self, movability: str | None) -> None:
            if movability is not None:
                self.movability = movability

    assert _pfv_movability_locked(_Row("locked")) is True
    assert _pfv_movability_locked(_Row("unknown")) is False
    assert _pfv_movability_locked(_Row("time_flexible")) is False
    assert _pfv_movability_locked(_Row("day_flexible")) is False
    # duck-typed な模擬 PFV が movability を持たない場合も落ちない.
    assert _pfv_movability_locked(_Row(None)) is False


# ---------------------------------------------------------------------------
# 1) 核心: 凍結フェンスが is_movability_locked で engage する
# ---------------------------------------------------------------------------


def test_movability_locked_visit_is_frozen_by_corrections() -> None:
    """``is_pinned=False`` でも ``is_movability_locked=True`` なら時刻不動.

    修正前は ``is_pinned`` のみを見ていたため、この visit は
    ``apply_travel_corrections`` の同時刻衝突 shift で動いていた。
    """
    office_id = uuid.uuid4()

    # 異住所 / 同時刻の 2 件 = _auto_shift_same_time_conflicts の対象.
    v_locked = _make_v2(
        patient_id=uuid.uuid4(),
        name="Locked",
        lat=35.65,
        lng=140.10,
        start_h=10,
        start_m=0,
        office_id=office_id,
        is_pinned=False,
        is_movability_locked=True,
    )
    v_other = _make_v2(
        patient_id=uuid.uuid4(),
        name="Other",
        lat=35.70,
        lng=140.15,
        start_h=10,
        start_m=0,
        office_id=office_id,
    )

    start_before = v_locked.start_time
    end_before = v_locked.end_time
    course_before = v_locked.course_code

    warnings: list[V2Warning] = []
    _apply_corrections_to_visits([v_locked, v_other], warnings=warnings)

    assert v_locked.start_time == start_before, (
        "可動域=完全固定 の visit の start_time が補正で動いた: "
        f"{start_before} -> {v_locked.start_time}"
    )
    assert v_locked.end_time == end_before
    assert v_locked.course_code == course_before
    # is_pinned は立てていない (フラグ分離の確認).
    assert v_locked.is_pinned is False
    assert v_locked.is_movability_locked is True


def test_unlocked_visit_still_moves_under_same_scenario() -> None:
    """対照実験: 同じ配置でフラグを外すと補正で動く.

    これが動かないと上のテストは「そもそも補正が働いていない」空振りになる。
    """
    office_id = uuid.uuid4()
    v_a = _make_v2(
        patient_id=uuid.uuid4(),
        name="A",
        lat=35.65,
        lng=140.10,
        start_h=10,
        start_m=0,
        office_id=office_id,
    )
    v_b = _make_v2(
        patient_id=uuid.uuid4(),
        name="B",
        lat=35.70,
        lng=140.15,
        start_h=10,
        start_m=0,
        office_id=office_id,
    )
    before = (v_a.start_time, v_b.start_time)

    warnings: list[V2Warning] = []
    _apply_corrections_to_visits([v_a, v_b], warnings=warnings)

    after = (v_a.start_time, v_b.start_time)
    assert before != after, (
        "対照実験が成立していない: 異住所同時刻 2 件が補正で 1 件も動かなかった "
        f"(before={before} after={after}). シナリオを見直すこと."
    )


# ---------------------------------------------------------------------------
# 2) 結合: legacy パイプラインが PFV.movability を V2Visit へ伝播する
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_pipeline_propagates_movability_locked(db) -> None:
    """PFV.movability='locked' (非 pinned) が V2Visit.is_movability_locked に乗り、
    その visit の start_time が PFV 値のまま保たれる (legacy 経路 = canary OFF)."""
    office = await _make_office(db, name="mvl-1-office")
    p_locked = await _make_patient(
        db,
        code="MVL-1-LOCK",
        name="mvl-1 locked patient",
        office=office,
        lat=35.65,
        lng=140.10,
        preferred_start="10:00",
    )
    await _make_pfv(
        db,
        patient=p_locked,
        weekday=0,
        start_hhmm=(10, 0),
        is_pinned=False,
        movability="locked",
    )
    # 異住所同時刻の可動な相手 (補正が実際に働く状況を作る).
    p_free = await _make_patient(
        db,
        code="MVL-1-FREE",
        name="mvl-1 free patient",
        office=office,
        lat=35.70,
        lng=140.15,
        preferred_start="10:00",
    )
    await _make_pfv(
        db,
        patient=p_free,
        weekday=0,
        start_hhmm=(10, 0),
        is_pinned=False,
        movability="unknown",
    )
    await _seed_staff_and_shift(db, office=office, weekday=0, name="mvl-1-staff1")
    await _seed_staff_and_shift(db, office=office, weekday=0, name="mvl-1-staff2")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    locked_after = [v for v in after_visits if v.patient_id == p_locked.id and v.weekday == 0]
    assert len(locked_after) == 1, (
        f"locked visit が after_visits に 1 件期待だが {len(locked_after)} 件: {locked_after}"
    )
    assert locked_after[0].is_movability_locked is True, (
        "PFV.movability='locked' が V2Visit.is_movability_locked に伝播していない"
    )
    assert locked_after[0].is_pinned is False, (
        "可動域ロックは is_pinned を立てない (フラグ分離が壊れている)"
    )
    assert locked_after[0].start_time == time(10, 0), (
        "可動域=完全固定 の PFV の start_time (10:00) が補正で動いた: "
        f"actual={locked_after[0].start_time}"
    )


@pytest.mark.asyncio
async def test_unknown_movability_does_not_set_flag(db) -> None:
    """regression: movability='unknown' の PFV はフラグが立たない (従来挙動)."""
    office = await _make_office(db, name="mvl-2-office")
    p = await _make_patient(
        db,
        code="MVL-2",
        name="mvl-2 patient",
        office=office,
        lat=35.65,
        lng=140.10,
        preferred_start="10:00",
    )
    await _make_pfv(
        db,
        patient=p,
        weekday=0,
        start_hhmm=(10, 0),
        is_pinned=False,
        movability="unknown",
    )
    await _seed_staff_and_shift(db, office=office, weekday=0, name="mvl-2-staff")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    rows = [v for v in after_visits if v.patient_id == p.id and v.weekday == 0]
    assert len(rows) == 1
    assert rows[0].is_movability_locked is False
    assert rows[0].is_pinned is False
