"""イベント考慮2段階提案 (PO確定 2026-07-27) のソルバ単体テスト.

仕様:
    - パスA: 全イベント (前後 EVENT_BUFFER_MINUTES=15 分バッファ込み) を占有として
      クリーン枠を探す. 1件でもあればクリーン枠のみ返す (event_conflicts 空).
    - パスAが0件のときだけパスB: blocking イベントのみ占有で再走査し、ソフト
      イベントと重なる枠へ event_conflicts タグを付けて返す.
    - blocking イベントはパスBでも占有 (衝突提案を出さない).
    - ゼロ長イベント (メモ) は無視. 容量 (件数/分) にイベントは算入しない.
"""

from __future__ import annotations

from datetime import time

from app.services.scheduling.proposal_solver import (
    EVENT_BUFFER_MINUTES,
    Candidate,
    EventWindow,
    ExistingVisit,
    find_available_slots_for_candidate,
    slot_fits_exact,
)

BASE = (35.6000, 140.1000)
P_1_4KM = (35.6100, 140.1100)
SAME = (35.60005, 140.10005)


def _overlaps(s: time, e: time, ws: time, we: time) -> bool:
    return s < we and e > ws


def test_clean_pass_avoids_event_with_buffer() -> None:
    """空きコース + ソフトイベント 10:00-11:00 → クリーン枠はバッファ込み区間
    (09:45-11:15) を避けて出る (例: 11:15 開始). event_conflicts は空."""
    cand = Candidate(*BASE, service_minutes=30, time_type="終日", patient_id="X")
    events = [EventWindow(start=time(10, 0), end=time(11, 0), title="会議")]
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, event_windows=events
    )
    assert slots, "イベント外の時間に枠があるはず"
    assert EVENT_BUFFER_MINUTES == 15, "以下の期待値は 15 分バッファ前提"
    buffered_start = time(9, 45)
    buffered_end = time(11, 15)
    for s in slots:
        assert not _overlaps(s.start, s.end, buffered_start, buffered_end), (
            f"クリーン枠がイベント(バッファ込み 09:45-11:15)と重なった: {s.start}-{s.end}"
        )
        assert s.event_conflicts == (), "クリーン枠に衝突タグが付いてはいけない"
    assert any(s.start == time(11, 15) for s in slots), (
        f"イベント終了+バッファ (11:15) 開始の枠が出るはず: {[str(s.start) for s in slots]}"
    )


def test_fallback_tags_soft_event_conflicts() -> None:
    """AM 全体を覆うソフトイベント + 午前限定候補 → パスA 0件 → パスBで
    イベントを無視した枠が event_conflicts 付きで出る."""
    cand = Candidate(*BASE, service_minutes=30, time_type="午前", patient_id="X")
    events = [EventWindow(start=time(9, 30), end=time(12, 0), title="研修")]
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, event_windows=events
    )
    assert slots, "フォールバックで枠が出るはず"
    for s in slots:
        assert s.event_conflicts, "フォールバック枠には衝突タグが必要"
        assert s.event_conflicts[0].title == "研修"
        assert s.event_conflicts[0].start == time(9, 30)


def test_blocking_event_never_proposed_over() -> None:
    """blocking イベントが AM 全体を覆う + 午前限定候補 → フォールバックでも 0 件."""
    cand = Candidate(*BASE, service_minutes=30, time_type="午前", patient_id="X")
    events = [EventWindow(start=time(9, 30), end=time(12, 0), title="重要", blocking=True)]
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, event_windows=events
    )
    assert slots == [], "blocking イベントの上に提案してはいけない"


