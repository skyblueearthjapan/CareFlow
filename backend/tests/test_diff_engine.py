"""Regression tests for diff engine.

Ports `PlaywrightTest1/lib/test_diff_engine.py` to the CareLink backend
package (Phase 4-6/4-7/4-16, W1-E). Covers Codex review Bug C (C-10
month-spanning week wrap) and Bug D (canonical-day-key equality).

Run from backend/::

    pytest tests/test_diff_engine.py -v
"""

from __future__ import annotations

import pytest

from app.services.diff import compare_schedules_from_content, service_grade

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
    """(C) 職員1 が准看 → 正看の行と **同一視しない** (差分として必ず出る).

    「精神基本療養費Ⅰ・准看」と「精神基本療養費Ⅰ・正看」は前方一致もしないので
    ``_service_matches`` は False のまま (ここは不変)。

    2026-09-11 から **出方が変わった**: 同じ日・同じ開始時刻なら Pass 3.5 が
    1 件の ``edit`` (請求区分変更) に束ねる。以前は対になっていない delete+add
    で、add だけ失敗すると予定が消えた (9/7 週の本番 21 件)。
    「送っても直らない差分にしない」という (C) の主旨はそのまま — 送る値は
    らく助側 (准看) になる。
    """
    cur = CSV_HEADER + _kaipoke_row(user="山田　様", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="山田　様", svc=PSY_ASSISTANT)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert _actions(corrections) == ["edit"], (
        f"想定外の出方: {[(c.action, c.service_type) for c in corrections]}"
    )
    assert corrections[0].grade_change is True
    assert corrections[0].service_type == PSY_ASSISTANT
    assert corrections[0].service_type_from == PSY_NURSE


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


# ===========================================================================
# 請求区分 (正看/准看) が変わる行の扱い (2026-09-11)
#
# 2026-09-03 W37 送信の実害: 担当を 髙梨(正看) → 高岡(准看) に変えた行が
# ``edit`` として送られ、カイポケ側は **担当だけ入れ替わってサービス内容は
# 正看のまま** 残った (6 件・請求に影響)。カイポケの編集ダイアログはサービス
# 内容を変更できない (設計 §3-1)。
#
# 対処は「1 件のまま送る」: ``grade_change=True`` を立て、``service_type`` を
# **らく助側の値**、``service_type_from`` に **カイポケ現況の値** を入れる。
# RPA はこの印の行を内部で「削除 → 再追加」として処理し、再追加時に
# ``service_type`` を書く (失敗時は ``service_type_from`` で元へ戻す)。
# 差分側で delete+add に割らないのは、RPA が 1 件ずつ独立に処理し (ペアも
# ロールバックも無い)、add だけ失敗すると予定が消えたまま残るため
# (8/31 の欠落と同じ形)。
# ===========================================================================

PSY_BARE = "精神基本療養費Ⅰ"  # 資格の接尾が無い旧表記 (カイポケ側に実在)


def test_service_grade_helper() -> None:
    """``service_grade`` は「准看」を含むかどうかだけで区分を決める (空は None)."""
    assert service_grade(PSY_ASSISTANT) == "准看"
    assert service_grade("基本療養費Ⅰ・准看") == "准看"
    assert service_grade(PSY_NURSE) == "正看"
    assert service_grade(GEN_NURSE) == "正看"
    # 接尾の無い旧表記はカイポケ既定の正看として扱う (前方一致で edit に結ばれる側)
    assert service_grade(PSY_BARE) == "正看"
    assert service_grade("") is None
    assert service_grade("   ") is None


def test_grade_change_staff_swap_stays_edit_with_optimized_service() -> None:
    """(a) 髙梨(正看) → 高岡(准看)・同じ日/同じ時刻 → edit 1 件.

    印が立ち、``service_type`` は **らく助側 (准看)**、``service_type_from`` は
    **カイポケ現況**。送る値を現況側のままにすると、RPA の再追加で古い資格が
    書き戻されて請求が狂う。
    """
    cur = CSV_HEADER + _kaipoke_row(
        user="齋藤　様", staff1="髙梨　桂子", svc=PSY_BARE, start="09:30", end="10:05"
    )
    opt = CSV_HEADER + _kaipoke_row(
        user="齋藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT, start="09:30", end="10:05"
    )
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"], (
        f"1 件の edit にならない: {[(c.action, c.service_type) for c in corrections]}"
    )
    edit = corrections[0]
    assert edit.grade_change is True
    assert edit.service_type == PSY_ASSISTANT, "送るサービス内容はらく助側でなければならない"
    assert edit.service_type_from == PSY_BARE, "復旧用に現況の値を残すこと"
    assert edit.staff1_from == "髙梨　桂子"
    assert edit.staff1_to == "高岡　真由美"


