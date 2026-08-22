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


def test_normalize_names_merges_kanji_variants() -> None:
    """異体字 (髙/高) 差も同一人物に束ねる (レビュー指摘: マスタ突合の正規化へ委譲)."""
    cur = CSV_HEADER + _kaipoke_row(user="髙梨　太郎", svc="A", start="09:00", end="10:00")
    opt = CSV_HEADER + _kaipoke_row(user="高梨太郎", svc="A", start="09:00", end="10:00")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert corrections == [], f"異体字で偽差分: {[(c.action, c.user_name) for c in corrections]}"


# ===========================================================================
# 2026-08-23 実データ (8/17 週 突合 46 件): 担当者名のスペース差 / 患者名の異体字
# ===========================================================================


def test_normalize_names_ignores_staff_spacing_variants() -> None:
    """担当者名「髙梨桂子」(らく助) と「髙梨　桂子」(カイポケ) は同一人物 → 偽 edit を出さない."""
    cur = CSV_HEADER + _kaipoke_row(user="前川　心愛", staff1="髙梨　桂子", svc="A")
    opt = CSV_HEADER + _kaipoke_row(user="前川　心愛", staff1="髙梨桂子", svc="A")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert corrections == [], f"偽差分が発生: {[(c.action, c.user_name) for c in corrections]}"


def test_normalize_names_still_detects_real_staff_change() -> None:
    """正規化しても別人への担当変更は edit として検出される."""
    cur = CSV_HEADER + _kaipoke_row(user="前川　心愛", staff1="髙梨　桂子", svc="A")
    opt = CSV_HEADER + _kaipoke_row(user="前川　心愛", staff1="宇田川　優莉", svc="A")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]


def test_normalize_names_merges_maki_variant() -> None:
    """患者名の異体字 槇/槙 (「槇 恵」vs「槙　恵」) は同一人物として束ねる."""
    cur = CSV_HEADER + _kaipoke_row(user="槙　恵", svc="A", start="12:00", end="12:35")
    opt = CSV_HEADER + _kaipoke_row(user="槇 恵", svc="A", start="12:00", end="12:35")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert corrections == []


# ===========================================================================
# S2 レビュー C1 (2026-08-23): サービス内容の一致は「双方向の前方一致」
#
# サービス内容の自動判定 (患者の訪問看護区分 x 職員1の資格) を入れると
# 4 通りの文字列が出る。旧実装の部分一致では
# "基本療養費I・正看" が "精神基本療養費I・正看" の部分文字列になるため、
# 一般の患者が精神科表記の行と結ばれて edit に化けていた。
# 正しくは delete+add (カイポケの edit ではサービス内容を直せないため)。
# ===========================================================================

PSY_NURSE = "精神基本療養費Ⅰ・正看"
PSY_ASSISTANT = "精神基本療養費Ⅰ・准看"
GEN_NURSE = "基本療養費Ⅰ・正看"


def _actions(corrections) -> list[str]:
    return sorted(c.action for c in corrections)


def test_general_patient_not_matched_to_psychiatric_service() -> None:
    """(A) 患者=一般・他は同一 → edit ではなく delete+add.

    旧実装では「基本療養費Ⅰ・正看」⊂「精神基本療養費Ⅰ・正看」の部分一致で
    Pass2 が結んでしまい、差分なしの edit すら出ない (= 送っても直らない) か
    中身のない edit になっていた。
    """
    cur = CSV_HEADER + _kaipoke_row(user="兼行　様", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="兼行　様", svc=GEN_NURSE)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert _actions(corrections) == ["add", "delete"], (
        f"delete+add にならない: {[(c.action, c.service_type) for c in corrections]}"
    )
    add = next(c for c in corrections if c.action == "add")
    assert add.service_type == GEN_NURSE
    delete = next(c for c in corrections if c.action == "delete")
    assert delete.service_type == PSY_NURSE


def test_general_patient_with_staff_change_is_not_edit() -> None:
    """(B) 一般 + 担当変更 → delete+add (edit にならない).

    担当だけ見れば edit だが、サービス内容が違う行は edit では直せない。
    """
    cur = CSV_HEADER + _kaipoke_row(user="兼行　様", staff1="宇田川　優莉", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="兼行　様", staff1="川名　千恵", svc=GEN_NURSE)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert _actions(corrections) == ["add", "delete"], (
        f"edit に化けた: {[(c.action, c.staff1_from, c.staff1_to) for c in corrections]}"
    )
    add = next(c for c in corrections if c.action == "add")
    assert add.staff1_to == "川名　千恵"
    assert add.service_type == GEN_NURSE


def test_assistant_nurse_service_is_not_matched_to_nurse() -> None:
    """(C) 職員1 が准看 → delete+add (正看の行と結ばない).

    「精神基本療養費Ⅰ・准看」と「精神基本療養費Ⅰ・正看」は前方一致もしない。
    """
    cur = CSV_HEADER + _kaipoke_row(user="山田　様", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="山田　様", svc=PSY_ASSISTANT)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert _actions(corrections) == ["add", "delete"], (
        f"正看/准看が同一視された: {[(c.action, c.service_type) for c in corrections]}"
    )
    assert next(c for c in corrections if c.action == "add").service_type == PSY_ASSISTANT


def test_service_prefix_growth_still_matches() -> None:
    """接尾が伸びただけ (「精神基本療養費Ⅰ」→「…・正看」) は従来どおり同一扱い.

    前方一致に絞っても、資格の接尾が付いただけのケースは結ばれる
    (= 時間だけ違えば edit)。ここが壊れると旧データの移行期に
    全件が delete+add に化ける。
    """
    cur = CSV_HEADER + _kaipoke_row(user="山田　様", svc="精神基本療養費Ⅰ", start="09:00")
    opt = CSV_HEADER + _kaipoke_row(user="山田　様", svc=PSY_NURSE, start="11:00")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].start_time_to == "11:00"
