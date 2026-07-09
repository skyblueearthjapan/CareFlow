"""patient_excel.schema — 拠点マスタ駆動の短縮名解決 (0059).

PO決定「コードが事業所を特定しない」: 拠点付きコーストークンの短縮名 ↔ office_code は
offices マスタ (short_label / code) から ``build_office_code_short_maps`` で構築して注入する。
未注入時は legacy 既定 (稲毛/都賀) にフォールバックする (後方互換)。
"""

from __future__ import annotations

from app.services.patient_excel.schema import (
    build_office_code_short_maps,
    course_token,
    parse_course_token,
)


def test_build_maps_merges_master_over_legacy_default() -> None:
    # 新拠点 (幕張=幕/MAKUHARI) をマスタから注入。legacy (稲毛/都賀) も土台として残る。
    code_to_short, short_to_code = build_office_code_short_maps(
        [("MAKUHARI", "幕"), ("INAGE", "稲")]
    )
    assert code_to_short["MAKUHARI"] == "幕"
    assert short_to_code["幕"] == "MAKUHARI"
    # legacy 既定は土台として残る (backfill 前 DB / short_label 未設定でも解決可能)。
    assert code_to_short["INAGE"] == "稲"
    assert short_to_code["津"] == "TSUGA"
    # None / 空は無視する (壊さない)。
    code_to_short2, short_to_code2 = build_office_code_short_maps([(None, "X"), ("Y", None)])
    assert "X" not in short_to_code2
    assert "Y" not in code_to_short2


def test_parse_course_token_master_driven_new_office() -> None:
    _, short_to_code = build_office_code_short_maps([("MAKUHARI", "幕")])
    # 注入された新拠点の短縮名でコースを解決できる。
    assert parse_course_token("幕A", short_to_code) == ("MAKUHARI", "A")
    # office_code そのもの始まりも後方互換で受理する。
    assert parse_course_token("MAKUHARIB", short_to_code) == ("MAKUHARI", "B")


def test_parse_course_token_default_fallback() -> None:
    # 未注入時は legacy 既定 (稲毛/都賀) を使う。
    assert parse_course_token("稲A") == ("INAGE", "A")
    assert parse_course_token("津M") == ("TSUGA", "M")
    assert parse_course_token("不明X") is None


def test_course_token_master_driven_and_default() -> None:
    code_to_short, _ = build_office_code_short_maps([("MAKUHARI", "幕")])
    assert course_token("MAKUHARI", "A", code_to_short) == "幕A"
    # 未注入時は legacy 既定。
    assert course_token("INAGE", "A") == "稲A"
    # 短縮名が無い拠点コードはコードそのものを使う (後方互換)。
    assert course_token("FOO", "A", code_to_short) == "FOOA"
