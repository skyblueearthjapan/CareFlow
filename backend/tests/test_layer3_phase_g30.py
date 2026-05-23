"""Phase G-30: legacy 経路でも pinned PFV (is_pinned=True) の時刻不動を担保.

背景:
    Phase G-21 (canary deploy) は **shadow mode = office_feature_flags 空** で
    本番運用中. canary OFF (= legacy 経路) でも 「pinned なら時刻動かない」
    という不変条件は維持されるべき.

    既存実装:
        - ``apply_travel_corrections`` (auto_allocator_v2.py:3948-3968) には既に
          「V2Visit.is_pinned=True ならば snapshot から時刻復元」 ロジックが実装済.
        - Phase G-21 経路 (= build_visits_for_pool_v2 / _load_before_visits_v2)
          は ``is_pinned=True`` を立てている.
        - **legacy 経路 (= build_visits_for_pool / _load_before_visits_from_pfv)
          は V2Visit.is_pinned を常に False のままにしていた** ため、 周辺 visit
          の影響で pinned 時刻が動く可能性があった.

    Phase G-30 修正:
        - ``build_visits_for_pool`` (fixed-source 分岐) で
          PFV.is_pinned を V2Visit.is_pinned にコピー.
        - ``_load_before_visits_from_pfv`` で同上.

検証観点:
    1. legacy 経路 (= canary OFF) で pinned visit の時刻が apply_travel_corrections
       で動かない (= 核心).
    2. 同住所ペアでも pinned なら不動.
    3. 非 pinned visit は引き続き apply_travel_corrections で動く (regression).
    4. G-21 経路 (canary ON) の既存挙動が壊れない (regression).
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from app.models import Office, Patient
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.services.scheduling.auto_allocator_v2 import (
    G21_NEW_ALGORITHM_FEATURE_KEY,
    V2Visit,
    apply_travel_corrections,
    build_visits_for_pool,
    run_v2_pipeline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    lat: float = 35.65,
    lng: float = 140.10,
    weekly_pattern: dict | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name,
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
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
    is_pinned: bool = True,
    duration_min: int = 30,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=time(start_hhmm[0], start_hhmm[1]),
        duration_min=duration_min,
        slot_index=0,
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.flush()
    return pfv


async def _enable_g21_flag(db, office: Office) -> None:
    flag = OfficeFeatureFlag(
        office_id=office.id,
        feature_key=G21_NEW_ALGORITHM_FEATURE_KEY,
        enabled_at=datetime.now(tz=UTC),
    )
    db.add(flag)
    await db.flush()


async def _seed_staff_and_shift(db, *, office: Office, weekday: int, name: str) -> Staff:
    s = Staff(
        name=name,
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=weekday, is_on=True))
    await db.flush()
    return s


# ---------------------------------------------------------------------------
# 1) 核心テスト: legacy 経路で pinned visit の時刻が動かない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_path_pinned_visit_time_not_moved(db) -> None:
    """legacy 経路 (= G-21 feature flag OFF) で pinned PFV の start_time が
    apply_travel_corrections で動かない.

    シナリオ:
        - 同 office に 3 名: pinned (10:00), 非 pinned A (10:00 同時刻 / 異住所),
          非 pinned B (10:05 / 異住所). apply_travel_corrections の
          ``_auto_shift_same_time_conflicts`` が pinned とぶつかる非 pinned を
          shift する経路を作るが、 pinned 自身は不動.

    Phase G-30 修正前 (sabotage):
        is_pinned=False で V2Visit を作ると、 pinned 周辺の travel 補正で
        pinned 自身の start_time が 10:00 から動く可能性がある (= test fail).

    Phase G-30 修正後:
        legacy 経路でも is_pinned=True が V2Visit に乗り、
        apply_travel_corrections の post-restore で 10:00 に固定される.
    """
    office = await _make_office(db, name="g30-1-office")
    # canary OFF (= _enable_g21_flag を呼ばない)
    p_pinned = await _make_patient(
        db,
        code="G30-1-PIN",
        name="g30-1 pinned patient",
        office=office,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(
        db,
        patient=p_pinned,
        weekday=0,
        start_hhmm=(10, 0),
        is_pinned=True,
    )
    # 異住所同時刻の非 pinned 患者: apply_travel_corrections の
    # _auto_shift_same_time_conflicts で shift される候補.
    p_unp = await _make_patient(
        db,
        code="G30-1-UNP",
        name="g30-1 unpinned patient",
        office=office,
        lat=35.70,  # 異住所
        lng=140.15,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(
        db,
        patient=p_unp,
        weekday=0,
        start_hhmm=(10, 0),
        is_pinned=False,
    )
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g30-1-staff1")
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g30-1-staff2")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    pinned_after = [v for v in after_visits if v.patient_id == p_pinned.id and v.weekday == 0]
    assert len(pinned_after) == 1, (
        f"pinned visit が after_visits に 1 件期待だが {len(pinned_after)} 件: {pinned_after}"
    )
    assert pinned_after[0].is_pinned is True, (
        "Phase G-30: legacy 経路でも pinned PFV から作る V2Visit には is_pinned=True が乗るべき"
    )
    assert pinned_after[0].start_time == time(10, 0), (
        "Phase G-30: pinned PFV の start_time (10:00) が apply_travel_corrections で "
        f"動いた: actual={pinned_after[0].start_time}"
    )


# ---------------------------------------------------------------------------
# 2) 同住所ペアでも pinned なら不動
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_path_pinned_visit_pair_not_moved(db) -> None:
    """同住所 (= 同 lat/lng) の pinned PFV ペアは両方とも時刻不動.

    legacy 経路 + 同住所 2 名 pinned. _align_same_address_pair_to_same_time の
    対象になるが、 両方 pinned なので post-restore で start_time / end_time が
    PFV 値に戻る. (end_time はペア合算 60 分占有等で書き換わる可能性があるが、
    pinned の場合は snapshot 復元される.)
    """
    office = await _make_office(db, name="g30-2-office")
    p_a = await _make_patient(
        db,
        code="G30-2A",
        name="g30-2 patient A",
        office=office,
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(
        db,
        patient=p_a,
        weekday=0,
        start_hhmm=(10, 0),
        is_pinned=True,
    )
    p_b = await _make_patient(
        db,
        code="G30-2B",
        name="g30-2 patient B",
        office=office,
        lat=35.65,  # 同住所
        lng=140.10,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:30",
            "time_type": "固定",
        },
    )
    await _make_pfv(
        db,
        patient=p_b,
        weekday=0,
        start_hhmm=(10, 30),
        is_pinned=True,
    )
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g30-2-staff")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    a_after = [v for v in after_visits if v.patient_id == p_a.id and v.weekday == 0]
    b_after = [v for v in after_visits if v.patient_id == p_b.id and v.weekday == 0]
    assert len(a_after) == 1, f"A の after が 1 件期待だが {len(a_after)} 件"
    assert len(b_after) == 1, f"B の after が 1 件期待だが {len(b_after)} 件"
    assert a_after[0].is_pinned is True, "A: is_pinned=True が乗っていない (G-30)"
    assert b_after[0].is_pinned is True, "B: is_pinned=True が乗っていない (G-30)"
    assert a_after[0].start_time == time(10, 0), (
        f"A: PFV.start_time=10:00 から動いた: actual={a_after[0].start_time}"
    )
    assert b_after[0].start_time == time(10, 30), (
        f"B: PFV.start_time=10:30 から動いた: actual={b_after[0].start_time}"
    )


# ---------------------------------------------------------------------------
# 3) regression: 非 pinned visit は引き続き apply_travel_corrections で動く
# ---------------------------------------------------------------------------


def test_legacy_path_unpinned_visit_can_move() -> None:
    """is_pinned=False な visit は apply_travel_corrections で動ける (= 既存挙動).

    apply_travel_corrections を直接呼んで、 同 (office, weekday, course) 内の
    非 pinned visit が _auto_shift_same_time_conflicts で shift されることを
    確認する. これにより 「pinned だけ動かない / 非 pinned は動く」 の二分が
    成立していることを担保.
    """
    from uuid import uuid4

    office_id = uuid4()
    # 同 (office, weekday, course="A") に異住所同時刻 (10:00) の非 pinned 2 件.
    # _auto_shift_same_time_conflicts は後者 (B) を距離最適化のために shift.
    v_a = V2Visit(
        patient_id=uuid4(),
        patient_name="g30-3 A",
        patient_code="G30-3A",
        weekday=0,
        start_time=time(10, 0),
        end_time=time(10, 30),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office_id,
        am_pm="am",
        source_kind="fixed",
        course_code="A",
        time_type="固定",
        preferred_start="10:00",
        is_pinned=False,
    )
    v_b = V2Visit(
        patient_id=uuid4(),
        patient_name="g30-3 B",
        patient_code="G30-3B",
        weekday=0,
        start_time=time(10, 0),  # 同時刻 (異住所)
        end_time=time(10, 30),
        service_minutes=30,
        lat=35.80,  # 異住所
        lng=140.30,
        office_id=office_id,
        am_pm="am",
        source_kind="fixed",
        course_code="A",
        time_type="時間帯",
        preferred_start="09:00",
        preferred_end="17:00",
        is_pinned=False,
    )
    warnings: list = []
    apply_travel_corrections([v_a, v_b], warnings=warnings)
    # 非 pinned 同士: post-restore は無いので少なくとも片方の start_time が動く.
    # 「両方 10:00 のまま」 = 非 pinned 動かない = G-30 fence が誤って効いた
    # ことを意味するので fail.
    times = {v_a.start_time, v_b.start_time}
    assert times != {time(10, 0)}, (
        "regression: 非 pinned visit が apply_travel_corrections で動かなくなった "
        f"(G-30 fence が non-pinned に誤適用): a={v_a.start_time}, b={v_b.start_time}"
    )


# ---------------------------------------------------------------------------
# 4) regression: G-21 経路 (canary ON) でも既存挙動が壊れない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g21_path_still_works(db) -> None:
    """G-21 feature flag ON (= canary 拠点) でも pinned PFV の時刻は動かない.

    Phase G-21 経路は既に is_pinned=True を立てているため G-30 修正の前後で
    挙動不変. これを assertion 1 件で確認する regression test.
    """
    office = await _make_office(db, name="g30-4-office")
    await _enable_g21_flag(db, office)
    p = await _make_patient(
        db,
        code="G30-4",
        name="g30-4 patient",
        office=office,
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    await _make_pfv(db, patient=p, weekday=0, start_hhmm=(10, 0), is_pinned=True)
    await _seed_staff_and_shift(db, office=office, weekday=0, name="g30-4-staff")
    await db.commit()

    result = await run_v2_pipeline(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="full_optimize",
    )
    after_visits = result.get("after_visits") or []
    pinned_after = [v for v in after_visits if v.patient_id == p.id and v.weekday == 0]
    assert len(pinned_after) == 1, (
        f"G-21 ON: pinned visit が 1 件期待だが {len(pinned_after)} 件 (regression)"
    )
    assert pinned_after[0].is_pinned is True, (
        "G-21 ON: pinned が is_pinned=True で乗っていない (regression)"
    )
    assert pinned_after[0].start_time == time(10, 0), (
        f"G-21 ON: pinned.start_time が動いた (regression): {pinned_after[0].start_time}"
    )


# ---------------------------------------------------------------------------
# 5) unit: build_visits_for_pool 直接呼び出しで is_pinned が PFV から流れる
# ---------------------------------------------------------------------------


def test_build_visits_for_pool_propagates_pfv_is_pinned() -> None:
    """build_visits_for_pool (legacy) の fixed-source 分岐で PFV.is_pinned が
    V2Visit.is_pinned に正しくコピーされる (unit-level pinpoint test).

    is_pinned=True PFV → V2Visit.is_pinned=True
    is_pinned=False PFV → V2Visit.is_pinned=False
    weekly_pattern (PFV なし) → V2Visit.is_pinned=False (= 旧挙動維持)
    """
    from uuid import uuid4

    office_id = uuid4()
    patient = Patient(
        code="G30-5",
        name="g30-5 patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Mon", "Tue"],
            "preferred_start": "10:00",
            "time_type": "固定",
        },
    )
    patient.id = uuid4()

    pfv_pinned = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=True,
    )
    pfv_unpinned = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=1,
        start_time=time(11, 0),
        duration_min=30,
        slot_index=0,
        is_pinned=False,
    )

    visits = build_visits_for_pool(
        [patient],
        fixed_by_patient={patient.id: [pfv_pinned, pfv_unpinned]},
        use_fixed_as_source=True,
    )
    mon = [v for v in visits if v.weekday == 0]
    tue = [v for v in visits if v.weekday == 1]
    assert len(mon) == 1, f"Mon visit が 1 件期待だが {len(mon)} 件"
    assert len(tue) == 1, f"Tue visit が 1 件期待だが {len(tue)} 件"
    assert mon[0].is_pinned is True, (
        "Phase G-30: is_pinned=True PFV から作る V2Visit に is_pinned が伝播していない"
    )
    assert tue[0].is_pinned is False, (
        "Phase G-30: is_pinned=False PFV (= 「動かしてよい」 visit) が "
        "誤って is_pinned=True で乗っている"
    )

    # weekly_pattern 経路 (= PFV なし) は is_pinned=False のまま.
    patient_no_pfv = Patient(
        code="G30-5B",
        name="g30-5 weekly only",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        weekly_pattern={
            "preferred_weekdays": ["Wed"],
            "preferred_start": "14:00",
            "time_type": "時間帯",
            "preferred_end": "17:00",
        },
    )
    patient_no_pfv.id = uuid4()
    visits_wp = build_visits_for_pool([patient_no_pfv])
    wed = [v for v in visits_wp if v.weekday == 2]
    assert len(wed) >= 1, "weekly_pattern 経路で Wed visit が出ていない"
    assert all(v.is_pinned is False for v in wed), (
        "Phase G-30: weekly_pattern 経路の V2Visit は is_pinned=False のまま (= 既存挙動)"
    )
