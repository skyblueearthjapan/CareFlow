"""訪問頻度 (毎週 / 隔週 / 月の第何週) の判定 — DB に触らない純関数だけ.

正典: ``docs/plans/biweekly-monthly-design-2026-10-30.md`` §3 (PO 決定 2026-10-30)。
予定を作る・数えるすべての経路 (週の生成・保留プール・自動割当・固定訪問への作り直し・
前の週のコピー・提案・エージェント) は、この 1 か所の決まりで「その週に訪問するか」を
判定する。

``patients.weekly_pattern`` (JSONB) のキー:

* ``visit_frequency``: ``"every"`` | ``"biweekly"`` | ``"monthly"``。無い / null は毎週。
* ``biweekly_anchor``: ``"YYYY-MM-DD"`` (月曜)。その月曜から始まる週が **A の週**。
  起点から 0・2・4… 週目が A、1・3・5… 週目が B。日数の差で数えるので年をまたいでも
  ずれない (ISO 週番号の偶奇は使わない。2026 年は 53 週ある)。
* ``visit_weeks``: ``"1,3"`` のような文字列。月の第何週 (1〜5) に訪問するか。
  ``monthly`` のときだけ使う。

``patient_fixed_visits.visit_week`` (枠ごと): ``"every"`` | ``"A"`` | ``"B"``。

決まり:

* every: 毎週。枠の visit_week は見ない。
* biweekly: 枠が every / A → A の週に訪問、B → B の週に訪問。
  起点が無い (または読めない) → 毎週 (今までと同じ) + 状態 ``missing_anchor``。
* monthly: 枠の曜日のその週の日付 (week_start + weekday) が、その日の属する月で
  第 N 回目の曜日 (N = (日 - 1) // 7 + 1) で、N が visit_weeks に含まれる週だけ訪問。
  枠の visit_week は見ない。visit_weeks が空 → 毎週 + 状態 ``missing_weeks``。
* ``frequency_per_week`` は「訪問する週の回数」。お休みの週の希望回数は 0。

特別週 (``mode='special'`` の枠・special_weekly_pattern) には頻度の決まりを当てない
(特別週は別設計。ここでは通常の枠だけを間引く)。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any, Literal

VisitFrequency = Literal["every", "biweekly", "monthly"]
FrequencyStatus = Literal["ok", "missing_anchor", "missing_weeks"]
SlotVisitWeek = Literal["every", "A", "B"]

FREQ_EVERY = "every"
FREQ_BIWEEKLY = "biweekly"
FREQ_MONTHLY = "monthly"
VISIT_FREQUENCIES: frozenset[str] = frozenset({FREQ_EVERY, FREQ_BIWEEKLY, FREQ_MONTHLY})

SLOT_EVERY = "every"
SLOT_A = "A"
SLOT_B = "B"
SLOT_VISIT_WEEKS: frozenset[str] = frozenset({SLOT_EVERY, SLOT_A, SLOT_B})

STATUS_OK = "ok"
STATUS_MISSING_ANCHOR = "missing_anchor"
STATUS_MISSING_WEEKS = "missing_weeks"

#: upcoming_visit_dates が先を探す上限 (週)。第 5 週だけの月次でも 5 年あれば十分。
_MAX_LOOKAHEAD_WEEKS = 260


# ---------------------------------------------------------------------------
# 値の読み取り・検査
# ---------------------------------------------------------------------------


def _as_pattern(weekly_pattern: Any) -> dict:
    return weekly_pattern if isinstance(weekly_pattern, dict) else {}


def monday_of(d: date) -> date:
    """その日を含む週の月曜."""
    return d - timedelta(days=d.weekday())


def normalize_visit_weeks(value: Any) -> str | None:
    """訪問週 (第何週) を ``"1,3"`` の形にそろえる (保存用・厳格).

    受けるもの: ``"1,3"`` / ``"1, 3"`` / ``"１，３"`` / ``"1、3"`` / ``[1, 3]`` / ``3``。
    空 (``None`` / ``""`` / 空白だけ / ``[]``) は ``None``。
    1〜5 以外・数でないもの・重複は ``ValueError``。並びは昇順にする。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("visit_weeks は 1〜5 の数をカンマ区切りで指定してください")
    if isinstance(value, int):
        parts: list[str] = [str(value)]
    elif isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    elif isinstance(value, str):
        text = unicodedata.normalize("NFKC", value)
        for sep in ("、", "，", ";", " ", "　"):
            text = text.replace(sep, ",")
        parts = text.split(",")
    else:
        raise ValueError("visit_weeks は 1〜5 の数をカンマ区切りで指定してください")
    nums: list[int] = []
    for raw in parts:
        token = raw.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"visit_weeks に数でない値があります: {raw!r}")
        n = int(token)
        if not 1 <= n <= 5:
            raise ValueError(f"visit_weeks は 1〜5 で指定してください: {n}")
        if n in nums:
            raise ValueError(f"visit_weeks に同じ週が 2 回あります: {n}")
        nums.append(n)
    if not nums:
        return None
    return ",".join(str(n) for n in sorted(nums))


