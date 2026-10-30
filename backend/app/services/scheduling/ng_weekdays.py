"""NG 曜日 (訪問しない曜日) — DB に触らない純関数だけ.

正典: ``docs/plans/biweekly-monthly-design-2026-10-30.md`` §2-1 / §9
(PO 決定 2026-10-30「NG 曜日＝画面に出して全経路で守る」)。

``patients.weekly_pattern.ng_weekdays`` (JSONB の中のキー) に ``["Mon", "Wed"]`` の
ような曜日コードの並びで持つ。NG スタッフと同じ「ハード制約」として扱う:

* 日を選ぶ経路 (空き枠の提案・保留プール・自動割当・ならし・今週だけの移動/追加・
  固定訪問の枠の作成/移動) は、NG 曜日を候補にしない / 422 で止める。
* 週の生成は、保存済みの固定訪問の枠を **そのまま** 展開する (黙って落とさない)。
  NG 曜日に載っている既存の枠は、点検 (feasibility) と保留プールの理由で知らせる。

曜日は 0=月 … 6=日 (``date.weekday()`` と同じ)。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

WEEKDAY_CODES: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_JP: tuple[str, ...] = ("月", "火", "水", "木", "金", "土", "日")

#: 422 の detail.code (FE はこの値で NG 曜日の止め方だと分かる)。
NG_WEEKDAY_CODE = "ng_weekday"


def _weekday_of(value: Any) -> int | None:
    """曜日コード ("Mon") / 0〜6 の整数 / 日本語 1 字 ("月") を 0〜6 に. 読めなければ None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    if isinstance(value, str):
        text = value.strip()
        if text in WEEKDAY_CODES:
            return WEEKDAY_CODES.index(text)
        if text in WEEKDAY_JP:
            return WEEKDAY_JP.index(text)
    return None


def normalize_ng_weekdays(value: Any) -> list[str] | None:
    """NG 曜日を保存用にそろえる (厳格). 曜日コードの並び (月→日の順・重複なし).

    受けるもの: ``["Mon", "Wed"]`` / ``[0, 2]`` / ``["月", "水"]``。``None`` は ``None``
    (= キーを書かない)。空の並びは ``[]`` (= NG 曜日なし)。
    読めない値は ``ValueError``。重複は 1 つにまとめる。
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("ng_weekdays は曜日 (Mon〜Sun) の並びで指定してください")
    days: set[int] = set()
    for raw in value:
        wd = _weekday_of(raw)
        if wd is None:
            raise ValueError(f"ng_weekdays に曜日でない値があります: {raw!r}")
        days.add(wd)
    return [WEEKDAY_CODES[d] for d in sorted(days)]


def ng_weekdays_of(weekly_pattern: Any) -> frozenset[int]:
    """患者の NG 曜日 (判定用・寛容). 読めない値は無視する. キーが無ければ空."""
    if not isinstance(weekly_pattern, dict):
        return frozenset()
    raw = weekly_pattern.get("ng_weekdays")
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    return frozenset(d for d in (_weekday_of(v) for v in raw) if d is not None)


def patient_ng_weekdays(patient: Any) -> frozenset[int]:
    """患者 (ORM / 任意のオブジェクト) の NG 曜日. ``weekly_pattern`` が無ければ空."""
    return ng_weekdays_of(getattr(patient, "weekly_pattern", None))


def is_ng_weekday(weekly_pattern: Any, weekday: int) -> bool:
    return weekday in ng_weekdays_of(weekly_pattern)


def weekday_label(weekday: int) -> str:
    return WEEKDAY_JP[weekday] if 0 <= weekday <= 6 else str(weekday)


def weekdays_label(weekdays: Iterable[int]) -> str:
    """``{0, 2}`` → ``"月・水"`` (月→日の順)."""
    return "・".join(weekday_label(d) for d in sorted(set(weekdays)))


def ng_weekday_message(patient_name: str | None, weekday: int) -> str:
    """日を選ぶ経路で止めるときの文言 (422 の detail.message)."""
    who = f"{patient_name}様" if patient_name else "この方"
    return (
        f"{who}は{weekday_label(weekday)}曜日が NG 曜日（訪問しない曜日）のため、"
        "その日には入れられません。患者マスタの NG 曜日をご確認ください。"
    )


def ng_weekday_detail(
    *, patient_id: Any, patient_name: str | None, weekday: int, **extra: Any
) -> dict[str, Any]:
    """422 の detail (全経路で同じ形)."""
    detail: dict[str, Any] = {
        "code": NG_WEEKDAY_CODE,
        "patient_id": str(patient_id),
        "weekday": weekday,
        "message": ng_weekday_message(patient_name, weekday),
    }
    detail.update(extra)
    return detail


def preferred_ng_conflicts(weekly_pattern: Any) -> list[int]:
    """希望曜日 (preferred_weekdays と entries の曜日) のうち NG 曜日に当たるもの."""
    ng = ng_weekdays_of(weekly_pattern)
    if not ng or not isinstance(weekly_pattern, dict):
        return []
    days: set[int] = set()
    raw_pref = weekly_pattern.get("preferred_weekdays")
    if isinstance(raw_pref, list):
        days.update(d for d in (_weekday_of(v) for v in raw_pref) if d is not None)
    raw_entries = weekly_pattern.get("entries")
    if isinstance(raw_entries, list):
        for e in raw_entries:
            if isinstance(e, dict):
                d = _weekday_of(e.get("weekday"))
                if d is not None:
                    days.add(d)
    return sorted(days & ng)


__all__ = [
    "NG_WEEKDAY_CODE",
    "WEEKDAY_CODES",
    "WEEKDAY_JP",
    "is_ng_weekday",
    "ng_weekday_detail",
    "ng_weekday_message",
    "ng_weekdays_of",
    "normalize_ng_weekdays",
    "patient_ng_weekdays",
    "preferred_ng_conflicts",
    "weekday_label",
    "weekdays_label",
]
