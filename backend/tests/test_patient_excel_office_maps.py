"""patient_excel.schema — 拠点マスタ駆動の略称解決 (0059 / 別の事業所へ提供する準備 #4).

PO決定「コードが事業所を特定しない」: 拠点付きコーストークンの略称 ↔ office_code は
offices マスタ (short_label、未設定なら拠点名の 1 文字目) から ``build_office_code_short_maps``
で構築して注入する。稲毛・都賀の決め打ち (旧 legacy 既定) は持たない。
"""

from __future__ import annotations

from app.services.patient_excel.schema import (
    build_office_code_short_maps,
    course_token,
    parse_course_token,
)


def test_build_maps_from_master_only() -> None:
    code_to_short, short_to_code = build_office_code_short_maps(
        [("MAKUHARI", "幕"), ("INAGE", "稲")]
    )
    assert code_to_short == {"MAKUHARI": "幕", "INAGE": "稲"}
    assert short_to_code == {"幕": "MAKUHARI", "稲": "INAGE"}
    # マスタに無い拠点 (旧 legacy の TSUGA) は入らない。
    assert "TSUGA" not in code_to_short
    # None / 空は無視する (壊さない)。
    code_to_short2, short_to_code2 = build_office_code_short_maps([(None, "X"), ("Y", None)])
    assert "X" not in short_to_code2
    assert "Y" not in code_to_short2


def test_parse_course_token_master_driven_new_office() -> None:
    _, short_to_code = build_office_code_short_maps([("MAKUHARI", "幕")])
    # 注入された新拠点の略称でコースを解決できる。
    assert parse_course_token("幕A", short_to_code) == ("MAKUHARI", "A")
    # office_code そのもの始まりも後方互換で受理する。
    assert parse_course_token("MAKUHARIB", short_to_code) == ("MAKUHARI", "B")


def test_parse_course_token_without_master_is_unresolved() -> None:
    # 対応を注入しなければ解決しない (拠点の決め打ちは持たない)。
    assert parse_course_token("稲A") is None
    assert parse_course_token("稲A", {}) is None
    assert parse_course_token("不明X", {"稲": "INAGE"}) is None
    # 略称だけでコースが無いものは解決しない。
    assert parse_course_token("稲", {"稲": "INAGE"}) is None


def test_course_token_master_driven() -> None:
    code_to_short, _ = build_office_code_short_maps([("MAKUHARI", "幕")])
    assert course_token("MAKUHARI", "A", code_to_short) == "幕A"
    # 略称が無い拠点コードはコードそのものを使う (後方互換)。
    assert course_token("FOO", "A", code_to_short) == "FOOA"
    assert course_token("INAGE", "A") == "INAGEA"