def parse_visit_weeks(value: Any) -> frozenset[int]:
    """訪問週を数の集合で返す (判定用・寛容). 読めない値は空集合 (= 未設定)."""
    try:
        normalized = normalize_visit_weeks(value)
    except ValueError:
        return frozenset()
    if normalized is None:
        return frozenset()
    return frozenset(int(x) for x in normalized.split(","))


def validate_biweekly_anchor(value: Any) -> str | None:
    """起点の週 (月曜の日付) を ``"YYYY-MM-DD"`` にそろえる (保存用・厳格).

    空は ``None``。日付として読めない・月曜でないものは ``ValueError``。
    """
    if value is None:
        return None
    if isinstance(value, date):
        d = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            d = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(
                f"biweekly_anchor は YYYY-MM-DD の日付で指定してください: {value!r}"
            ) from exc
    else:
        raise ValueError("biweekly_anchor は YYYY-MM-DD の日付で指定してください")
    if d.weekday() != 0:
        raise ValueError(f"biweekly_anchor は月曜の日付で指定してください: {d.isoformat()}")
    return d.isoformat()


def parse_biweekly_anchor(value: Any) -> date | None:
    """起点の週を ``date`` で返す (判定用・寛容). 読めない・月曜でない値は ``None``."""
    try:
        normalized = validate_biweekly_anchor(value)
    except ValueError:
        return None
    return date.fromisoformat(normalized) if normalized else None


def visit_frequency_of(weekly_pattern: Any) -> str:
    """頻度を返す. 無い・null・知らない値は ``"every"`` (今までと同じ毎週)."""
    raw = _as_pattern(weekly_pattern).get("visit_frequency")
    return raw if isinstance(raw, str) and raw in VISIT_FREQUENCIES else FREQ_EVERY


def frequency_status(weekly_pattern: Any) -> FrequencyStatus:
    """頻度の設定が足りているか.

    * ``missing_anchor``: 隔週なのに起点の週が無い (→ 毎週作る)。
    * ``missing_weeks``: 月の第何週なのに第何週が空 (→ 毎週作る)。
    * ``ok``: それ以外 (毎週を含む)。
    """
    wp = _as_pattern(weekly_pattern)
    freq = visit_frequency_of(wp)
    if freq == FREQ_BIWEEKLY and parse_biweekly_anchor(wp.get("biweekly_anchor")) is None:
        return STATUS_MISSING_ANCHOR
    if freq == FREQ_MONTHLY and not parse_visit_weeks(wp.get("visit_weeks")):
        return STATUS_MISSING_WEEKS
    return STATUS_OK


def frequency_rule_active(weekly_pattern: Any) -> bool:
    """頻度でお休みの週が生じうるか (隔週・月次で、設定が足りているとき)."""
    return visit_frequency_of(weekly_pattern) != FREQ_EVERY and (
        frequency_status(weekly_pattern) == STATUS_OK
    )


_WEEKDAY_CODES: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _weekday_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str) and value in _WEEKDAY_CODES:
        return _WEEKDAY_CODES.index(value)
    return None


