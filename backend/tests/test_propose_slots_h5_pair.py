"""I-07 (H5 受入カレンダー warning) + I-11 (pair_mode=blocked 除外) 単体テスト.

propose-slots を diff-add と整合させる 2 つの不整合修正:

- I-07: 受入カレンダー × の (office, weekday, start) に該当する候補枠は **除外せず**
  ``acceptance_calendar`` 警告 + スコア降格する (N-6「なぜ出ないか分かるように」).
- I-11: pair_mode='blocked' のペア相手と同住所同時刻 (90分占有) で重なる候補枠を
  **除外** し、除外理由 ``pair_blocked`` を excluded_summary に集約する.

DB 非依存・純ロジックのみ (test_propose_slots_p1 と同じ千葉市近辺座標).
compute_all_proposed_slots に unavailable_slots / pair_modes を直接渡す.
"""

from __future__ import annotations

from datetime import time
from uuid import UUID, uuid4

from app.services.scheduling.auto_allocator_v2 import V2Visit
from app.services.scheduling.propose_slots_service import (
    CandidateInput,
    ExcludedReasonSummary,
    _CourseBucket,
    compute_all_proposed_slots,
)

BASE = (35.6000, 140.1000)
SAME = (35.60005, 140.10005)  # BASE と同住所 (<=100m)


def _v2(
    start: time,
    end: time,
    *,
    lat: float = BASE[0],
    lng: float = BASE[1],
    service: int = 30,
    office_id: UUID,
    course_code: str = "A",
    patient_id: UUID | None = None,
) -> V2Visit:
    return V2Visit(
        patient_id=patient_id or uuid4(),
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
    time_type: str = "終日",
    preferred_start: time | None = None,
    existing_patient_id: UUID | None = None,
) -> CandidateInput:
    return CandidateInput(
        lat=lat,
        lng=lng,
        service_minutes=service,
        time_type=time_type,
        preferred_start=preferred_start,
        preferred_end=None,
        preferred_weekdays=frozenset({0}),
        requires_multiple_staff=False,
        existing_patient_id=existing_patient_id,
    )


# ---------------------------------------------------------------------------
# I-07: 受入カレンダー × (acceptance_calendar warning)
# ---------------------------------------------------------------------------


def test_acceptance_blocked_slot_gets_warning_and_demotion_not_excluded() -> None:
    """受入不可 (office, weekday, start) の枠は除外されず acceptance_calendar 警告 + 降格."""
    office_id = uuid4()
    # 固定 10:00 の候補. 空コースなので 10:00 の単独枠が 1 件出る.
    v = _v2(time(13, 0), time(13, 30), office_id=office_id, course_code="A")
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", [v])}
    cand = _candidate(time_type="固定", preferred_start=time(10, 0))

    # 受入 × に 10:00 を設定 → 10:00 の枠に警告が付く (除外はされない).
    unavailable = {(office_id, 0): {time(10, 0)}}
    blocked_results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, cand, office_ids=[office_id], unavailable_slots=unavailable
    )
    assert blocked_results, "受入× でも枠は除外されず出る (warning 方式)"
    ten = next((r for r in blocked_results if r.start == time(10, 0)), None)
    assert ten is not None, "10:00 の枠が残っているはず"
    assert "acceptance_calendar" in ten.warnings, f"警告が付くはず: {ten.warnings}"

    # 受入× なしの同一シナリオと比べてスコアが 60 降格している.
    clean_results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, cand, office_ids=[office_id]
    )
    clean_ten = next(r for r in clean_results if r.start == time(10, 0))
    assert "acceptance_calendar" not in clean_ten.warnings
    assert abs(ten.score - (clean_ten.score - 60.0)) < 1e-6, (
        f"降格幅は staff_absent と同じ 60.0: blocked={ten.score} clean={clean_ten.score}"
    )


