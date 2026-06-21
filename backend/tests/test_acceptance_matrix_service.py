"""受け入れ枠マトリックス 集計サービスの純ロジックテスト (DB 非依存).

``build_course_state`` / ``classify_slot`` の容量ベース判定を検証する。
``test_proposal_solver.py`` と同じく dataclass 入出力のみで完結する。
"""

from __future__ import annotations

from uuid import uuid4

from app.services.scheduling.acceptance_matrix_service import (
    MatrixVisit,
    build_course_state,
    classify_slot,
)
from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG

CFG = DEFAULT_SCHEDULING_CONFIG


def _m(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _mv(start: str, end: str, *, pid=None, lat=None, lng=None) -> MatrixVisit:
    return MatrixVisit(
        start_min=_m(start),
        end_min=_m(end),
        patient_id=pid or uuid4(),
        lat=lat,
        lng=lng,
    )


def test_empty_course_is_available_in_business_hours() -> None:
    st = build_course_state([], config=CFG)
    assert st.remaining_count == 6
    assert st.remaining_minutes == 480
    # 10:00 / 14:00 は ○、昼の 12:00 は × (営業枠外コア).
    assert classify_slot([st], _m("10:00")).status == "available"
    assert classify_slot([st], _m("14:00")).status == "available"
    assert classify_slot([st], _m("12:00")).status == "unavailable"


def test_full_course_is_unavailable() -> None:
    # 6 名 (別患者) → 残人数 0 → どの時間帯も ×.
    visits = [_mv("09:30", "10:00") for _ in range(6)]
    st = build_course_state(visits, config=CFG)
    assert st.remaining_count == 0
    assert classify_slot([st], _m("15:00")).status == "unavailable"


def test_single_visit_blocks_only_its_slot() -> None:
    st = build_course_state([_mv("10:00", "10:30")], config=CFG)
    assert st.remaining_count == 5
    # 10:00 枠は visit と被り完全空きでない → △.
    assert classify_slot([st], _m("10:00")).status == "consult"
    # 11:00 枠は完全に空き → ○.
    assert classify_slot([st], _m("11:00")).status == "available"


def test_same_address_pair_occupies_90_minutes() -> None:
    lat, lng = 35.6, 140.1
    pair = [
        _mv("10:00", "10:30", lat=lat, lng=lng),
        _mv("10:00", "10:30", lat=lat, lng=lng),
    ]
    st = build_course_state(pair, config=CFG)
    # 同住所同時刻の 2 名は 10:00-11:30 (90 分) を占有 → (690, 720) が空き.
    assert (690, 720) in st.free_intervals
    assert st.remaining_count == 4  # 2 名で 2 消費


def test_different_address_same_time_not_bumped() -> None:
    pair = [
        _mv("10:00", "10:30", lat=35.6, lng=140.1),
        _mv("10:00", "10:30", lat=35.7, lng=140.2),
    ]
    st = build_course_state(pair, config=CFG)
    # 別住所なら 90 分底上げなし → 10:30 以降 (630,720) が空き.
    assert (630, 720) in st.free_intervals


def test_visit_without_coords_still_occupies() -> None:
    st = build_course_state([_mv("10:00", "11:00", lat=None, lng=None)], config=CFG)
    # 座標なしでも占有計上 → 10:30 始まり枠は被るので 10:00 枠は ×.
    assert classify_slot([st], _m("10:00")).status == "unavailable"
    assert (660, 720) in st.free_intervals  # 11:00 以降は空き


def test_only_30min_gap_is_consult() -> None:
    # 13:00-17:30 を埋め、PM に 30 分の隙間しか残さない (17:30-18:00).
    visits = [_mv("13:00", "17:30")]
    st = build_course_state(visits, config=CFG)
    # 17:00 枠 [17:00,18:00) は 17:30 まで埋まり 30 分のみ空き → △.
    assert classify_slot([st], _m("17:00")).status == "consult"


def test_metrics_aggregate_across_courses() -> None:
    empty = build_course_state([], config=CFG)
    one = build_course_state([_mv("10:00", "10:30")], config=CFG)
    res = classify_slot([empty, one], _m("11:00"))
    assert res.status == "available"
    assert res.remaining_patients_total == 6 + 5
    assert res.available_course_count >= 1