def pattern_weekdays(weekly_pattern: Any) -> list[int]:
    """希望の曜日 (枠の無い患者用). ``entries`` の曜日、無ければ ``preferred_weekdays``."""
    wp = _as_pattern(weekly_pattern)
    for key, pick in (
        ("entries", lambda e: e.get("weekday") if isinstance(e, dict) else None),
        ("preferred_weekdays", lambda e: e),
    ):
        raw = wp.get(key)
        if isinstance(raw, list):
            days = sorted({d for d in (_weekday_int(pick(e)) for e in raw) if d is not None})
            if days:
                return days
    return []


def nth_weekday_of_month(d: date) -> int:
    """その日の曜日が、その月で何回目か (1〜5). 例: 10/15 (木) → 第 3 木曜 → 3."""
    return (d.day - 1) // 7 + 1


def is_a_week(anchor: date, week_start: date) -> bool:
    """起点の週から数えて偶数週目 (0・2・4…) なら A の週."""
    weeks = (monday_of(week_start) - monday_of(anchor)).days // 7
    return weeks % 2 == 0


def normalize_slot_visit_week(value: Any) -> str:
    """枠の visit_week を 3 値にそろえる. 無い・知らない値は ``"every"``."""
    return value if isinstance(value, str) and value in SLOT_VISIT_WEEKS else SLOT_EVERY


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def is_slot_visit_week(
    weekly_pattern: Any,
    slot_visit_week: Any,
    week_start: date,
    weekday: int,
) -> bool:
    """枠 (曜日 ``weekday``・``slot_visit_week``) が ``week_start`` の週に訪問するか.

    ``week_start`` は月曜を想定するが、ほかの曜日が来てもその週の月曜に寄せる。
    設定が足りない (missing_anchor / missing_weeks) ときは毎週 True。
    """
    if not frequency_rule_active(weekly_pattern):
        return True
    wp = _as_pattern(weekly_pattern)
    monday = monday_of(week_start)
    freq = visit_frequency_of(wp)
    if freq == FREQ_BIWEEKLY:
        anchor = parse_biweekly_anchor(wp.get("biweekly_anchor"))
        assert anchor is not None  # frequency_rule_active で確認済み
        a_week = is_a_week(anchor, monday)
        return not a_week if normalize_slot_visit_week(slot_visit_week) == SLOT_B else a_week
    if freq == FREQ_MONTHLY:
        weeks = parse_visit_weeks(wp.get("visit_weeks"))
        the_day = monday + timedelta(days=int(weekday))
        return nth_weekday_of_month(the_day) in weeks
    return True


def is_patient_visit_week(
    weekly_pattern: Any,
    week_start: date,
    slots: Iterable[tuple[int, Any]] | None = None,
) -> bool:
    """その週に、この患者の訪問が 1 回でもありうるか.

    ``slots`` (``(曜日, visit_week)`` の並び) を渡すと、その枠のどれかが訪問すれば True。
    渡さない (枠を知らない・枠の無い患者) ときは:

    * 隔週: A の週だけ True (B の枠を知らないため。枠を知っている呼び出し側は
      ``slots`` を渡すか ``is_slot_visit_week`` を使うこと)。
    * 月次: 希望の曜日 (``entries`` の曜日、無ければ ``preferred_weekdays``。どちらも
      無ければ週の 7 日) のどれかの日付が visit_weeks の第 N 週に当たれば True。
    """
    if not frequency_rule_active(weekly_pattern):
        return True
    slot_list = list(slots) if slots is not None else []
    if slot_list:
        return any(is_slot_visit_week(weekly_pattern, vw, week_start, wd) for wd, vw in slot_list)
    wp = _as_pattern(weekly_pattern)
    if visit_frequency_of(wp) == FREQ_BIWEEKLY:
        return is_slot_visit_week(wp, SLOT_A, week_start, 0)
    days = pattern_weekdays(wp) or list(range(7))
    return any(is_slot_visit_week(wp, SLOT_EVERY, week_start, wd) for wd in days)


