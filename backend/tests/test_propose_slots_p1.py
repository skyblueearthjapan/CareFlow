"""Stage P-1 単体テスト: propose-slots の物差し統一 (delta) と除外理由集約.

P-1a: 各候補スロットに挿入の厳密限界コスト ``marginal_cost_minutes`` を付与し、
      delta 昇順を主キーに再ソートする (improvement_engine の compute_exact_marginal /
      course_travel_buffer_total と同一物差し = クロスチェックで一致).
P-1b: 候補 0 件時に除外理由を ``excluded_summary`` として集約 (N-6「黙って消さない」).

DB 非依存・純ロジックのみ. 座標は test_proposal_solver と同じ千葉市近辺.
"""

from __future__ import annotations

from datetime import time
from uuid import UUID, uuid4

from app.services.scheduling.auto_allocator_v2 import V2Visit
from app.services.scheduling.improvement_engine import course_travel_buffer_total
from app.services.scheduling.proposal_solver import ExistingVisit
from app.services.scheduling.propose_slots_service import (
    DELTA_EVAL_LIMIT,
    CandidateInput,
    ExcludedReasonSummary,
    _CourseBucket,
    _to_existing_visits,
    compute_all_proposed_slots,
)

BASE = (35.6000, 140.1000)
SAME = (35.60005, 140.10005)  # BASE と同住所 (<=100m)
P_2_7KM = (35.6000, 140.1300)  # BASE から ~2.712km (8 分 + 8 buffer = 16 分)
FAR = (35.6500, 140.2000)  # BASE から遠方 (回り道)


def _v2(
    start: time,
    end: time,
    *,
    lat: float = BASE[0],
    lng: float = BASE[1],
    service: int = 30,
    office_id: UUID,
    course_code: str = "A",
) -> V2Visit:
    return V2Visit(
        patient_id=uuid4(),
        patient_name="既存",
        patient_code=None,
        weekday=0,
        start_time=start,
        end_time=end,
        service_minutes=service,
        lat=lat,
        lng=lng,
        office_id=office_id,
        am_pm="am" if start.hour < 12 else "pm",  # type: ignore[arg-type]
        source_kind="fixed",
        course_code=course_code,
    )


def _bucket(office_id: UUID, code: str, visits: list[V2Visit]) -> _CourseBucket:
    return _CourseBucket(
        office_id=office_id,
        weekday=0,
        course_code=code,
        office_code=None,
        staff_name="S",
        visits=visits,
    )


def _candidate(
    *,
    lat: float = BASE[0],
    lng: float = BASE[1],
    service: int = 30,
) -> CandidateInput:
    return CandidateInput(
        lat=lat,
        lng=lng,
        service_minutes=service,
        time_type="終日",
        preferred_start=None,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=False,
        existing_patient_id=None,
    )


# ---------------------------------------------------------------------------
# P-1a: 厳密限界コスト (delta) の付与・並び順・クロスチェック
# ---------------------------------------------------------------------------


def test_same_address_slot_has_delta_zero_and_ranks_top() -> None:
    """同住所・同時刻に入れられる枠は delta≈0 になり最上位に来る (pair_bonus を物差しが吸収)."""
    office_id = uuid4()
    # A: 候補と同住所の既存 (10:00). B: 遠方 (P_2_7KM) の既存.
    a = _v2(
        time(10, 0), time(10, 30), lat=BASE[0], lng=BASE[1], office_id=office_id, course_code="A"
    )
    b = _v2(
        time(10, 0),
        time(10, 30),
        lat=P_2_7KM[0],
        lng=P_2_7KM[1],
        office_id=office_id,
        course_code="B",
    )
    buckets = {
        (office_id, 0, "A"): _bucket(office_id, "A", [a]),
        (office_id, 0, "B"): _bucket(office_id, "B", [b]),
    }
    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, _candidate(), office_ids=[office_id]
    )
    assert results, "枠が出るはず"
    top = results[0]
    assert top.marginal_cost_minutes == 0.0, f"最上位は delta≈0: {top.marginal_cost_minutes}"
    # A コース (同住所) の枠が最上位群、B コース (遠方) は delta が大きい.
    a_deltas = [r.marginal_cost_minutes for r in results if r.course_code == "A"]
    b_deltas = [
        r.marginal_cost_minutes
        for r in results
        if r.course_code == "B" and r.marginal_cost_minutes is not None
    ]
    assert all(d == 0.0 for d in a_deltas if d is not None), "同住所コースは delta 0"
    assert b_deltas and min(b_deltas) > 0.0, "遠方コースは delta > 0"


