"""Phase G-88 Step3: 最適化パイプラインへの SchedulingConfig 注入の単体テスト.

検証方針:
    - **既定 (config=None / DEFAULT_SCHEDULING_CONFIG) では現行 module 定数と同値**
      → 既存テスト (test_auto_allocator_v2 / test_proposal_solver /
      test_propose_slots_api) が回帰ゼロで pass することで担保 (本ファイルでも
      既定一致を spot-check する).
    - **非既定 config を渡すと、その値が最適化に効く** ことを propose 経路
      (proposal_solver) と full-optimize 経路 (auto_allocator_v2) の双方で確認:
        * buffer 8→20: 移動可否 (earliest_start) が変わる
        * speed 20→40: 移動時間が半減する
        * capacity 6→4: 1 コース上限が下がる
        * business_end 18:00→16:00: 遅い枠が出ない
        * lunch_duration 60→90: 昼休みが伸びる

純ロジック層のみ (DB 非依存). 座標は test_proposal_solver と同じ千葉市近辺.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from uuid import uuid4

from app.models import Patient
from app.services.scheduling.auto_allocator_v2 import (
    DEFAULT_SCHEDULING_CONFIG,
    V2Visit,
    V2Warning,
    _align_same_address_pair_to_same_time,
    _apply_corrections_to_visits,
    _auto_shift_same_time_conflicts,
    _extract_weekly_entries,
    _filter_unavailable_and_lunch,
    apply_travel_corrections,
    calc_course_total_minutes,
    calc_h_violations,
    compute_lunch_window,
    haversine_km,
    haversine_minutes,
)
from app.services.scheduling.config import SchedulingConfig
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    compute_earliest_start_after,
    find_available_slots_for_candidate,
    slot_feasible,
)
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    _CourseBucket,
    compute_all_proposed_slots,
)

BASE = (35.6000, 140.1000)
P_1_4KM = (35.6100, 140.1100)  # ~1.433km / 20km/h で 4 分
P_2_7KM = (35.6000, 140.1300)  # ~2.712km / 20km/h で 8 分


def _cfg(**overrides: object) -> SchedulingConfig:
    """DEFAULT_SCHEDULING_CONFIG の一部だけ差し替えた config を作る."""
    return replace(DEFAULT_SCHEDULING_CONFIG, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 既定一致 (config=None と DEFAULT_SCHEDULING_CONFIG が同値・module 定数と一致)
# ---------------------------------------------------------------------------


def test_default_config_matches_module_constants() -> None:
    assert DEFAULT_SCHEDULING_CONFIG.visit_buffer_min == 8
    assert DEFAULT_SCHEDULING_CONFIG.travel_speed_kmh == 20.0
    assert DEFAULT_SCHEDULING_CONFIG.lunch_duration_min == 60
    assert DEFAULT_SCHEDULING_CONFIG.lunch_window_start == time(11, 30)
    assert DEFAULT_SCHEDULING_CONFIG.lunch_window_end == time(13, 30)
    assert DEFAULT_SCHEDULING_CONFIG.business_start == time(9, 30)
    assert DEFAULT_SCHEDULING_CONFIG.business_end == time(18, 0)
    assert DEFAULT_SCHEDULING_CONFIG.max_patients_per_course == 6


def test_haversine_minutes_default_speed_unchanged() -> None:
    km = haversine_km(*BASE, *P_2_7KM)
    # 既定速度 (20km/h) は引数なし呼出と一致.
    assert haversine_minutes(km) == haversine_minutes(km, speed_kmh=20.0)


def test_earliest_start_default_config_equals_no_config() -> None:
    no_cfg = compute_earliest_start_after(time(10, 0), BASE, P_1_4KM, same_address=False)
    with_default = compute_earliest_start_after(
        time(10, 0), BASE, P_1_4KM, same_address=False, config=DEFAULT_SCHEDULING_CONFIG
    )
    assert no_cfg == with_default == time(10, 15)


# ---------------------------------------------------------------------------
# ② 移動速度: speed 20→40 で移動時間が半減する
# ---------------------------------------------------------------------------


def test_speed_doubled_halves_travel_minutes() -> None:
    km = haversine_km(*BASE, *P_2_7KM)
    base = haversine_minutes(km, speed_kmh=20.0)  # 8 分
    fast = haversine_minutes(km, speed_kmh=40.0)  # 速度倍 → 約 4 分
    assert base == 8
    assert fast == 4


def test_speed_config_propagates_to_earliest_start() -> None:
    # 異住所 8 分移動 + 8 buffer = 16 分 → 10:16 → 5 分切上げ 10:20 (既定).
    default = compute_earliest_start_after(
        time(10, 0), BASE, P_2_7KM, same_address=False, config=_cfg(travel_speed_kmh=20.0)
    )
    assert default == time(10, 20)
    # 速度倍 (40km/h): 4 分移動 + 8 buffer = 12 分 → 10:12 → 5 分切上げ 10:15.
    fast = compute_earliest_start_after(
        time(10, 0), BASE, P_2_7KM, same_address=False, config=_cfg(travel_speed_kmh=40.0)
    )
    assert fast == time(10, 15)


# ---------------------------------------------------------------------------
# ① バッファー: buffer 8→20 で移動可否 (earliest_start) が変わる
# ---------------------------------------------------------------------------


def test_buffer_config_propagates_to_earliest_start() -> None:
    # 既定 buffer 8: 8 分移動 + 8 = 16 分 → 10:20.
    default = compute_earliest_start_after(
        time(10, 0), BASE, P_2_7KM, same_address=False, config=_cfg(visit_buffer_min=8)
    )
    assert default == time(10, 20)
    # buffer 20: 8 分移動 + 20 = 28 分 → 10:28 → 5 分切上げ 10:30.
    wide = compute_earliest_start_after(
        time(10, 0), BASE, P_2_7KM, same_address=False, config=_cfg(visit_buffer_min=20)
    )
    assert wide == time(10, 30)


def test_buffer_config_changes_slot_feasibility() -> None:
    # 既存 BASE 09:30-10:00. 候補 P_2_7KM 60 分 (終日).
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*P_2_7KM, service_minutes=60, time_type="終日", patient_id="C")

    default_slots = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, config=_cfg(visit_buffer_min=8)
    )
    wide_slots = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, config=_cfg(visit_buffer_min=20)
    )
    # 既定 buffer 8: 10:00 + 8travel + 8buf = 10:16 → 10:20 開始.
    after_default = next(
        s
        for s in default_slots
        if s.block == "am" and not s.same_address_pair and s.start >= time(10, 0)
    )
    assert after_default.start == time(10, 20)
    # buffer 20: 10:00 + 8travel + 20buf = 10:28 → 10:30 開始 (= 後ろにずれる).
    after_wide = next(
        s
        for s in wide_slots
        if s.block == "am" and not s.same_address_pair and s.start >= time(10, 0)
    )
    assert after_wide.start == time(10, 30)


# ---------------------------------------------------------------------------
# ⑤ 定員: capacity 6→4 で 1 コース上限が下がる
# ---------------------------------------------------------------------------


def test_capacity_config_lowers_course_limit() -> None:
    # 既存 4 名 (連続枠) を持つコースに 5 人目候補を入れようとする.
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
        ExistingVisit(time(10, 30), time(11, 0), *BASE, service_minutes=30, patient_id="B"),
        ExistingVisit(time(14, 0), time(14, 30), *BASE, service_minutes=30, patient_id="D"),
        ExistingVisit(time(15, 0), time(15, 30), *BASE, service_minutes=30, patient_id="E"),
    ]
    cand = Candidate(*BASE, service_minutes=30, time_type="終日", patient_id="X")

    # capacity 6 (既定): 既存 4 < 6 → 候補を入れられる枠が出る (同住所枠含む).
    slots6 = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, config=_cfg(max_patients_per_course=6)
    )
    assert slots6, "capacity 6 では 5 人目を入れる枠が存在する"

    # capacity 4: 既存 4 >= 4 → 容量超過で枠ゼロ.
    slots4 = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, config=_cfg(max_patients_per_course=4)
    )
    assert slots4 == [], "capacity 4 では既存 4 名で上限に達し枠が出ない"


# ---------------------------------------------------------------------------
# ④ 営業時間: business_end 18:00→16:00 で遅い枠が出ない
# ---------------------------------------------------------------------------


def test_business_end_config_blocks_late_slots() -> None:
    cand = Candidate(*BASE, service_minutes=60, time_type="終日", patient_id="C")
    # 17:00 開始 (= 17:00-18:00) は既定 18:00 営業内なら合法、16:00 終了なら不可.
    assert slot_feasible(
        time(17, 0), cand, lunch_window=None, block="pm", config=_cfg(business_end=time(18, 0))
    )
    assert not slot_feasible(
        time(17, 0), cand, lunch_window=None, block="pm", config=_cfg(business_end=time(16, 0))
    )


def test_business_end_config_no_late_slots_in_solver() -> None:
    # PM 枠最後に 15:30-16:00 の既存 visit がある状態で 60 分候補.
    existing = [
        ExistingVisit(time(15, 30), time(16, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*BASE, service_minutes=60, time_type="終日", patient_id="C")

    # business_end 18:00 (既定): 16:00 以降に 16:00+travel/buffer から枠が出る.
    slots_default = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, config=_cfg(business_end=time(18, 0))
    )
    late_default = [s for s in slots_default if s.block == "pm" and s.start >= time(16, 0)]
    assert late_default, "business_end 18:00 では 16:00 以降の枠が出る"

    # business_end 16:00: 16:00 以降の 60 分枠は営業外 → 出ない.
    slots_early = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, config=_cfg(business_end=time(16, 0))
    )
    late_early = [s for s in slots_early if s.start >= time(16, 0)]
    assert late_early == [], "business_end 16:00 では 16:00 以降に枠が出ない"


# ---------------------------------------------------------------------------
# ③a 昼休み: lunch_duration 60→90 で昼休みが伸びる
# ---------------------------------------------------------------------------


def _v2(
    start: time,
    end: time,
    *,
    lat: float = BASE[0],
    lng: float = BASE[1],
    service: int = 30,
) -> V2Visit:
    return V2Visit(
        patient_id=uuid4(),
        patient_name="t",
        patient_code=None,
        weekday=0,
        start_time=start,
        end_time=end,
        service_minutes=service,
        lat=lat,
        lng=lng,
        office_id=uuid4(),
        am_pm="any",
        source_kind="fixed",
    )


def test_lunch_duration_default_60() -> None:
    # 午前 / 午後に十分な空きがある → 既定で 60 分 (例 11:30-12:30 など) が取れる.
    visits = [_v2(time(9, 30), time(10, 0)), _v2(time(15, 0), time(15, 30))]
    lunch = compute_lunch_window(visits)
    assert lunch is not None
    dur = (lunch[1].hour * 60 + lunch[1].minute) - (lunch[0].hour * 60 + lunch[0].minute)
    assert dur == 60


def test_lunch_duration_config_90_extends_lunch() -> None:
    # 同じ空き状況で標準長 90 分を指定 → 90 分の昼休みが取れる.
    # 90 分が 11:30-13:30 (= window 全体) に収まる唯一解は 11:30-13:00 or 12:00-13:30.
    visits = [_v2(time(9, 30), time(10, 0)), _v2(time(15, 0), time(15, 30))]
    lunch90 = compute_lunch_window(visits, duration=90)
    assert lunch90 is not None
    dur = (lunch90[1].hour * 60 + lunch90[1].minute) - (lunch90[0].hour * 60 + lunch90[0].minute)
    assert dur == 90
    # window (11:30-13:30) 内に収まる.
    assert lunch90[0] >= time(11, 30)
    assert lunch90[1] <= time(13, 30)


def test_lunch_window_config_shifts_window() -> None:
    # window を 12:00-14:00 にずらすと、昼休みもその範囲で取られる.
    visits = [_v2(time(9, 30), time(10, 0)), _v2(time(15, 0), time(15, 30))]
    lunch = compute_lunch_window(
        visits, duration=60, window_start=time(12, 0), window_end=time(14, 0)
    )
    assert lunch is not None
    assert lunch[0] >= time(12, 0)
    assert lunch[1] <= time(14, 0)


def test_lunch_default_args_equal_no_args() -> None:
    visits = [_v2(time(9, 30), time(10, 0)), _v2(time(15, 0), time(15, 30))]
    no_args = compute_lunch_window(visits)
    explicit_default = compute_lunch_window(
        visits, duration=60, window_start=time(11, 30), window_end=time(13, 30)
    )
    assert no_args == explicit_default


# ---------------------------------------------------------------------------
# full-optimize 経路: calc_course_total_minutes / apply_travel_corrections に効く
# ---------------------------------------------------------------------------


def test_calc_course_total_minutes_buffer_and_speed_config() -> None:
    # 2 件異住所 (BASE → P_2_7KM). service 30+60 = 90.
    a = _v2(time(9, 30), time(10, 0), service=30)
    b = _v2(time(11, 0), time(12, 0), lat=P_2_7KM[0], lng=P_2_7KM[1], service=60)

    # 既定 (buffer 8 / speed 20): travel 8 + buffer 8 = 16. total = 90 + 16 = 106.
    total_default = calc_course_total_minutes([a, b])
    assert total_default == 106

    # buffer 20 / speed 40: travel 4 + buffer 20 = 24. total = 90 + 24 = 114.
    total_cfg = calc_course_total_minutes(
        [a, b], config=_cfg(visit_buffer_min=20, travel_speed_kmh=40.0)
    )
    assert total_cfg == 114

    # 既定 config 明示 == 引数なし.
    assert calc_course_total_minutes([a, b], config=DEFAULT_SCHEDULING_CONFIG) == total_default


def test_apply_travel_corrections_business_end_config_unassigns_late_pm() -> None:
    # 午後希望 2 件. 後者が 16:00 以降に押し出されるレイアウト.
    # business_end 16:00 では遅延配置の warning が business_end ベースで出る.
    office = uuid4()
    a = V2Visit(
        patient_id=uuid4(),
        patient_name="A",
        patient_code=None,
        weekday=0,
        start_time=time(15, 0),
        end_time=time(15, 30),
        service_minutes=30,
        lat=BASE[0],
        lng=BASE[1],
        office_id=office,
        am_pm="pm",
        source_kind="fixed",
        course_code="A",
        time_type="午後",
    )
    b = V2Visit(
        patient_id=uuid4(),
        patient_name="B",
        patient_code=None,
        weekday=0,
        start_time=time(15, 30),
        end_time=time(16, 30),
        service_minutes=60,
        lat=P_2_7KM[0],
        lng=P_2_7KM[1],
        office_id=office,
        am_pm="pm",
        source_kind="fixed",
        course_code="A",
        time_type="午後",
    )

    warnings_default: list = []
    apply_travel_corrections(
        [replace(a), replace(b)],
        warnings=warnings_default,
        config=_cfg(business_end=time(18, 0)),
    )
    # 既定 18:00: 16:30 終了は超過しない → 18:00 超過 warning なし.
    assert not any(
        "18:00 を超過" in w.message or "16:00 を超過" in w.message for w in warnings_default
    )

    warnings_early: list = []
    apply_travel_corrections(
        [replace(a), replace(b)],
        warnings=warnings_early,
        config=_cfg(business_end=time(16, 0)),
    )
    # business_end 16:00: 午後 visit が 16:00 を超過する warning が出る.
    assert any("16:00 を超過" in w.message for w in warnings_early)


# ---------------------------------------------------------------------------
# propose 経路 (service オーケストレーション): compute_all_proposed_slots に効く
# ---------------------------------------------------------------------------


def _bucket(office_id, course_code: str, visits: list[V2Visit]) -> _CourseBucket:
    return _CourseBucket(
        office_id=office_id,
        weekday=0,
        course_code=course_code,
        office_code=None,
        staff_name=None,
        visits=visits,
    )


def test_compute_all_proposed_slots_business_end_config() -> None:
    office_id = uuid4()
    # PM 枠最後に 15:30-16:00 の既存 visit. 60 分候補.
    v = _v2(time(15, 30), time(16, 0))
    v = replace(v, office_id=office_id, course_code="A", am_pm="pm")
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", [v])}
    cand = CandidateInput(
        lat=BASE[0],
        lng=BASE[1],
        service_minutes=60,
        time_type="終日",
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=False,
        existing_patient_id=None,
    )

    default = compute_all_proposed_slots(
        buckets,
        {office_id: "O"},
        cand,
        office_ids=[office_id],
        config=_cfg(business_end=time(18, 0)),
    )
    assert any(s.start >= time(16, 0) for s in default), (
        "business_end 18:00 で 16:00 以降の枠が出る"
    )

    early = compute_all_proposed_slots(
        buckets,
        {office_id: "O"},
        cand,
        office_ids=[office_id],
        config=_cfg(business_end=time(16, 0)),
    )
    assert not any(s.start >= time(16, 0) for s in early), "business_end 16:00 で遅い枠が出ない"


def test_compute_all_proposed_slots_capacity_config() -> None:
    office_id = uuid4()
    # 既存 4 名 (連続枠).
    existing = [
        replace(_v2(time(9, 30), time(10, 0)), office_id=office_id, course_code="A"),
        replace(_v2(time(10, 30), time(11, 0)), office_id=office_id, course_code="A"),
        replace(_v2(time(14, 0), time(14, 30)), office_id=office_id, course_code="A"),
        replace(_v2(time(15, 0), time(15, 30)), office_id=office_id, course_code="A"),
    ]
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", existing)}
    cand = CandidateInput(
        lat=BASE[0],
        lng=BASE[1],
        service_minutes=30,
        time_type="終日",
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=False,
        existing_patient_id=None,
    )

    cap6 = compute_all_proposed_slots(
        buckets,
        {office_id: "O"},
        cand,
        office_ids=[office_id],
        config=_cfg(max_patients_per_course=6),
    )
    assert cap6, "capacity 6 では 5 人目の枠が出る"

    cap4 = compute_all_proposed_slots(
        buckets,
        {office_id: "O"},
        cand,
        office_ids=[office_id],
        config=_cfg(max_patients_per_course=4),
    )
    assert cap4 == [], "capacity 4 では既存 4 名で上限に達し枠が出ない"


# ---------------------------------------------------------------------------
# 漏れ修正 (1): _auto_shift_same_time_conflicts に buffer / 速度 config が効く
# ---------------------------------------------------------------------------


def _conflict_pair(office: object) -> tuple[V2Visit, V2Visit]:
    """同コース・同 start_time・異住所の 2 visit (BASE と P_2_7KM = 8 分/20km/h)."""
    a = V2Visit(
        patient_id=uuid4(),
        patient_name="A",
        patient_code=None,
        weekday=0,
        start_time=time(10, 0),
        end_time=time(10, 30),
        service_minutes=30,
        lat=BASE[0],
        lng=BASE[1],
        office_id=office,  # type: ignore[arg-type]
        am_pm="am",
        source_kind="fixed",
        course_code="A",
        time_type="時間帯",
    )
    b = V2Visit(
        patient_id=uuid4(),
        patient_name="B",
        patient_code=None,
        weekday=0,
        start_time=time(10, 0),
        end_time=time(10, 30),
        service_minutes=30,
        lat=P_2_7KM[0],
        lng=P_2_7KM[1],
        office_id=office,  # type: ignore[arg-type]
        am_pm="am",
        source_kind="fixed",
        course_code="A",
        time_type="時間帯",
    )
    return a, b


def test_auto_shift_default_config_equals_no_config() -> None:
    office = uuid4()
    a1, b1 = _conflict_pair(office)
    a2, b2 = _conflict_pair(office)
    w1: list[V2Warning] = []
    w2: list[V2Warning] = []
    res_none = _auto_shift_same_time_conflicts(
        [a1, b1], office_name="O", course_code="A", weekday=0, warnings=w1
    )
    res_default = _auto_shift_same_time_conflicts(
        [a2, b2],
        office_name="O",
        course_code="A",
        weekday=0,
        warnings=w2,
        config=DEFAULT_SCHEDULING_CONFIG,
    )
    # config=None と DEFAULT は同一結果 (= 後者の start が一致).
    assert [v.start_time for v in res_none] == [v.start_time for v in res_default]
    # 既定 buffer 8 / speed 20: prev.end 10:30 + travel 8 + buffer 8 = 10:46 → 5 分切上げ 10:50.
    assert res_none[1].start_time == time(10, 50)


def test_auto_shift_nondefault_buffer_speed_shifts_more() -> None:
    office = uuid4()
    a, b = _conflict_pair(office)
    warnings: list[V2Warning] = []
    # buffer 20 / speed 40: prev.end 10:30 + travel 4 + buffer 20 = 10:54 → 5 分切上げ 10:55.
    result = _auto_shift_same_time_conflicts(
        [a, b],
        office_name="O",
        course_code="A",
        weekday=0,
        warnings=warnings,
        config=_cfg(visit_buffer_min=20, travel_speed_kmh=40.0),
    )
    assert result[1].start_time == time(10, 55)
    # 既定 (10:50) より後ろにずれている (= config が効いている).
    assert result[1].start_time > time(10, 50)


# ---------------------------------------------------------------------------
# 漏れ修正 (2): 昼休みプレフィルタ (_filter_unavailable_and_lunch) に窓 config が効く
# ---------------------------------------------------------------------------


def test_lunch_prefilter_window_config_shifts_excluded_visits() -> None:
    # 11:50-13:10 の visit:
    #   - 既定窓 (11:30-13:30): AM 側 (start 11:50 < 12:00 NG) も PM 側
    #     (end 13:10 > 13:00 NG) も 30 分 lunch を確保できない →
    #     _is_in_lunch_break True → プレフィルタで除外.
    #   - 窓を 14:00-16:00 にずらすと、この visit は窓より前 (end 13:10 <= 14:00) →
    #     干渉なし → 残る.
    office = uuid4()
    mid = _v2(time(11, 50), time(13, 10))
    mid = replace(mid, office_id=office, course_code="A")

    out_default = _filter_unavailable_and_lunch(
        [replace(mid)],
        unavailable_slots={},
        warnings=[],
        skip_acceptance=True,
        config=_cfg(lunch_window_start=time(11, 30), lunch_window_end=time(13, 30)),
    )
    assert not any(v.start_time == time(11, 50) for v in out_default), (
        "既定窓 (11:30-13:30) では 11:50-13:10 は lunch 不可避で除外される"
    )

    # 窓を 14:00-16:00 にずらす: 11:50-13:10 は窓より前 → 除外されず残る.
    out_shifted = _filter_unavailable_and_lunch(
        [replace(mid)],
        unavailable_slots={},
        warnings=[],
        skip_acceptance=True,
        config=_cfg(lunch_window_start=time(14, 0), lunch_window_end=time(16, 0)),
    )
    assert any(v.start_time == time(11, 50) for v in out_shifted), (
        "lunch 窓を 14:00-16:00 にずらすと 11:50-13:10 は干渉せず残る (= 窓 config が効く)"
    )


def test_lunch_prefilter_default_config_equals_no_config() -> None:
    office = uuid4()
    v = _v2(time(12, 10), time(12, 50))  # AM/PM どちらでも避けられない → 既定で除外.
    v = replace(v, office_id=office, course_code="A")
    out_none = _filter_unavailable_and_lunch(
        [replace(v)], unavailable_slots={}, warnings=[], skip_acceptance=True
    )
    out_default = _filter_unavailable_and_lunch(
        [replace(v)],
        unavailable_slots={},
        warnings=[],
        skip_acceptance=True,
        config=DEFAULT_SCHEDULING_CONFIG,
    )
    assert [x.start_time for x in out_none] == [x.start_time for x in out_default]


# ---------------------------------------------------------------------------
# 漏れ修正 (3): _extract_weekly_entries の仮開始に business_start config が効く
# ---------------------------------------------------------------------------


def _patient_no_start() -> Patient:
    """preferred_start 不在の weekly_pattern を持つ患者 (仮開始が使われる)."""
    return Patient(
        id=uuid4(),
        code="NOSTART",
        name="No start",
        status="active",
        lat=BASE[0],
        lng=BASE[1],
        primary_office_id=uuid4(),
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "service_minutes": 30,
            "time_type": "時間帯",
        },
    )


def test_extract_weekly_entries_default_business_start_is_0930() -> None:
    entries_none = _extract_weekly_entries(_patient_no_start())
    entries_default = _extract_weekly_entries(_patient_no_start(), config=DEFAULT_SCHEDULING_CONFIG)
    assert entries_none[0][1] == time(9, 30)
    assert entries_default[0][1] == time(9, 30)


def test_extract_weekly_entries_business_start_config_applies() -> None:
    # business_start 08:00 を指定 → 仮開始が 08:00 になる.
    entries = _extract_weekly_entries(_patient_no_start(), config=_cfg(business_start=time(8, 0)))
    assert entries[0][1] == time(8, 0)


def test_extract_weekly_entries_entries_form_business_start_config() -> None:
    # entries 形式 (preferred_start なし) でも仮開始に business_start が効く.
    patient = Patient(
        id=uuid4(),
        code="E",
        name="entries",
        status="active",
        lat=BASE[0],
        lng=BASE[1],
        primary_office_id=uuid4(),
        weekly_pattern={
            "entries": [{"weekday": "Mon", "service_minutes": 30, "time_type": "時間帯"}],
        },
    )
    entries = _extract_weekly_entries(patient, config=_cfg(business_start=time(8, 30)))
    assert entries[0][1] == time(8, 30)


# ---------------------------------------------------------------------------
# 漏れ修正 (4): 同住所ペア lunch 圧迫 warning に窓 config が効く
# ---------------------------------------------------------------------------


def _same_addr_pair(office: object, start: time, service: int) -> list[V2Visit]:
    a = V2Visit(
        patient_id=uuid4(),
        patient_name="A",
        patient_code=None,
        weekday=0,
        start_time=start,
        end_time=_min_add(start, service),
        service_minutes=service,
        lat=BASE[0],
        lng=BASE[1],
        office_id=office,  # type: ignore[arg-type]
        am_pm="any",
        source_kind="fixed",
        course_code="A",
        time_type="時間帯",
    )
    b = replace(a, patient_id=uuid4(), patient_name="B")
    return [a, b]


def _min_add(t: time, m: int) -> time:
    total = t.hour * 60 + t.minute + m
    return time(total // 60, total % 60)


def test_same_address_pair_lunch_warning_window_config() -> None:
    # 同住所ペアを 11:30 開始・各 60 分 (占有 120 分 = 11:30-13:30) に置く.
    # 既定窓 (11:30-13:30): _is_in_lunch_break True → 「重なります」warning.
    office = uuid4()
    pair_default = _same_addr_pair(office, time(11, 30), 60)
    w_default: list[V2Warning] = []
    _align_same_address_pair_to_same_time(
        pair_default,
        warnings=w_default,
        weekday=0,
        course_code="A",
        office_name="O",
        config=_cfg(lunch_window_start=time(11, 30), lunch_window_end=time(13, 30)),
    )
    assert any("11:30-13:30" in w.message and "重なります" in w.message for w in w_default), (
        "既定窓では 11:30-13:30 占有が昼休憩と重なる warning を出す"
    )

    # 窓を 14:00-16:00 にずらすと、11:30-13:30 占有は lunch 窓外 → 「重なります」warning なし.
    office2 = uuid4()
    pair_shifted = _same_addr_pair(office2, time(11, 30), 60)
    w_shifted: list[V2Warning] = []
    _align_same_address_pair_to_same_time(
        pair_shifted,
        warnings=w_shifted,
        weekday=0,
        course_code="A",
        office_name="O",
        config=_cfg(lunch_window_start=time(14, 0), lunch_window_end=time(16, 0)),
    )
    assert not any("重なります" in w.message for w in w_shifted), (
        "lunch 窓を 14:00-16:00 にずらすと 11:30-13:30 占有は干渉しない"
    )
    # warning メッセージが窓 config を反映する (14:00-16:00 文字列).
    assert all("11:30-13:30" not in w.message for w in w_shifted)


# ---------------------------------------------------------------------------
# 漏れ修正 (5): calc_h_violations H9 に capacity config が効く
# ---------------------------------------------------------------------------


def test_calc_h_violations_h9_capacity_config() -> None:
    # 同コース 5 名: 既定 (6) では H9=0, capacity=4 では H9=1.
    office = uuid4()
    visits = []
    for i in range(5):
        v = _v2(time(9, 30 + i), time(10, 0 + i))
        v = replace(v, office_id=office, course_code="A")
        visits.append(v)

    h_default = calc_h_violations(visits)
    assert h_default["H9"] == 0, "既定 capacity 6 では 5 名は超過しない"

    h_none_equals_default = calc_h_violations(visits, config=DEFAULT_SCHEDULING_CONFIG)
    assert h_none_equals_default["H9"] == 0

    h_cap4 = calc_h_violations(visits, config=_cfg(max_patients_per_course=4))
    assert h_cap4["H9"] == 1, "capacity 4 では 5 名コースが 1 件超過する"


# ---------------------------------------------------------------------------
# 残漏れ修正 (G-88 Step3 再レビュー): calc_h_violations H10 に昼休み窓 config が効く
# ---------------------------------------------------------------------------


def test_calc_h_violations_h10_lunch_window_config() -> None:
    # 14:10-15:40 の visit (90 分):
    #   - 既定窓 (11:30-13:30): visit は窓外 (start 14:10 >= 13:30) → H10=0.
    #   - 窓を 14:00-16:00 にずらすと、AM 側回避 (start 14:10 < 14:30) も PM 側回避
    #     (end 15:40 > 15:30) も 30 分 lunch を確保できず lunch 不可避 → H10=1.
    office = uuid4()
    v = replace(_v2(time(14, 10), time(15, 40)), office_id=office, course_code="A")

    h_default = calc_h_violations([replace(v)])
    assert h_default["H10"] == 0, "既定窓 (11:30-13:30) では 14:10-14:50 は窓外で H10=0"

    h_none_equals_default = calc_h_violations([replace(v)], config=DEFAULT_SCHEDULING_CONFIG)
    assert h_none_equals_default["H10"] == 0, "config=None と DEFAULT は同一 (回帰ゼロ)"

    h_shifted = calc_h_violations(
        [replace(v)],
        config=_cfg(lunch_window_start=time(14, 0), lunch_window_end=time(16, 0)),
    )
    assert h_shifted["H10"] == 1, (
        "昼休み窓を 14:00-16:00 にずらすと 14:10-15:40 は lunch 不可避で H10=1"
    )


# ---------------------------------------------------------------------------
# 漏れ修正 (6): 確定適用経路 (_apply_corrections_to_visits) がプレビューと同一
# config で再計算する. apply_week_only / reset_visits_to_fixed /
# apply_individual_proposal はいずれも本 helper を経由するため、本 helper が
# config を honor し、かつプレビュー側 apply_travel_corrections と同一結果を出す
# ことを示せば「確定 = プレビュー」の整合が担保される.
# ---------------------------------------------------------------------------


def _pm_late_pair(office: object) -> list[V2Visit]:
    """午後 2 件 (異住所). business_end 次第で後者が超過 warning を出すレイアウト."""
    a = V2Visit(
        patient_id=uuid4(),
        patient_name="A",
        patient_code=None,
        weekday=0,
        start_time=time(15, 0),
        end_time=time(15, 30),
        service_minutes=30,
        lat=BASE[0],
        lng=BASE[1],
        office_id=office,  # type: ignore[arg-type]
        am_pm="pm",
        source_kind="fixed",
        course_code="A",
        time_type="午後",
    )
    b = V2Visit(
        patient_id=uuid4(),
        patient_name="B",
        patient_code=None,
        weekday=0,
        start_time=time(15, 30),
        end_time=time(16, 30),
        service_minutes=60,
        lat=P_2_7KM[0],
        lng=P_2_7KM[1],
        office_id=office,  # type: ignore[arg-type]
        am_pm="pm",
        source_kind="fixed",
        course_code="A",
        time_type="午後",
    )
    return [a, b]


def test_apply_corrections_matches_preview_with_same_config() -> None:
    # 確定経路 helper (_apply_corrections_to_visits) と プレビュー
    # (apply_travel_corrections) は、同一 config / 同一入力で同一の確定結果
    # (start/end) と warning を出すこと (= 確定 == プレビュー).
    office = uuid4()
    cfg = _cfg(business_end=time(16, 0))

    preview_visits = _pm_late_pair(office)
    preview_warnings: list[V2Warning] = []
    apply_travel_corrections(preview_visits, warnings=preview_warnings, config=cfg)

    apply_visits = _pm_late_pair(office)
    apply_warnings: list[V2Warning] = []
    _apply_corrections_to_visits(apply_visits, warnings=apply_warnings, config=cfg)

    # 確定 start/end がプレビューと一致.
    assert [(v.start_time, v.end_time) for v in apply_visits] == [
        (v.start_time, v.end_time) for v in preview_visits
    ]
    # business_end 16:00 超過 warning が確定側でも出る (= config が確定経路に効く).
    assert any("16:00 を超過" in w.message for w in apply_warnings)
    assert any("16:00 を超過" in w.message for w in preview_warnings)


def test_apply_corrections_default_config_no_late_warning() -> None:
    # business_end 18:00 (既定相当): 16:30 終了は超過しない → 確定経路でも warning なし.
    office = uuid4()
    apply_visits = _pm_late_pair(office)
    apply_warnings: list[V2Warning] = []
    _apply_corrections_to_visits(
        apply_visits, warnings=apply_warnings, config=DEFAULT_SCHEDULING_CONFIG
    )
    assert not any(
        "18:00 を超過" in w.message or "16:00 を超過" in w.message for w in apply_warnings
    )
    # config=None も既定と同一挙動 (回帰ゼロ).
    apply_visits_none = _pm_late_pair(office)
    apply_warnings_none: list[V2Warning] = []
    _apply_corrections_to_visits(apply_visits_none, warnings=apply_warnings_none)
    assert [(v.start_time, v.end_time) for v in apply_visits_none] == [
        (v.start_time, v.end_time) for v in apply_visits
    ]