def desired_count_for_week(
    weekly_pattern: Any,
    base: int | None,
    week_start: date,
    slots: Iterable[tuple[int, Any]] | None = None,
) -> int | None:
    """その週の希望回数. お休みの週は 0.

    ``base`` は今までの計算で出した希望回数 (``frequency_per_week`` など。不明は None)。
    頻度の決まりが効かない患者 (毎週・設定不足) は ``base`` をそのまま返す (挙動不変)。
    枠を渡すと、その週に当たる枠の曜日数を上限にする (設計 §3-3.3)。
    """
    if not frequency_rule_active(weekly_pattern):
        return base
    slot_list = list(slots) if slots is not None else []
    if slot_list:
        weekdays = {
            int(wd)
            for wd, vw in slot_list
            if is_slot_visit_week(weekly_pattern, vw, week_start, int(wd))
        }
        if not weekdays:
            return 0
        return len(weekdays) if base is None else min(base, len(weekdays))
    return base if is_patient_visit_week(weekly_pattern, week_start) else 0


def upcoming_visit_dates(
    weekly_pattern: Any,
    slots: Sequence[tuple[int, Any]],
    from_date: date,
    count: int,
) -> list[date]:
    """``from_date`` 以降の訪問日を先から ``count`` 件 (患者マスタの「次の訪問」用).

    ``slots`` は ``(曜日, visit_week)``。同じ曜日の 2 名体制 (slot 0/1) は 1 日に数える。
    枠が無ければ空。
    """
    if count <= 0 or not slots:
        return []
    out: list[date] = []
    seen: set[date] = set()
    monday = monday_of(from_date)
    ordered = sorted(((int(wd), vw) for wd, vw in slots), key=lambda s: s[0])
    for _ in range(_MAX_LOOKAHEAD_WEEKS):
        for wd, vw in ordered:
            d = monday + timedelta(days=wd)
            if d < from_date or d in seen:
                continue
            if is_slot_visit_week(weekly_pattern, vw, monday, wd):
                seen.add(d)
                out.append(d)
                if len(out) >= count:
                    return out
        monday += timedelta(days=7)
    return out


# ---------------------------------------------------------------------------
# 固定訪問の行 (PatientFixedVisit) 向けの薄い包み (属性を読むだけ・DB には触らない)
# ---------------------------------------------------------------------------


def pfv_slot(row: Any) -> tuple[int, str]:
    """PatientFixedVisit 行を ``(曜日, visit_week)`` にする."""
    return int(row.weekday), normalize_slot_visit_week(getattr(row, "visit_week", None))


def pfv_slots(rows: Iterable[Any], *, mode: str = "normal") -> list[tuple[int, str]]:
    """``mode`` の行だけを ``(曜日, visit_week)`` の並びにする."""
    return [pfv_slot(r) for r in rows if getattr(r, "mode", "normal") == mode]


def pfv_visits_in_week(weekly_pattern: Any, row: Any, week_start: date) -> bool:
    """固定訪問の 1 行が ``week_start`` の週に訪問するか.

    特別週の行 (``mode='special'``) には頻度の決まりを当てない (常に True)。
    """
    if getattr(row, "mode", "normal") != "normal":
        return True
    weekday, vw = pfv_slot(row)
    return is_slot_visit_week(weekly_pattern, vw, week_start, weekday)


def filter_pfv_rows_for_week(weekly_pattern: Any, rows: Iterable[Any], week_start: date) -> list:
    """その週に訪問する固定訪問の行だけを残す (特別週の行はそのまま残す)."""
    return [r for r in rows if pfv_visits_in_week(weekly_pattern, r, week_start)]


__all__ = [
    "FREQ_BIWEEKLY",
    "FREQ_EVERY",
    "FREQ_MONTHLY",
    "SLOT_A",
    "SLOT_B",
    "SLOT_EVERY",
    "SLOT_VISIT_WEEKS",
    "STATUS_MISSING_ANCHOR",
    "STATUS_MISSING_WEEKS",
    "STATUS_OK",
    "VISIT_FREQUENCIES",
    "desired_count_for_week",
    "filter_pfv_rows_for_week",
    "frequency_rule_active",
    "frequency_status",
    "is_a_week",
    "is_patient_visit_week",
    "is_slot_visit_week",
    "monday_of",
    "normalize_slot_visit_week",
    "normalize_visit_weeks",
    "nth_weekday_of_month",
    "pattern_weekdays",
    "parse_biweekly_anchor",
    "parse_visit_weeks",
    "pfv_slot",
    "pfv_slots",
    "pfv_visits_in_week",
    "upcoming_visit_dates",
    "validate_biweekly_anchor",
    "visit_frequency_of",
]