def test_far_slot_has_larger_delta_and_ranks_lower() -> None:
    """回り道になる遠方枠は delta が大きく、同住所枠より順位が下がる."""
    office_id = uuid4()
    near = _v2(time(10, 0), time(10, 30), office_id=office_id, course_code="A")  # BASE
    far = _v2(
        time(10, 0), time(10, 30), lat=FAR[0], lng=FAR[1], office_id=office_id, course_code="B"
    )
    buckets = {
        (office_id, 0, "A"): _bucket(office_id, "A", [near]),
        (office_id, 0, "B"): _bucket(office_id, "B", [far]),
    }
    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, _candidate(), office_ids=[office_id]
    )
    assert results
    # delta 昇順に並ぶので、遠方 (B) の最良 index は 近接 (A) の最良 index より後ろ.
    first_a = next(i for i, r in enumerate(results) if r.course_code == "A")
    first_b = next(i for i, r in enumerate(results) if r.course_code == "B")
    assert first_a < first_b, "近接コースが遠方コースより上位"
    a_delta = results[first_a].marginal_cost_minutes
    b_delta = results[first_b].marginal_cost_minutes
    assert a_delta is not None and b_delta is not None
    assert b_delta > a_delta, f"遠方の delta ({b_delta}) が近接 ({a_delta}) より大きい"


def test_marginal_cost_matches_course_travel_buffer_total_diff() -> None:
    """marginal_cost_minutes が improvement_engine の course_travel_buffer_total 差分と一致."""
    office_id = uuid4()
    # 既存 2 件 (BASE と P_2_7KM) のコースに候補 (BASE) を入れる.
    v1 = _v2(time(9, 30), time(10, 0), office_id=office_id, course_code="A")
    v2 = _v2(
        time(14, 0),
        time(14, 30),
        lat=P_2_7KM[0],
        lng=P_2_7KM[1],
        office_id=office_id,
        course_code="A",
    )
    bucket = _bucket(office_id, "A", [v1, v2])
    buckets = {(office_id, 0, "A"): bucket}
    cand = _candidate()
    results = compute_all_proposed_slots(buckets, {office_id: "O"}, cand, office_ids=[office_id])
    assert results

    existing = _to_existing_visits(bucket)
    base_min, _ = course_travel_buffer_total(existing)
    checked = 0
    for r in results:
        if r.marginal_cost_minutes is None:
            continue
        member = ExistingVisit(
            start_time=r.start,
            end_time=r.end,
            lat=cand.lat,
            lng=cand.lng,
            service_minutes=cand.service_minutes,
            patient_id="__candidate__",
        )
        with_min, _ = course_travel_buffer_total([*existing, member])
        expected = float(with_min - base_min)
        assert r.marginal_cost_minutes == expected, (
            f"delta 不一致 slot={r.start}: got {r.marginal_cost_minutes}, expected {expected}"
        )
        checked += 1
    assert checked > 0, "少なくとも 1 枠を検証したはず"


