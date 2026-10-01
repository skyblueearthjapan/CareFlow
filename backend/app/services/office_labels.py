"""拠点の略称と並び順 (offices マスタから決める・画面と帳票で共通)。

別の事業所へ提供する準備 (``docs/plans/multi-office-readiness-audit-2026-10-01.md`` #4 / #9)。
拠点の略称 (札・コース表・提案・患者 Excel のコース表記の 1 文字目) と拠点の並び
(Excel の集計・プルダウン) は、拠点コードや拠点名をコードに書かず ``offices`` の
``short_label`` / ``sort_order`` から決める。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


def office_short(short_label: str | None, office_name: str | None) -> str:
    """拠点の略称 = ``offices.short_label``。未設定なら拠点名の 1 文字目。

    PO 決定 (2026-10-01): 札の略称は現場ボード・コース表・患者 Excel と同じ
    ``short_label`` に揃える。``short_label`` 自体を書き換えると Excel の拠点の対応が
    崩れるため、表示側を揃える。
    """
    label = (short_label or "").strip()
    return label or (office_name or "")[:1]


def office_sort_key(sort_order: int | None, name: str | None) -> tuple:
    """拠点の並べ替えキー (``offices.sort_order`` → 名前。sort_order 無しは末尾)。"""
    return (sort_order is None, sort_order or 0, name or "")


class _OfficeLike(Protocol):
    code: str | None
    name: str
    sort_order: int | None
    short_label: str | None


def sorted_offices[T: _OfficeLike](offices: Iterable[T]) -> list[T]:
    """拠点を ``sort_order`` → 名前 → コードの順に並べる。"""
    return sorted(offices, key=lambda o: (*office_sort_key(o.sort_order, o.name), o.code or ""))


def office_code_short_pairs(offices: Iterable[_OfficeLike]) -> list[tuple[str | None, str]]:
    """``(code, 略称)`` の組 (``patient_excel.schema.build_office_code_short_maps`` に渡す)。"""
    return [(o.code, office_short(o.short_label, o.name)) for o in offices]


def ordered_office_codes(offices: Iterable[_OfficeLike]) -> list[str]:
    """コードのある拠点のコードを、拠点の並び順で返す (Excel の拠点コードの選択肢)。"""
    return [o.code for o in sorted_offices(offices) if o.code]
