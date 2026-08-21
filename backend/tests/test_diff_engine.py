"""Regression tests for diff engine.

Ports `PlaywrightTest1/lib/test_diff_engine.py` to the CareLink backend
package (Phase 4-6/4-7/4-16, W1-E). Covers Codex review Bug C (C-10
month-spanning week wrap) and Bug D (canonical-day-key equality).

Run from backend/::

    pytest tests/test_diff_engine.py -v
"""

from __future__ import annotations

import pytest

from app.services.diff import compare_schedules_from_content

CSV_HEADER = (
    "職員名1,職種1,職員名2,職種2,同行2,職員名3,職種3,同行3,"
    "事業所名,日付,曜日,利用者,業務種別,サービス内容,"
    "開始時間,終了時間,提供時間,備考\n"
)


def _kaipoke_row(
    *,
    staff1: str = "A田",
    staff2: str = "",
    date: str = "1",
    weekday: str = "月",
    user: str = "P1",
    business: str = "医療保険",
    svc: str = "訪問看護I",
    start: str = "09:00",
    end: str = "10:00",
    remarks: str = "",
) -> str:
    return (
        f"{staff1},看護師,{staff2},,,,,,"  # 8 cols
        f"事業所A,{date},{weekday},{user},{business},{svc},"
        f"{start},{end},60,{remarks}\n"
    )


# ===========================================================================
# Bug C: month-spanning week ranges (e.g. 29..5)
# ===========================================================================


@pytest.mark.parametrize(
    "raw_date,start,end,expected_kept",
    [
        # Wrap range 29..5 — May has 31 days, week spans 29 May → 4 Jun
        ("30", 29, 5, True),  # day 30 inside [29..31] tail
        ("31", 29, 5, True),  # day 31 inside [29..31] tail
        ("2", 29, 5, True),  # day  2 inside [1..5]   head
        ("5", 29, 5, True),  # day  5 inside [1..5]   head (boundary)
        ("29", 29, 5, True),  # boundary start
        # Out-of-wrap days must still be excluded
        ("6", 29, 5, False),
        ("28", 29, 5, False),
        # yyyy/MM/dd formats also wrap correctly
        ("2026/05/30", 29, 5, True),
        ("2026/06/02", 29, 5, True),
        ("2026/06/06", 29, 5, False),
        # Non-wrap (start<=end) sanity preserved
        ("3", 1, 7, True),
        ("8", 1, 7, False),
    ],
)
def test_bug_c_wrap_range_filter(raw_date: str, start: int, end: int, expected_kept: bool) -> None:
    """`compare_schedules_from_content` keeps only rows whose day-of-month
    falls inside [start..end], wrapping when start > end."""
    cur = CSV_HEADER + _kaipoke_row(date=raw_date, user="P1", svc="A")
    opt = CSV_HEADER + _kaipoke_row(
        date=raw_date,
        user="P1",
        svc="A",
        start="10:00",
        end="11:00",
    )
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=start,
        target_week_end=end,
    )
    if expected_kept:
        assert any(c.action == "edit" for c in corrections), (
            f"Bug C: wrap range {start}..{end} dropped day '{raw_date}'"
        )
    else:
        assert corrections == [], (
            f"Bug C: wrap range {start}..{end} kept out-of-range day '{raw_date}': {corrections}"
        )


def test_bug_c_wrap_range_aggregate() -> None:
    """A whole week with 5 entries spread across a month boundary survives
    the filter — every entry's day belongs in [29..5]."""
    rows = ""
    days = ["29", "30", "31", "1", "2"]
    for i, d in enumerate(days):
        rows += _kaipoke_row(
            date=d,
            user=f"P{i}",
            svc=f"S{i}",
            start="09:00",
            end="10:00",
        )
    cur = CSV_HEADER + rows
    # Optimized differs in time on every entry → 5 edits expected
    opt_rows = ""
    for i, d in enumerate(days):
        opt_rows += _kaipoke_row(
            date=d,
            user=f"P{i}",
            svc=f"S{i}",
            start="11:00",
            end="12:00",
        )
    opt = CSV_HEADER + opt_rows
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=29,
        target_week_end=5,
    )
    edits = [c for c in corrections if c.action == "edit"]
    assert len(edits) == 5, (
        f"Bug C: expected 5 edits across the wrap week, got {len(edits)} "
        f"({[c.user_name for c in edits]})"
    )


# ===========================================================================
# Bug D: mixed date-format equality (canonical key)
# ===========================================================================


