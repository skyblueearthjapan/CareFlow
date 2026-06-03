"""提案スロットソルバ (Phase2-1) 単体テスト.

auto_allocator_v2 と挙動が一致することを検証する. DB 非依存・純ロジックのみ.

座標メモ (千葉市近辺):
    BASE = (35.6000, 140.1000)
    P_1_4KM = (35.6100, 140.1100)  → BASE から ~1.433km / 4 分 (+8 buffer = 12 分)
    P_2_7KM = (35.6000, 140.1300)  → BASE から ~2.712km / 8 分 (+8 buffer = 16 分)
    SAME = (35.60005, 140.10005)   → BASE と同住所 (<=100m, _address_bucket 一致)
"""

from __future__ import annotations

from datetime import time

import pytest

from app.services.scheduling.auto_allocator_v2 import (
    SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
    SHORTAGE_THRESHOLD_MIN,
    VISIT_BUFFER_MINUTES,
    _address_bucket,
    haversine_km,
    haversine_minutes,
)
from app.services.scheduling.proposal_solver import (
    Candidate,
    ExistingVisit,
    compute_earliest_start_after,
    find_available_slots_for_candidate,
    slot_feasible,
)

BASE = (35.6000, 140.1000)
P_1_4KM = (35.6100, 140.1100)
P_2_7KM = (35.6000, 140.1300)
SAME = (35.60005, 140.10005)


# ---------------------------------------------------------------------------
# プリミティブ単体検証 (距離→移動分 / 5 分刻み切上げ / 同住所判定)
# ---------------------------------------------------------------------------


def test_primitive_distance_to_minutes() -> None:
    km = haversine_km(*BASE, *P_1_4KM)
    assert 1.40 < km < 1.46
    assert haversine_minutes(km) == 4  # 1.433 / 20 * 60 ≈ 4.3 → round 4
    km2 = haversine_km(*BASE, *P_2_7KM)
    assert haversine_minutes(km2) == 8  # 2.712 / 20 * 60 ≈ 8.1 → round 8


def test_primitive_same_address_bucket() -> None:
    assert _address_bucket(*BASE) == _address_bucket(*SAME)  # <=100m → 同住所
    assert _address_bucket(*BASE) != _address_bucket(*P_1_4KM)  # 1.4km → 別住所


def test_earliest_start_5min_roundup_cross_address() -> None:
    # prev_end 10:00, 異住所 4 分移動 + 8 buffer = 12 分 → 10:12 → 5 分切上げ 10:15.
    got = compute_earliest_start_after(time(10, 0), BASE, P_1_4KM, same_address=False)
    assert got == time(10, 15)


def test_earliest_start_same_address_zero_travel() -> None:
    # 同住所: 移動 0 + buffer 0 → prev_end のまま (既に 5 分刻み).
    got = compute_earliest_start_after(time(10, 0), BASE, SAME, same_address=True)
    assert got == time(10, 0)


# ---------------------------------------------------------------------------
# ケース 1: 異住所連続 — 移動 + 距離分が加算され開始がずれる / 不足で前詰め不可
# ---------------------------------------------------------------------------


def test_case1_cross_address_shifts_start() -> None:
    # 既存: BASE 09:30-10:00. 候補は P_2_7KM (8 分 + 8 buffer = 16 分) 60 分.
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*P_2_7KM, service_minutes=60, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    am_slots = [s for s in slots if s.block == "am" and not s.same_address_pair]
    assert am_slots, "AM gap に少なくとも 1 スロット"
    # 10:00 + 16 = 10:16 → 5 分切上げ 10:20.
    after = next(s for s in am_slots if s.start >= time(10, 0))
    assert after.start == time(10, 20)


