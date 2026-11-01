"""受け入れ枠を「月で見る」— 谷 (空き) と山 (混み) の集計 (DB を使わない純粋な関数).

試算: ``docs/reports/agent/2026-11-受け入れ枠の月の埋まり.html``。
判定は今の受け入れ枠 (``acceptance_strict``) と同じ決まりを使い、見る週を「その月の週」に、
マスを曜日 × 30 分にする。

* 谷: マスの 30 分のうちに 35 分の新しい訪問を始められた職員の数 (週ごと) → ○△× (``grade``)。
* 山: 稼働率 = 訪問していた分 ÷ 勤務できた分 (マスの 30 分の中で)。``CROWDED_UTILIZATION``
  以上のマスに「新しい訪問は入れない方がよい」の印。
* 前の月との比べ: 稼働率が ``TREND_POINTS`` ポイント以上上がれば ▲、下がれば ▼。

時刻はすべて「その日の 0:00 からの分」。区間は [開始, 終了)。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Final

from app.services.scheduling.acceptance_strict import (
    StaffDay,
    StrictRules,
    _merge,
    _window,
    free_starts,
    grade,
    hhmm,
)

#: マスの長さ (分) と、表に出す範囲 (9:30〜18:00・終わりは含まない)。
SLOT_MIN: Final = 30
SLOT_FROM: Final = 9 * 60 + 30
SLOT_TO: Final = 18 * 60
#: 混みの基準 (稼働率がこれ以上のマスに印。PO 決定・後で調整する 1 か所)。
CROWDED_UTILIZATION: Final = 0.70
#: 前の月との比べで ▲▼ を付ける差 (ポイント)。
TREND_POINTS: Final = 10
#: 「件数の多い職員」の件数 (1 日・主担当)。
BUSY_DAY_VISITS: Final = 6
#: 訪問がこの件数に満たない日は、休業か予定がまだ無い日として数えない。
OPEN_DAY_MIN_VISITS: Final = 5
#: 要約に出すピークの数。
PEAK_COUNT: Final = 3

TREND_UP: Final = "▲"
TREND_DOWN: Final = "▼"


def month_slots() -> list[int]:
    """マスの開始 (分)。9:30, 10:00, …, 17:30。"""
    return list(range(SLOT_FROM, SLOT_TO, SLOT_MIN))


def slot_key(weekday: int, slot: int) -> str:
    return f"{weekday}-{hhmm(slot)}"


@dataclass(frozen=True)
class MonthVisit:
    """主担当の訪問 1 件 (件数・間の余裕・時間固定の割合に使う)。"""

    start: int
    end: int
    time_fixed: bool = False


@dataclass
class MonthStaffDay:
    """職員 × 日の材料。``sd`` は今の受け入れ枠と同じ (訪問は同行も含めて時間を塞ぐ)。"""

    sd: StaffDay
    primary: list[MonthVisit] = field(default_factory=list)


@dataclass
class DaySlots:
    """1 人 1 日の、マスごとの勤務できた分・訪問していた分・入れるマス。"""

    off: bool
    full: bool
    free: set[int]
    work: dict[int, int]
    busy: dict[int, int]


def _overlap(a: int, b: int, c: int, d: int) -> int:
    return max(0, min(b, d) - max(a, c))


def day_slots(msd: MonthStaffDay, rules: StrictRules, *, use_off: bool) -> DaySlots:
    """1 人 1 日をマスに分ける。休み・時間変更は ``use_off`` のとき今の受け入れ枠と同じに読む。"""
    sd = msd.sd
    is_off, ws, we, _why, _kind = _window(sd, rules, use_off)
    slots = month_slots()
    if is_off:
        return DaySlots(off=True, full=False, free=set(), work={}, busy={})
    work = {s: m for s in slots if (m := _overlap(ws, we, s, s + SLOT_MIN)) > 0}
    starts, ends = _merge(sd.visits)
    busy: dict[int, int] = {}
    for s in work:
        lo, hi = max(s, ws), min(s + SLOT_MIN, we)
        minutes = sum(_overlap(a, b, lo, hi) for a, b in zip(starts, ends, strict=True))
        if minutes > 0:
            busy[s] = minutes
    full = sd.n >= rules.cap
    free: set[int] = set()
    if not full:
        blocks = list(sd.events)
        if use_off:
            blocks += [(a, b) for a, b, _ in sd.kp_unknown]
        for t in free_starts(sd.visits, blocks, ws, we, rules):
            if SLOT_FROM <= t < SLOT_TO:
                free.add(SLOT_FROM + (t - SLOT_FROM) // SLOT_MIN * SLOT_MIN)
    return DaySlots(off=False, full=full, free=free, work=work, busy=busy)


def month_rules(rules: StrictRules, *, lunch: bool) -> StrictRules:
    """昼休みを「考えない」ときは昼の決まりを外す。"""
    return rules if lunch else replace(rules, lunch_min=0)


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def summarize_month(
    month_days: Sequence[date],
    open_days: Iterable[date],
    staff_days: Iterable[MonthStaffDay],
    selected: set[str],
    weekdays: Sequence[int],
    rules: StrictRules,
    *,
    use_off: bool,
) -> dict[str, Any]:
    """曜日 × 30 分のマス・曜日の要約・ピーク。

    ``month_days`` はその月の日 (営業曜日)、``open_days`` は数える日 (訪問のある日)。
    担い手 (``selected``) の職員だけを数える。
    """
    open_set = set(open_days)
    by_day: dict[date, list[MonthStaffDay]] = {}
    for msd in staff_days:
        if msd.sd.staff_id in selected and msd.sd.day in open_set:
            by_day.setdefault(msd.sd.day, []).append(msd)
    computed = {
        d: [(m, day_slots(m, rules, use_off=use_off)) for m in ms] for d, ms in by_day.items()
    }
    slots = month_slots()
    cells: dict[str, dict[str, Any]] = {}
    weekday_rows: list[dict[str, Any]] = []
    for wd in weekdays:
        total_dates = [d for d in month_days if d.weekday() == wd]
        counted = [d for d in total_dates if computed.get(d)]
        day_visits = sum(len(m.primary) for d in counted for m, _ in computed[d])
        wd_work = wd_busy = 0
        for slot in slots:
            per_week: list[int] = []
            work = busy = starts = fixed = busy_staff = pairs = tight = 0
            for d in counted:
                n_free = 0
                for m, ds in computed[d]:
                    if slot in ds.free:
                        n_free += 1
                    w = ds.work.get(slot, 0)
                    work += w
                    busy += ds.busy.get(slot, 0)
                    if w > 0 and m.sd.n >= BUSY_DAY_VISITS:
                        busy_staff += 1
                    prev_end: int | None = None
                    for v in sorted(m.primary, key=lambda v: (v.start, v.end)):
                        in_slot = slot <= v.start < slot + SLOT_MIN
                        if in_slot:
                            starts += 1
                            fixed += int(v.time_fixed)
                            if prev_end is not None:
                                pairs += 1
                                tight += int(v.start - prev_end < rules.pad)
                        prev_end = v.end if prev_end is None else max(prev_end, v.end)
                per_week.append(n_free)
            wd_work += work
            wd_busy += busy
            ok = sum(1 for x in per_week if x > 0)
            util = _ratio(busy, work)
            n_days = len(counted)
            cells[slot_key(wd, slot)] = {
                "weekday": wd,
                "slot": hhmm(slot),
                "mark": grade(ok, n_days, len(total_dates)),
                "ok": ok,
                "counted": n_days,
                "total": len(total_dates),
                "per_week": per_week,
                "free_avg": _ratio(sum(per_week), n_days),
                "work_min": work,
                "busy_min": busy,
                "utilization": util,
                "crowded": util is not None and util >= CROWDED_UTILIZATION,
                "starts": starts,
                "starts_avg": _ratio(starts, n_days),
                "busy_staff_avg": _ratio(busy_staff, n_days),
                "pairs": pairs,
                "tight_ratio": _ratio(tight, pairs),
                "fixed_ratio": _ratio(fixed, starts),
            }
        weekday_rows.append(
            {
                "weekday": wd,
                "days": len(counted),
                "total": len(total_dates),
                "visits_per_day": _ratio(day_visits, len(counted)),
                "utilization": _ratio(wd_busy, wd_work),
            }
        )
    rated = [c for c in cells.values() if c["utilization"] is not None]
    by_util = sorted(rated, key=lambda c: (-c["utilization"], c["weekday"], c["slot"]))
    started = [c for c in cells.values() if c["starts_avg"]]
    by_starts = sorted(started, key=lambda c: (-c["starts_avg"], c["weekday"], c["slot"]))
    all_work = sum(c["work_min"] for c in cells.values())
    all_busy = sum(c["busy_min"] for c in cells.values())
    return {
        "cells": cells,
        "weekday_summary": weekday_rows,
        "peaks": {
            "utilization": [
                {"weekday": c["weekday"], "slot": c["slot"], "value": c["utilization"]}
                for c in by_util[:PEAK_COUNT]
            ],
            "starts": [
                {"weekday": c["weekday"], "slot": c["slot"], "value": c["starts_avg"]}
                for c in by_starts[:PEAK_COUNT]
            ],
        },
        "utilization": _ratio(all_busy, all_work),
        "counts": {
            "ok": sum(1 for c in cells.values() if c["mark"] == "○"),
            "mid": sum(1 for c in cells.values() if c["mark"] == "△"),
            "ng": sum(1 for c in cells.values() if c["mark"] == "×"),
            "none": sum(1 for c in cells.values() if c["mark"] == "－"),
            "crowded": sum(1 for c in cells.values() if c["crowded"]),
        },
    }


def add_trend(current: dict[str, Any], previous: dict[str, Any]) -> None:
    """前の月の稼働率との差 (ポイント) と ▲▼ を ``current`` のマスに書き足す。"""
    for key, cell in current["cells"].items():
        prev = previous["cells"].get(key)
        prev_util = prev["utilization"] if prev else None
        cell["prev_utilization"] = prev_util
        if cell["utilization"] is None or prev_util is None:
            cell["trend_points"] = None
            cell["trend"] = None
            continue
        points = round((cell["utilization"] - prev_util) * 100, 1)
        cell["trend_points"] = points
        cell["trend"] = (
            TREND_UP if points >= TREND_POINTS else TREND_DOWN if points <= -TREND_POINTS else None
        )


__all__ = [
    "BUSY_DAY_VISITS",
    "CROWDED_UTILIZATION",
    "OPEN_DAY_MIN_VISITS",
    "SLOT_FROM",
    "SLOT_MIN",
    "SLOT_TO",
    "TREND_DOWN",
    "TREND_POINTS",
    "TREND_UP",
    "DaySlots",
    "MonthStaffDay",
    "MonthVisit",
    "add_trend",
    "day_slots",
    "month_rules",
    "month_slots",
    "slot_key",
    "summarize_month",
]