def test_bug_d_yyyy_mm_dd_vs_day_only_no_false_date_change() -> None:
    """Current row dated `2026/05/04` and optimized row dated `4` must be
    treated as the same day. Prior to the fix this generated a spurious
    `date_change` correction. After the fix, only an `edit` (time change)
    should appear."""
    cur = CSV_HEADER + _kaipoke_row(
        date="2026/05/04",
        user="P1",
        svc="A",
        start="09:00",
        end="10:00",
    )
    opt = CSV_HEADER + _kaipoke_row(
        date="4",
        user="P1",
        svc="A",
        start="11:00",
        end="12:00",
    )
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=1,
        target_week_end=7,
    )
    actions = sorted(c.action for c in corrections)
    assert "date_change" not in actions, (
        f"Bug D: false date_change emitted for same-day mixed formats: {corrections}"
    )
    assert "edit" in actions, f"Bug D: expected an edit (time change) but got: {corrections}"


def test_bug_d_identical_schedules_in_mixed_formats_yield_no_corrections() -> None:
    """Two identical schedules expressed in different date formats must
    produce zero corrections — neither edit nor date_change."""
    cur = CSV_HEADER + _kaipoke_row(
        date="2026/05/04",
        user="P1",
        svc="A",
        start="09:00",
        end="10:00",
    )
    opt = CSV_HEADER + _kaipoke_row(
        date="4",
        user="P1",
        svc="A",
        start="09:00",
        end="10:00",
    )
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=1,
        target_week_end=7,
    )
    assert corrections == [], (
        f"Bug D: identical schedules in mixed formats produced corrections: {corrections}"
    )


def test_bug_d_genuine_date_change_still_detected() -> None:
    """Negative control: a true date change (2 → 4) must still surface as
    a `date_change` action, even when one side uses yyyy/MM/dd."""
    cur = CSV_HEADER + _kaipoke_row(
        date="2",
        user="P1",
        svc="A",
        start="09:00",
        end="10:00",
    )
    opt = CSV_HEADER + _kaipoke_row(
        date="2026/05/04",
        user="P1",
        svc="A",
        start="09:00",
        end="10:00",
    )
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=1,
        target_week_end=7,
    )
    assert any(c.action == "date_change" for c in corrections), (
        f"Bug D: genuine date_change (2→4) was lost: {corrections}"
    )


# ===========================================================================
# 週空間C2 (2026-08-21): 氏名スペース差の正規化 — outbound でも偽ペアを作らない
# ===========================================================================


def test_normalize_names_merges_spacing_variants() -> None:
    """「今井 康敦」(半角スペース) と「今井　康敦」(全角スペース) が同一人物として
    束ねられ、同時刻の行が偽の delete+add ペアに割れない (C2実機テストの実障害)。"""
    cur = CSV_HEADER + _kaipoke_row(user="今井　康敦", svc="A", start="14:30", end="15:05")
    opt = CSV_HEADER + _kaipoke_row(user="今井 康敦", svc="A", start="14:30", end="15:05")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert corrections == [], f"偽差分が発生: {[(c.action, c.user_name) for c in corrections]}"


def test_normalize_names_staff_diff_becomes_edit_not_pair() -> None:
    """氏名スペース差 + 担当違い → delete+add ではなく edit 1 件に畳まれる."""
    cur = CSV_HEADER + _kaipoke_row(user="今井　康敦", staff1="宇田川　優莉", svc="A")
    opt = CSV_HEADER + _kaipoke_row(user="今井 康敦", staff1="髙梨桂子", svc="A")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert len(corrections) == 1
    assert corrections[0].action == "edit"
    # 表示名は現況(カイポケ)側の原文 = RPA の利用者検索がそのまま成立する
    assert corrections[0].user_name == "今井　康敦"


def test_date_change_prefers_nearest_day() -> None:
    """日付変更の相手は最近接日を選ぶ (先着順だと遠い日と結ばれ出方が揺れる)."""
    cur = CSV_HEADER + _kaipoke_row(date="2", user="P1", svc="A", start="09:00", end="10:00")
    opt = (
        CSV_HEADER
        + _kaipoke_row(date="6", user="P1", svc="A", start="09:00", end="10:00")
        + _kaipoke_row(date="3", user="P1", svc="A", start="09:00", end="10:00")
    )
    corrections = compare_schedules_from_content(cur, opt, target_week_start=1, target_week_end=7)
    dc = [c for c in corrections if c.action == "date_change"]
    assert len(dc) == 1
    assert dc[0].date_to == "3", f"最近接日(3)でなく{dc[0].date_to}が選ばれた"
    # 残り (6日) は追加として出る
    assert any(c.action == "add" and c.date_to == "6" for c in corrections)