def test_case1_cannot_fit_before_due_to_travel() -> None:
    # 先頭ギャップ (枠開始 09:30) には移動制約なしで入れるが、既存 visit の直前
    # (枠開始〜既存.start) に候補 60 分は入らない (既存が 09:40 開始で被る).
    existing = [
        ExistingVisit(time(9, 40), time(10, 10), *P_2_7KM, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*BASE, service_minutes=60, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    # 先頭 09:30 開始だと 09:30-10:30 で、次 visit 09:40 と被る → 先頭は不可.
    head = [
        s for s in slots if s.block == "am" and s.start == time(9, 30) and not s.same_address_pair
    ]
    assert not head, "枠先頭 09:30 は次 visit と移動含め衝突するので不可"


# ---------------------------------------------------------------------------
# ケース 2: 同住所 — 移動 0 / 同時刻ペア / 90 分占有
# ---------------------------------------------------------------------------


def test_case2_same_address_pair_same_time_90min() -> None:
    existing = [
        ExistingVisit(time(10, 0), time(10, 30), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*SAME, service_minutes=30, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    pairs = [s for s in slots if s.same_address_pair]
    assert pairs, "同住所ペアのスロットが存在"
    pair = pairs[0]
    # 同時刻 (既存 start) かつ 90 分占有 (30+30=60 < 90 → 90 へ底上げ).
    assert pair.start == time(10, 0)
    occupancy = (pair.end.hour * 60 + pair.end.minute) - (pair.start.hour * 60 + pair.start.minute)
    assert occupancy == SAME_ADDRESS_PAIR_MIN_OCCUPANCY


def test_case2_same_address_zero_travel_in_earliest() -> None:
    # earliest 計算でも同住所は travel 0 / buffer 0.
    got = compute_earliest_start_after(time(11, 0), BASE, SAME, same_address=True)
    assert got == time(11, 0)


# ---------------------------------------------------------------------------
# ケース 3: 昼休みスキップ — 11:30-13:30 帯に置かれない
# ---------------------------------------------------------------------------


def test_case3_lunch_skip_no_slot_in_window() -> None:
    lunch = (time(12, 0), time(13, 0))
    # 既存なし、候補 終日 60 分. AM 末尾 11:30 開始 (→12:30) は lunch と重なる → 不可.
    cand = Candidate(*BASE, service_minutes=60, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate([], cand, lunch_window=lunch, weekday=0)
    for s in slots:
        # どのスロットも lunch window と重ならない.
        overlaps = s.start < lunch[1] and s.end > lunch[0]
        assert not overlaps, f"{s.start}-{s.end} が昼休みと重なる"


def test_case3_lunch_overlap_rejected_by_slot_feasible() -> None:
    lunch = (time(12, 0), time(13, 0))
    cand = Candidate(*BASE, service_minutes=60, time_type="終日")
    # 11:30 開始 → 12:30 終了 (lunch と重なる) は不可.
    assert not slot_feasible(time(11, 30), cand, lunch_window=lunch, block="am")
    # 11:00 開始 → 12:00 終了 (lunch start にちょうど接する) は OK.
    assert slot_feasible(time(11, 0), cand, lunch_window=lunch, block="am")


# ---------------------------------------------------------------------------
# ケース 4: 午前希望が満杯 → 午後にバンプ or 午前不可
# ---------------------------------------------------------------------------


def test_case4_am_full_no_am_slot() -> None:
    # AM 枠 09:30-12:00 を既存で埋める (異住所連続). 午前希望候補は AM に入れない.
    existing = [
        ExistingVisit(time(9, 30), time(11, 30), *BASE, service_minutes=120, patient_id="A"),
    ]
    cand = Candidate(*P_2_7KM, service_minutes=60, time_type="午前", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    # 11:30 + 16 = 11:46 → 5 分切上げ 11:50 開始だと 12:50 で 12:00 超過 → 午前不可.
    am = [s for s in slots if s.block == "am"]
    assert not am, "午前枠が埋まっているので午前希望候補は配置不可"


def test_case4_am_candidate_fits_when_room() -> None:
    # 午前に余裕がある場合は午前に入る.
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*P_1_4KM, service_minutes=30, time_type="午前", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    am = [s for s in slots if s.block == "am" and not s.same_address_pair]
    assert am
    # 10:00 + 4 + 8 = 10:12 → 10:15 開始、10:45 終了 (12:00 内).
    assert any(s.start == time(10, 15) for s in am)


# ---------------------------------------------------------------------------
# ケース 5: 18:00 超過で末尾候補が排除される
# ---------------------------------------------------------------------------


def test_case5_after_18_excluded() -> None:
    # PM 最終 visit 17:30 終了. 候補 P_2_7KM 60 分は 17:30 + 16 = 17:46 → 17:50 開始
    # → 18:50 終了で 18:00 超過 → 不可.
    existing = [
        ExistingVisit(time(17, 0), time(17, 30), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*P_2_7KM, service_minutes=60, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    late = [s for s in slots if s.start >= time(17, 30)]
    assert not late, "17:30 以降の 60 分候補は 18:00 を超えるため不可"


def test_case5_short_visit_fits_before_18() -> None:
    # 同条件でも 10 分 visit なら 17:50-18:00 で収まる.
    existing = [
        ExistingVisit(time(17, 0), time(17, 30), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*P_2_7KM, service_minutes=10, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    assert any(s.end <= time(18, 0) and s.start >= time(17, 30) for s in slots)


# ---------------------------------------------------------------------------
# ケース 6: 固定時刻 — 移動不足は SHORTAGE_THRESHOLD_MIN で分岐
#   (auto_allocator_v2 と同手法: < 5 分不足は warning 付きで許容 / >= 5 分は不可)
# ---------------------------------------------------------------------------


def test_case6_fixed_time_infeasible_when_shortage_ge_threshold() -> None:
    # 既存 BASE 09:30-10:00. 候補 P_2_7KM (移動 8 + buffer 8 = 16 分) 固定 10:10.
    # raw earliest = 10:00 + 16 = 10:16. shortage = 16:16-10:10 = 6 分 >= 5 → 不可.
    assert SHORTAGE_THRESHOLD_MIN == 5
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(
        *P_2_7KM,
        service_minutes=30,
        time_type="固定",
        preferred_start=time(10, 10),
        patient_id="C",
    )
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    assert not [s for s in slots if not s.same_address_pair], (
        "shortage >= SHORTAGE_THRESHOLD_MIN なら固定時刻に入れない"
    )


def test_case6_fixed_time_feasible_with_warning_when_shortage_lt_threshold() -> None:
    # 同条件で固定 10:13. raw earliest = 10:16, shortage = 3 分 < 5 → warning 付きで許容.
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(
        *P_2_7KM,
        service_minutes=30,
        time_type="固定",
        preferred_start=time(10, 13),
        patient_id="C",
    )
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    fixed_slots = [s for s in slots if not s.same_address_pair and s.start == time(10, 13)]
    assert fixed_slots, "shortage < SHORTAGE_THRESHOLD_MIN なら固定時刻に warning 付きで配置"
    assert fixed_slots[0].warning == "travel_time_shortage"


def test_case6_fixed_time_feasible_when_travel_ok() -> None:
    # 固定 10:30 なら earliest 10:20 <= 10:30 → OK.
    existing = [
        ExistingVisit(time(9, 30), time(10, 0), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(
        *P_2_7KM,
        service_minutes=30,
        time_type="固定",
        preferred_start=time(10, 30),
        patient_id="C",
    )
    slots = find_available_slots_for_candidate(existing, cand, lunch_window=None, weekday=0)
    assert any(s.start == time(10, 30) for s in slots)


# ---------------------------------------------------------------------------
# ケース 7: 容量 — 6 名満杯 / 480 分超で候補不可
# ---------------------------------------------------------------------------


def test_case7_capacity_count_full() -> None:
    # 6 名既存 → 7 人目は不可 (同住所ペアも used_count>=6 で不可).
    cand = Candidate(*P_1_4KM, service_minutes=30, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, course_capacity_used=6, course_minutes_used=200
    )
    assert slots == []


def test_case7_capacity_minutes_full() -> None:
    # used_minutes 460 + 候補 30 = 490 > 480 → 不可.
    cand = Candidate(*P_1_4KM, service_minutes=30, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, course_capacity_used=3, course_minutes_used=460
    )
    assert slots == []


def test_case7_capacity_ok_when_room() -> None:
    cand = Candidate(*P_1_4KM, service_minutes=30, time_type="終日", patient_id="C")
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, course_capacity_used=2, course_minutes_used=100
    )
    assert slots, "容量に余裕があればスロットが出る"


# ---------------------------------------------------------------------------
# 補助: buffer 定数が auto_allocator_v2 と一致 (ハードコード再定義していない証跡)
# ---------------------------------------------------------------------------


def test_buffer_constant_matches_engine() -> None:
    assert VISIT_BUFFER_MINUTES == 8


@pytest.mark.parametrize(
    ("prev_end", "expected"),
    [
        (time(10, 0), time(10, 15)),  # +12 → 10:12 → 切上 10:15
        (time(10, 5), time(10, 20)),  # +12 → 10:17 → 切上 10:20
    ],
)
def test_earliest_start_roundup_parametrized(prev_end: time, expected: time) -> None:
    got = compute_earliest_start_after(prev_end, BASE, P_1_4KM, same_address=False)
    assert got == expected