def test_beyond_delta_eval_limit_stays_none_at_tail() -> None:
    """DELTA_EVAL_LIMIT 超の候補は marginal_cost_minutes=None で末尾に残る."""
    office_id = uuid4()
    # DELTA_EVAL_LIMIT より多いコースを作り、各コース 1 枠以上出す (候補は全て空きに入る).
    n_courses = DELTA_EVAL_LIMIT + 8
    buckets: dict[tuple[UUID, int, str], _CourseBucket] = {}
    for i in range(n_courses):
        code = f"C{i:02d}"
        # コースごとに少しずつ異なる遠さの既存 1 件 (delta を分散させる).
        lat = BASE[0] + 0.001 * i
        v = _v2(
            time(9, 30), time(10, 0), lat=lat, lng=BASE[1], office_id=office_id, course_code=code
        )
        buckets[(office_id, 0, code)] = _bucket(office_id, code, [v])

    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, _candidate(), office_ids=[office_id]
    )
    assert len(results) > DELTA_EVAL_LIMIT, "DELTA_EVAL_LIMIT を超える枠数が必要"
    head = results[:DELTA_EVAL_LIMIT]
    tail = results[DELTA_EVAL_LIMIT:]
    assert all(r.marginal_cost_minutes is not None for r in head), "上位は delta 計算済み"
    assert all(r.marginal_cost_minutes is None for r in tail), "下位は None のまま末尾に残る"


# ---------------------------------------------------------------------------
# P-1b: 候補 0 件時の除外理由集約 (excluded_summary)
# ---------------------------------------------------------------------------


def test_excluded_summary_capacity_full_when_no_candidates() -> None:
    """容量満杯 (既存 6 名) で候補 0 件のとき、excluded_summary に capacity_full が非空で出る."""
    office_id = uuid4()
    # 既存 6 名 (max_patients=6) で容量上限. 候補は 1 件も入らない.
    visits = [
        _v2(time(9, 30), time(10, 0), office_id=office_id, course_code="A"),
        _v2(time(10, 10), time(10, 40), office_id=office_id, course_code="A"),
        _v2(time(10, 50), time(11, 20), office_id=office_id, course_code="A"),
        _v2(time(13, 0), time(13, 30), office_id=office_id, course_code="A"),
        _v2(time(14, 0), time(14, 30), office_id=office_id, course_code="A"),
        _v2(time(15, 0), time(15, 30), office_id=office_id, course_code="A"),
    ]
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", visits)}
    excluded: list[ExcludedReasonSummary] = []
    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, _candidate(), office_ids=[office_id], exclusions_out=excluded
    )
    assert results == [], "容量満杯なので候補 0 件"
    assert excluded, "0 件時は excluded_summary が非空"
    reasons = {e.reason for e in excluded}
    assert "capacity_full" in reasons, f"capacity_full を含むべき: {reasons}"
    cap = next(e for e in excluded if e.reason == "capacity_full")
    assert cap.weekday == 0
    assert cap.sample_course_code == "A"
    assert cap.count >= 1


def test_excluded_summary_course_closed_when_no_bucket_for_weekday() -> None:
    """希望曜日に開講コースが 1 つも無い場合は course_closed が集約される."""
    office_id = uuid4()
    # 希望は月曜 (weekday 0) だが、bucket は存在しない (空) → course_closed.
    buckets: dict[tuple[UUID, int, str], _CourseBucket] = {}
    excluded: list[ExcludedReasonSummary] = []
    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, _candidate(), office_ids=[office_id], exclusions_out=excluded
    )
    assert results == []
    assert excluded
    assert any(e.reason == "course_closed" and e.weekday == 0 for e in excluded)


def test_excluded_summary_empty_when_candidates_exist() -> None:
    """候補が 1 件でもあれば excluded_summary は空 (表示要否は FE 判断)."""
    office_id = uuid4()
    v = _v2(time(10, 0), time(10, 30), office_id=office_id, course_code="A")
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", [v])}
    excluded: list[ExcludedReasonSummary] = []
    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, _candidate(), office_ids=[office_id], exclusions_out=excluded
    )
    assert results, "枠が出る前提"
    assert excluded == [], "候補があれば excluded_summary は空"
