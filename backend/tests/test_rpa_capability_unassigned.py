"""担当なしガードの単体テスト — rpa_capability.is_unassigned_item / unassigned_item_ids.

2026-09-03 の本番事故: 職員1が空/'-' の add/edit を RPA へ送ると、カイポケには
担当なしの行として入るが、スケジュール表CSV(職員別)は未割当行を出さないため
らく助からは「送れていない」ように見えて add を繰り返す (二重登録)。
edit は実在の職員を '-' で上書きして担当を消す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.kaipoke.rpa_capability import (
    UNASSIGNED_REASON,
    is_unassigned_item,
    unassigned_item_ids,
)


@dataclass
class _Item:
    """CorrectionSheetItem の最小スタブ (id / action / before / after)。

    ``before`` はペア判定 (``pair_key``) で delete 側の実体を読むために要る。
    """

    id: str
    action: str
    after: dict[str, Any] | None = field(default=None)
    before: dict[str, Any] | None = field(default=None)


def _after(staff1: str, *, day: str = "3", start: str = "10:00") -> dict[str, Any]:
    return {"user_name": "山田　花子", "date": day, "start_time": start, "staff1": staff1}


@pytest.mark.parametrize("action", ["add", "edit", "date_change"])
@pytest.mark.parametrize("staff1", ["-", "", "  ", "　"])
def test_unassigned_actions_with_blank_staff(action: str, staff1: str) -> None:
    """職員1が空/'-' の add/edit/date_change は担当なし (全角空白も空扱い)。"""
    assert is_unassigned_item(action, _after(staff1)) is True


@pytest.mark.parametrize("action", ["add", "edit", "date_change"])
def test_assigned_actions_are_not_unassigned(action: str) -> None:
    assert is_unassigned_item(action, _after("熊澤　看護師")) is False


def test_delete_is_never_unassigned() -> None:
    """delete は職員を書き込まない (行を消すだけ) ので素通しでよい。"""
    assert is_unassigned_item("delete", _after("-")) is False
    assert is_unassigned_item("delete", None) is False


def test_missing_after_is_unassigned_for_add() -> None:
    """after が無い add は職員1を確定できない = 送らない (フェイルクローズ)。"""
    assert is_unassigned_item("add", None) is True


def test_unassigned_item_ids_picks_only_blank_staff() -> None:
    items = [
        _Item("a", "add", _after("-")),
        _Item("b", "edit", _after("")),
        _Item("c", "add", _after("熊澤　看護師")),
        _Item("d", "delete", _after("-")),
    ]
    assert unassigned_item_ids(items) == {"a", "b"}


def test_unassigned_item_ids_is_empty_when_all_assigned() -> None:
    items = [_Item("a", "add", _after("熊澤　看護師")), _Item("b", "delete", _after("田中"))]
    assert unassigned_item_ids(items) == set()


# --- ペア保護 (レビュー HIGH) -----------------------------------------------
#
# 差分エンジンはサービス内容も突合キーに含めるため、「カイポケ=准看で担当あり /
# らく助=担当なし ('-' → 正看)」は edit ではなく delete + add に割れる。
# add だけ落として delete を送るとカイポケから予定が丸ごと消える。


def test_paired_delete_is_skipped_with_the_unassigned_add() -> None:
    """同じ (日, 開始時刻, 患者) の delete は担当なし add の道連れで外す。"""
    items = [
        _Item("add", "add", after=_after("-")),
        _Item("del", "delete", before=_after("熊澤　看護師")),
    ]
    assert unassigned_item_ids(items) == {"add", "del"}


def test_paired_delete_matches_across_name_variants() -> None:
    """氏名のゆれ (空白/異体字) でペアを取り逃がさない (正規化して突合)。"""
    items = [
        _Item("add", "add", after={**_after("-"), "user_name": "山田 花子"}),
        _Item("del", "delete", before={**_after("熊澤"), "user_name": "山田　花子"}),
    ]
    assert unassigned_item_ids(items) == {"add", "del"}


def test_unrelated_delete_is_not_dragged_in() -> None:
    """別の訪問の delete は巻き添えにしない (ガードの過剰適用を防ぐ)。"""
    items = [
        _Item("add", "add", after=_after("-", day="3", start="10:00")),
        _Item("del", "delete", before=_after("熊澤", day="4", start="15:00")),
    ]
    assert unassigned_item_ids(items) == {"add"}


def test_pair_key_unresolvable_does_not_block_everything() -> None:
    """日付/時刻/氏名が欠けた担当なし行はペア判定をしない (delete は素通し)。"""
    items = [
        _Item("add", "add", after={"staff1": "-"}),
        _Item("del", "delete", before=_after("熊澤")),
    ]
    assert unassigned_item_ids(items) == {"add"}


def test_reason_is_human_readable() -> None:
    """FE の注記・job.result_summary で同じ文言を使う (説明できる除外にする)。"""
    assert "担当なし" in UNASSIGNED_REASON