def test_acceptance_available_weekday_has_no_warning() -> None:
    """受入 × 時刻に該当しない枠 (別時刻 / 別曜日 / 未設定) は警告なし (既存挙動不変)."""
    office_id = uuid4()
    v = _v2(time(13, 0), time(13, 30), office_id=office_id, course_code="A")
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", [v])}
    cand = _candidate(time_type="固定", preferred_start=time(10, 0))

    # × は 11:00 (候補は 10:00) / さらに weekday=1 なので (office,0) には効かない.
    unavailable = {(office_id, 0): {time(11, 0)}, (office_id, 1): {time(10, 0)}}
    results = compute_all_proposed_slots(
        buckets, {office_id: "O"}, cand, office_ids=[office_id], unavailable_slots=unavailable
    )
    assert results
    ten = next(r for r in results if r.start == time(10, 0))
    assert "acceptance_calendar" not in ten.warnings, f"該当なしは警告なし: {ten.warnings}"


# ---------------------------------------------------------------------------
# I-11: pair_mode='blocked' の同住所同時刻ペア除外
# ---------------------------------------------------------------------------


def test_pair_blocked_excludes_pair_slot_and_surfaces_reason() -> None:
    """blocked ペア相手と同住所同時刻の枠が除外され excluded_summary に pair_blocked."""
    office_id = uuid4()
    cand_pid = uuid4()
    partner_pid = uuid4()
    # 相手は 10:00 の同住所単独訪問. 候補は固定 10:00 (同住所) → 同時刻ペアのみ可能.
    partner = _v2(
        time(10, 0),
        time(10, 30),
        lat=SAME[0],
        lng=SAME[1],
        office_id=office_id,
        course_code="A",
        patient_id=partner_pid,
    )
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", [partner])}
    cand = _candidate(
        lat=SAME[0],
        lng=SAME[1],
        time_type="固定",
        preferred_start=time(10, 0),
        existing_patient_id=cand_pid,
    )
    pair_modes = {(cand_pid, partner_pid): "blocked"}
    excluded: list[ExcludedReasonSummary] = []
    results = compute_all_proposed_slots(
        buckets,
        {office_id: "O"},
        cand,
        office_ids=[office_id],
        pair_modes=pair_modes,
        exclusions_out=excluded,
    )
    assert results == [], "blocked ペアの同時刻枠は除外され候補 0 件"
    assert any(e.reason == "pair_blocked" for e in excluded), (
        f"pair_blocked が excluded_summary に出るべき: {[e.reason for e in excluded]}"
    )


def test_pair_preferred_and_unset_do_not_exclude() -> None:
    """pair_mode 未設定 / preferred では同住所ペア枠が除外されない (既存挙動不変)."""
    office_id = uuid4()
    cand_pid = uuid4()
    partner_pid = uuid4()
    partner = _v2(
        time(10, 0),
        time(10, 30),
        lat=SAME[0],
        lng=SAME[1],
        office_id=office_id,
        course_code="A",
        patient_id=partner_pid,
    )
    buckets = {(office_id, 0, "A"): _bucket(office_id, "A", [partner])}
    cand = _candidate(
        lat=SAME[0],
        lng=SAME[1],
        time_type="固定",
        preferred_start=time(10, 0),
        existing_patient_id=cand_pid,
    )

    # (a) pair_modes 未指定 → ペア枠が出る.
    unset = compute_all_proposed_slots(buckets, {office_id: "O"}, cand, office_ids=[office_id])
    assert any(r.is_pair for r in unset), "未設定なら同住所ペア枠が出る"

    # (b) preferred 明示 → ペア枠が出る (blocked 以外は除外しない).
    pref_results = compute_all_proposed_slots(
        buckets,
        {office_id: "O"},
        cand,
        office_ids=[office_id],
        pair_modes={(cand_pid, partner_pid): "preferred"},
    )
    assert any(r.is_pair for r in pref_results), "preferred なら同住所ペア枠が出る"
