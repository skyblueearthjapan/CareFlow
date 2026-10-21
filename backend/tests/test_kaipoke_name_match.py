"""K-1c: 氏名正規化・名寄せユーティリティのテスト."""

from __future__ import annotations

from app.services.kaipoke.name_match import (
    build_name_index,
    match_name,
    normalize_name_key,
)


def test_full_width_space_collapsed() -> None:
    # カイポケ「姓　名」(全角スペース) と CareFlow「姓名」が同キーになる。
    assert normalize_name_key("職員D　秋子") == normalize_name_key("職員D秋子")
    assert normalize_name_key("職員D 秋子") == normalize_name_key("職員D秋子")


def test_kanji_variants_folded() -> None:
    # 異体字が常用字体へ寄る。
    assert normalize_name_key("髙橋　花子") == normalize_name_key("高橋 花子")
    assert normalize_name_key("栁田") == normalize_name_key("柳田")
    # 槇/槙 (2026-07-26 置換取り込みで見つかった形・名前は仮: らく助「槇田 一郎」⇔カイポケ「槙田 一郎」)
    assert normalize_name_key("槇田　一郎") == normalize_name_key("槙田 一郎")
    assert normalize_name_key("渡邉　太郎") == normalize_name_key("渡辺太郎")
    assert normalize_name_key("山﨑") == normalize_name_key("山崎")


def test_empty_and_whitespace() -> None:
    assert normalize_name_key("") == ""
    assert normalize_name_key("　  ") == ""


def test_match_unique() -> None:
    idx = build_name_index(
        {
            "s1": "職員D　秋子",
            "s2": "髙橋　花子",
        }
    )
    # カイポケ側の異体字・全角スペース違いでも一意に当たる。
    assert match_name("職員D 秋子", idx) == "s1"
    assert match_name("高橋　花子", idx) == "s2"


def test_match_miss_returns_none() -> None:
    idx = build_name_index({"s1": "職員D　秋子"})
    assert match_name("存在　しない", idx) is None


def test_match_ambiguous_returns_none() -> None:
    # 同姓同名 (正規化後に衝突) は成功扱いしない。
    idx = build_name_index({"s1": "田中　太郎", "s2": "田中太郎"})
    assert match_name("田中 太郎", idx) is None