def test_grade_change_assistant_to_nurse_stays_edit_with_optimized_service() -> None:
    """(b) 逆向き (高岡 准看 → 熊澤 正看) も edit 1 件・らく助側の値."""
    cur = CSV_HEADER + _kaipoke_row(user="岡村　様", staff1="高岡　真由美", svc=PSY_ASSISTANT)
    opt = CSV_HEADER + _kaipoke_row(user="岡村　様", staff1="熊澤　真理奈", svc=PSY_BARE)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is True
    assert corrections[0].service_type == PSY_BARE
    assert corrections[0].service_type_from == PSY_ASSISTANT


def test_suffixed_grade_pair_same_slot_becomes_single_edit() -> None:
    """(a') 双方が資格まで書かれている (前方一致しない) 行も 1 件の edit に束ねる.

    同じ日・同じ利用者・同じ開始時刻で区分だけ違う組は Pass 3.5 で対にする。
    放っておくと **対になっていない delete+add** になり、add だけ失敗すると
    予定が消える (9/7 週の本番 21 件がこの形)。担当も一緒に直る。
    """
    cur = CSV_HEADER + _kaipoke_row(user="齋藤　様", staff1="髙梨　桂子", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="齋藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"], (
        f"1 件に束ねられない: {[(c.action, c.service_type) for c in corrections]}"
    )
    edit = corrections[0]
    assert edit.grade_change is True
    assert edit.service_type == PSY_ASSISTANT
    assert edit.service_type_from == PSY_NURSE
    assert edit.staff1_from == "髙梨　桂子"
    assert edit.staff1_to == "高岡　真由美"


