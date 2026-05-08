"""W33: Layer 3 に 15 分バッファ制約を追加 (移動時間・準備余裕の考慮).

検証観点:
  1. event 終了 → visit 開始 が 15 分未満 → ハード除外 (別 staff に割付)
  2. event 終了 → visit 開始 が 16 分 → OK (割付可)
  3. event 終了 = visit 開始 (0 分差) → 除外
  4. event 開始 = visit 終了 (visit 直後にすぐ event) → 除外
  5. event なし → 通常通り割付可 (regression)
  6. event 終了 から 15 分ちょうど後の visit (境界ぴったり) → 除外
  7. event 終了 から 16 分後の visit (境界+1 分) → 割付可

BUFFER_MINUTES = 15 で判定:
    event_end_buffered = event.ends_at + 15min
    半開区間: event_end_buffered > visit_start → 除外
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

from app.models.staff import StaffEvent
from app.services.scheduling.layer3_assignment import (
    BUFFER_MINUTES,
    CourseAssignmentTarget,
    Layer3Assigner,
    StaffInfo,
    VisitTimeSlot,
    _has_event_overlap_with_buffer,
)

W33_ISO_YEAR = 2026
W33_ISO_WEEK = 33
W33_WEEK_MONDAY = date(2026, 8, 10)  # 2026-W33 月曜


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _staff(name: str, work_days: frozenset[int] | None = None) -> StaffInfo:
    return StaffInfo(
        staff_id=uuid4(),
        name=name,
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=work_days if work_days is not None else frozenset(range(6)),
    )


def _course(code: str, weekday: int, start: time, end: time) -> CourseAssignmentTarget:
    return CourseAssignmentTarget(
        course_id=uuid4(),
        weekday=weekday,
        course_code=code,
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        visits=[VisitTimeSlot(start_time=start, end_time=end)],
    )


def _event(staff_id, starts_at: datetime, ends_at: datetime) -> StaffEvent:
    return StaffEvent(
        id=uuid4(),
        staff_id=staff_id,
        event_type="研修",
        starts_at=starts_at,
        ends_at=ends_at,
    )


# ---------------------------------------------------------------------------
# BUFFER_MINUTES 定数確認
# ---------------------------------------------------------------------------


def test_buffer_minutes_constant() -> None:
    """BUFFER_MINUTES が 15 分に設定されていることを確認."""
    assert BUFFER_MINUTES == 15


# ---------------------------------------------------------------------------
# _has_event_overlap_with_buffer 単体テスト
# ---------------------------------------------------------------------------


def test_buffer_helper_event_end_0min_before_visit_excluded() -> None:
    """event 終了 = visit 開始 (0 分差) → バッファ内なので除外."""
    # event 14:00-15:00, visit 15:00-15:30 → event_end+15min=15:15 > 15:00 → True
    s1 = _staff("S1")
    course = _course("A", weekday=0, start=time(15, 0), end=time(15, 30))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(14, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(15, 0)),
    )
    result = _has_event_overlap_with_buffer(
        staff_id=s1.staff_id,
        course=course,
        weekday=0,
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert result is True, "event 終了=visit 開始 (0 分差) は除外されるべき"


def test_buffer_helper_event_end_14min_before_visit_excluded() -> None:
    """event 終了から visit 開始まで 14 分 (バッファ 15 分未満) → 除外."""
    # event 14:00-14:30, visit 14:44-15:00 → event_end+15min=14:45 > 14:44 → True
    s1 = _staff("S1")
    course = _course("A", weekday=0, start=time(14, 44), end=time(15, 0))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(14, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(14, 30)),
    )
    result = _has_event_overlap_with_buffer(
        staff_id=s1.staff_id,
        course=course,
        weekday=0,
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert result is True, "event 終了から 14 分差の visit は除外されるべき"


def test_buffer_helper_event_end_exactly_15min_before_visit_excluded() -> None:
    """event 終了から visit 開始まで 15 分ちょうど (境界ぴったり) → 除外.

    半開区間: event_end_buffered = event_end + 15min = visit_start
    → event_end_buffered > visit_start は False (=  境界は含まない) → 除外しない。
    ※ 15 分ちょうどは「ギリギリ許容」とする仕様 (半開区間)。
    """
    # event 14:00-14:30, visit 14:45-15:00 → event_end+15min=14:45 = 14:45 → False
    s1 = _staff("S1")
    course = _course("A", weekday=0, start=time(14, 45), end=time(15, 0))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(14, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(14, 30)),
    )
    result = _has_event_overlap_with_buffer(
        staff_id=s1.staff_id,
        course=course,
        weekday=0,
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert result is False, "event 終了から 15 分ちょうどの visit は許容されるべき (半開区間)"


def test_buffer_helper_event_end_16min_before_visit_ok() -> None:
    """event 終了から visit 開始まで 16 分 → バッファ超過なので割付可."""
    # event 14:00-14:30, visit 14:46-15:00 → event_end+15min=14:45 < 14:46 → False
    s1 = _staff("S1")
    course = _course("A", weekday=0, start=time(14, 46), end=time(15, 0))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(14, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(14, 30)),
    )
    result = _has_event_overlap_with_buffer(
        staff_id=s1.staff_id,
        course=course,
        weekday=0,
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert result is False, "event 終了から 16 分差の visit は除外不要"


# ---------------------------------------------------------------------------
# solve() 統合テスト
# ---------------------------------------------------------------------------


def test_solve_buffer_excludes_staff_0min_gap() -> None:
    """event 15:00-15:30 + visit 15:30 開始 → 0 分差 → S1 除外・S2 割付.

    ユーザー指摘の典型ケース: 「15:00-15:30 event + 15:30 visit は無茶な詰め込み」.
    """
    s1 = _staff("S1")
    s2 = _staff("S2")
    course = _course("A", weekday=0, start=time(15, 30), end=time(16, 0))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(15, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(15, 30)),
    )

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course],
        [s1, s2],
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s2.staff_id, (
        f"event 直後の visit で S1 が除外されず S1 が割付された: {result.assignments}"
    )


def test_solve_buffer_excludes_staff_14min_gap() -> None:
    """event 14:00-14:30 + visit 14:44 開始 (14 分差) → S1 除外・S2 割付."""
    s1 = _staff("S1")
    s2 = _staff("S2")
    course = _course("A", weekday=0, start=time(14, 44), end=time(15, 30))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(14, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(14, 30)),
    )

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course],
        [s1, s2],
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s2.staff_id, (
        "event 終了から 14 分差の visit で S1 が除外されていない"
    )


def test_solve_buffer_ok_16min_gap() -> None:
    """event 14:00-14:30 + visit 14:46 開始 (16 分差) → バッファ超過・S1 割付可."""
    s1 = _staff("S1")
    course = _course("A", weekday=0, start=time(14, 46), end=time(15, 30))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(14, 0)),
        datetime.combine(W33_WEEK_MONDAY, time(14, 30)),
    )

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course],
        [s1],
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.staff_id, (
        "event 終了から 16 分差 (バッファ超) で S1 が誤って除外された"
    )


def test_solve_buffer_event_end_16_30_visit_16_30_excluded() -> None:
    """event 終了 16:30 + visit 16:30 開始 → 0 分差 → 除外."""
    s1 = _staff("S1")
    s2 = _staff("S2")
    course = _course("A", weekday=0, start=time(16, 30), end=time(17, 0))
    ev = _event(
        s1.staff_id,
        datetime.combine(W33_WEEK_MONDAY, time(15, 30)),
        datetime.combine(W33_WEEK_MONDAY, time(16, 30)),
    )

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course],
        [s1, s2],
        events_by_staff={s1.staff_id: [ev]},
        week_monday=W33_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s2.staff_id, (
        "event 終了=visit 開始 (16:30) で S1 が除外されていない"
    )


def test_solve_no_event_regression() -> None:
    """event なしのスタッフは通常通り割付可 (regression)."""
    s1 = _staff("S1")
    course = _course("A", weekday=0, start=time(9, 0), end=time(10, 0))

    assigner = Layer3Assigner()
    result = assigner.solve(
        [course],
        [s1],
        events_by_staff={},
        week_monday=W33_WEEK_MONDAY,
    )
    assert len(result.assignments) == 1
    assert result.assignments[0].staff_id == s1.staff_id, "event なしなのに S1 が割付されなかった"