def test_mixed_blocking_and_soft() -> None:
    """AM=ソフト研修 / PM=blocking 重要イベント (終日候補) → パスA 0件 →
    パスBは blocking の PM を守り、AM のみ衝突タグ付きで提案する."""
    cand = Candidate(*BASE, service_minutes=30, time_type="終日", patient_id="X")
    events = [
        EventWindow(start=time(9, 30), end=time(12, 0), title="研修"),
        EventWindow(start=time(13, 0), end=time(18, 0), title="重要", blocking=True),
    ]
    slots = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, event_windows=events
    )
    assert slots, "AM 側にフォールバック枠が出るはず"
    for s in slots:
        assert s.block == "am", f"blocking の PM に枠が出た: {s.start}"
        assert s.event_conflicts and s.event_conflicts[0].title == "研修"


def test_zero_length_memo_is_ignored() -> None:
    """ゼロ長イベント (メモ) は占有にならない — イベント無しと同じ結果."""
    cand = Candidate(*BASE, service_minutes=30, time_type="終日", patient_id="X")
    memo = [EventWindow(start=time(10, 0), end=time(10, 0), title="メモ")]
    with_memo = find_available_slots_for_candidate(
        [], cand, lunch_window=None, weekday=0, event_windows=memo
    )
    without = find_available_slots_for_candidate([], cand, lunch_window=None, weekday=0)
    assert [(s.start, s.end) for s in with_memo] == [(s.start, s.end) for s in without]
    assert all(s.event_conflicts == () for s in with_memo)


def test_events_do_not_consume_capacity() -> None:
    """イベントは容量 (件数) に算入しない: 既存5件 (上限6) + イベントでも枠が出る."""
    existing = [
        ExistingVisit(time(9, 30), time(9, 50), *BASE, service_minutes=20, patient_id=f"p{i}")
        for i in range(5)
    ]
    # 5件が同時刻に固まるのは不自然だが、容量カウント (len) の検証には十分.
    cand = Candidate(*BASE, service_minutes=20, time_type="午後", patient_id="X")
    events = [EventWindow(start=time(13, 0), end=time(13, 30), title="会議")]
    slots = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, event_windows=events
    )
    assert slots, "イベントが容量を食って capacity_full になってはいけない"


def test_same_address_pair_slot_filtered_by_event() -> None:
    """同住所ペア枠 (既存10:00の隣に同時刻90分占有) がイベントと重なる場合、
    クリーン枠が他にある限りペア枠は出さない."""
    existing = [
        ExistingVisit(time(10, 0), time(10, 30), *BASE, service_minutes=30, patient_id="A"),
    ]
    cand = Candidate(*SAME, service_minutes=30, time_type="終日", patient_id="B")
    events = [EventWindow(start=time(10, 30), end=time(11, 30), title="会議")]
    slots = find_available_slots_for_candidate(
        existing, cand, lunch_window=None, weekday=0, event_windows=events
    )
    assert slots, "PM などにクリーン枠があるはず"
    for s in slots:
        assert not s.same_address_pair, (
            "ペア占有 (10:00-11:30) はイベント(バッファ込み)と重なるため出してはいけない"
        )
        assert s.event_conflicts == ()


def test_slot_fits_exact_respects_events() -> None:
    """slot_fits_exact: enforce_soft_events=True でイベント重複Tを拒否、
    False (フォールバック相当) ではソフトイベントのみなら許容する."""
    cand = Candidate(*BASE, service_minutes=30, time_type="終日", patient_id="X")
    soft = [EventWindow(start=time(10, 0), end=time(11, 0), title="会議")]
    hard = [EventWindow(start=time(10, 0), end=time(11, 0), title="重要", blocking=True)]
    assert not slot_fits_exact([], cand, time(10, 0), lunch_window=None, event_windows=soft), (
        "全イベント強制でイベント中のTは不可"
    )
    assert slot_fits_exact(
        [], cand, time(10, 0), lunch_window=None, event_windows=soft, enforce_soft_events=False
    ), "ソフトのみ・非強制なら許容 (フォールバック相当)"
    assert not slot_fits_exact(
        [], cand, time(10, 0), lunch_window=None, event_windows=hard, enforce_soft_events=False
    ), "blocking は非強制でも不可"