def test_suffixed_grade_pair_same_staff_same_time_becomes_single_edit() -> None:
    """(a'') 担当も時刻も同じで区分だけ違う行も 1 件の edit (放置すると直らない)."""
    cur = CSV_HEADER + _kaipoke_row(user="齋藤　様", staff1="高岡　真由美", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="齋藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is True
    assert corrections[0].service_type == PSY_ASSISTANT
    assert corrections[0].service_type_from == PSY_NURSE


def test_suffixed_grade_pair_with_different_start_stays_delete_add() -> None:
    """(a''') 開始時刻まで違う組は「同じ訪問」と言い切れない → 従来どおり delete+add.

    印 (表示用) だけ付ける。``service_type_from`` は空 — それぞれの行が自分の
    側の値を持っているため。
    """
    cur = CSV_HEADER + _kaipoke_row(user="齋藤　様", svc=PSY_NURSE, start="09:00", end="09:35")
    opt = CSV_HEADER + _kaipoke_row(user="齋藤　様", svc=PSY_ASSISTANT, start="14:00", end="14:35")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["delete", "add"]
    assert corrections[0].service_type == PSY_NURSE
    assert corrections[1].service_type == PSY_ASSISTANT
    assert all(c.service_type_from == "" for c in corrections)


def test_suffixed_grade_pair_not_merged_when_flag_is_off() -> None:
    """門が閉じている (flag_grade_change=False) 間は従来どおり delete+add・印なし."""
    cur = CSV_HEADER + _kaipoke_row(user="齋藤　様", staff1="髙梨　桂子", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="齋藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT)
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=1,
        target_week_end=7,
        normalize_names=True,
        flag_grade_change=False,
    )
    assert [c.action for c in corrections] == ["delete", "add"]
    assert not any(c.grade_change for c in corrections)


def test_same_grade_staff_change_stays_edit() -> None:
    """(c) 区分が変わらない担当変更 (髙梨 → 川名・どちらも正看) は印を立てない."""
    cur = CSV_HEADER + _kaipoke_row(user="園田　様", staff1="髙梨　桂子", svc=PSY_NURSE)
    opt = CSV_HEADER + _kaipoke_row(user="園田　様", staff1="川名　千恵", svc=PSY_NURSE)
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is False
    assert corrections[0].service_type == PSY_NURSE
    assert corrections[0].service_type_from == ""
    assert corrections[0].staff1_to == "川名　千恵"


def test_time_only_change_same_grade_stays_edit() -> None:
    """(d) 時刻だけの変更 (区分は同じ) は edit・印なし."""
    cur = CSV_HEADER + _kaipoke_row(user="河野　様", svc=PSY_NURSE, start="09:00", end="09:35")
    opt = CSV_HEADER + _kaipoke_row(user="河野　様", svc=PSY_NURSE, start="11:00", end="11:35")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is False
    assert corrections[0].start_time_to == "11:00"


def test_date_change_with_grade_change_keeps_action_and_takes_optimized_service() -> None:
    """(e) 日付変更 + 区分変更 → date_change のまま・印あり・らく助側の値.

    RPA は date_change も内部で「削除 → 再追加」するので、``service_type`` が
    現況側のままだと **古い資格で再追加される**。ここがこの修正の肝。
    """
    cur = CSV_HEADER + _kaipoke_row(user="藤江　様", date="3", svc=PSY_BARE, start="12:00")
    opt = CSV_HEADER + _kaipoke_row(
        user="藤江　様", date="4", svc=PSY_ASSISTANT, staff1="高岡　真由美", start="12:00"
    )
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["date_change"]
    assert corrections[0].grade_change is True
    assert corrections[0].service_type == PSY_ASSISTANT
    assert corrections[0].service_type_from == PSY_BARE
    assert corrections[0].date_from == "3"
    assert corrections[0].date_to == "4"


def test_date_change_same_grade_stays_date_change() -> None:
    """(e') 区分が変わらない日付変更は印なし (退行防止)."""
    cur = CSV_HEADER + _kaipoke_row(user="藤江　様", date="3", svc=PSY_NURSE, start="12:00")
    opt = CSV_HEADER + _kaipoke_row(user="藤江　様", date="4", svc=PSY_NURSE, start="12:00")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["date_change"]
    assert corrections[0].grade_change is False
    assert corrections[0].service_type_from == ""


def test_time_change_with_grade_change_keeps_edit_and_takes_optimized_service() -> None:
    """(g) 担当変更 + 時刻変更 + 区分変更 → edit のまま・印あり・らく助側の値."""
    cur = CSV_HEADER + _kaipoke_row(
        user="武藤　様", staff1="髙梨　桂子", svc=PSY_BARE, start="09:00", end="09:35"
    )
    opt = CSV_HEADER + _kaipoke_row(
        user="武藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT, start="11:00", end="11:35"
    )
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is True
    assert corrections[0].service_type == PSY_ASSISTANT
    assert corrections[0].service_type_from == PSY_BARE
    assert corrections[0].start_time_to == "11:00"


def test_end_time_only_change_with_grade_change_keeps_edit() -> None:
    """(g') 終了時刻だけ動く行も同じ扱い (edit・印あり・らく助側の値)."""
    cur = CSV_HEADER + _kaipoke_row(
        user="武藤　様", staff1="髙梨　桂子", svc=PSY_BARE, start="09:00", end="09:35"
    )
    opt = CSV_HEADER + _kaipoke_row(
        user="武藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT, start="09:00", end="10:05"
    )
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is True
    assert corrections[0].service_type == PSY_ASSISTANT
    assert corrections[0].service_type_from == PSY_BARE


def test_inbound_direction_never_flags_or_rewrites_service() -> None:
    """(f) inbound (flag_grade_change=False) は印も差し替えもしない.

    らく助は訪問ごとのサービス内容を持たないので、取り込み方向では意味が無い。
    向きの指定は ``local_diff`` が渡す。
    """
    cur = CSV_HEADER + _kaipoke_row(user="武藤　様", staff1="髙梨　桂子", svc=PSY_BARE)
    opt = CSV_HEADER + _kaipoke_row(user="武藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT)
    corrections = compare_schedules_from_content(
        cur,
        opt,
        target_week_start=1,
        target_week_end=7,
        normalize_names=True,
        flag_grade_change=False,
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is False
    assert corrections[0].service_type == PSY_BARE  # 現況側のまま
    assert corrections[0].service_type_from == ""


def test_event_rows_without_service_are_not_flagged() -> None:
    """サービス内容が空の行 (イベント) は区分不明 → 印なし."""
    cur = CSV_HEADER + _kaipoke_row(user="朝会", business="個別業務", svc="", staff1="髙梨　桂子")
    opt = CSV_HEADER + _kaipoke_row(user="朝会", business="個別業務", svc="", staff1="高岡　真由美")
    corrections = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )
    assert [c.action for c in corrections] == ["edit"]
    assert corrections[0].grade_change is False


def test_rpa_payload_carries_grade_change_and_service_type_from() -> None:
    """RPA へ渡す平坦形式に ``grade_change`` と ``service_type_from`` が載る.

    RPA 側は ``Correction(**item)`` で復元するので、キー名と型が命。
    """
    from app.services.kaipoke.local_diff import (
        correction_before_after,
        item_to_kaipoke_correction,
    )

    cur = CSV_HEADER + _kaipoke_row(
        user="齋藤　様", staff1="髙梨　桂子", svc=PSY_BARE, start="09:30", end="10:05"
    )
    opt = CSV_HEADER + _kaipoke_row(
        user="齋藤　様", staff1="高岡　真由美", svc=PSY_ASSISTANT, start="09:30", end="10:05"
    )
    correction = compare_schedules_from_content(
        cur, opt, target_week_start=1, target_week_end=7, normalize_names=True
    )[0]
    before, after = correction_before_after(correction)
    payload = item_to_kaipoke_correction(correction.action, before, after)

    assert payload["grade_change"] is True
    assert payload["service_type"] == PSY_ASSISTANT
    assert payload["service_type_from"] == PSY_BARE
    assert payload["action"] == "edit"
    # 印の無い行では False / 空文字 (キー自体は常に送る)。
    plain = item_to_kaipoke_correction("edit", {"service_type": PSY_NURSE}, {})
    assert plain["grade_change"] is False
    assert plain["service_type_from"] == ""
