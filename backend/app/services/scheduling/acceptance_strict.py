"""受け入れ枠を「数週続けて空いているか」で出す — 判定の中身 (DB も RPA も使わない純粋な関数).

正典: ``docs/plans/acceptance-strict-design-2026-10-20.md`` §2 (改訂 3)。

* サーバーは職員 × 日 × 時台の「入れる」を、休みの扱いの両方 (``real`` / ``ignore``) で
  一度に作る (``compute_strict``)。担い手・見る週・休みの扱いは画面が集計する。
* 画面と同じ集計 (``summarize``) もここに置く (試験で試算と比べるため。フロントの
  ``frontend/lib/acceptanceStrict.ts`` と同じ決まり)。
* 休みの種類・境目・件名の判定は ``app.core.leave`` だけを使う (値を書き写さない)。

時刻はすべて「その日の 0:00 からの分」。区間は [開始, 終了) で、端が触れるだけは重ならない。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any, Final

from app.core import leave
from app.services.scheduling.config import SchedulingConfig

#: 新しい訪問の長さ (分・PO 決定 3)。
VISIT_MIN: Final = 35
#: 1 日の上限 (件・職員・マネージャー・新人とも。PO 決定 2)。
CAP: Final = 7
#: 移動の見込み (分)。``visit_buffer_min`` と足して前後の間 (既定 20 分) にする。
TRAVEL_GUESS_MIN: Final = 12
#: 開始の候補の刻み (分)。
STEP: Final = 5
#: 表に出す時間台 (両端含む)。8 時台の開始も計算するが表には出さない。
HOUR_FROM: Final = 9
HOUR_TO: Final = 17
#: 見る週の最大 (今週を含む直近)。4・6 週はこの一部。
MAX_WEEKS: Final = 8
#: 見る週の選択肢と、△ の下限の表 (それ以外の週数は ceil(週数×5/8))。
WEEK_CHOICES: Final = (4, 6, 8)
_TRIANGLE_TABLE: Final = {4: 3, 6: 4, 8: 5}

_DAY_MINUTES: Final = 24 * 60

#: 休みの種類の名前 (画面の内訳に出す)。
LEAVE_LABELS: Final = {"off": "休み", "paid_off": "有給", "am_off": "午前休", "pm_off": "午後休"}
#: 理由の種類 (職員の権限の応答で丸めるのに使う)。
WHY_FULL: Final = "full"
WHY_HALF: Final = "half"
WHY_TIME: Final = "time"
WHY_UNKNOWN: Final = "unknown"

#: マスの記号。
MARK_OK: Final = "○"
MARK_MID: Final = "△"
MARK_NG: Final = "×"
MARK_NONE: Final = "－"

Interval = tuple[int, int]


def minutes_of(value: time) -> int:
    return value.hour * 60 + value.minute


def time_of(value: int) -> time:
    value = max(0, min(value, _DAY_MINUTES - 1))
    return time(value // 60, value % 60)


def hhmm(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


@dataclass(frozen=True)
class StrictRules:
    """判定の決まった値 (画面では切り替えない)。``cap`` は試験で試算 (6 件) に合わせて渡せる。"""

    buffer_min: int
    lunch_min: int
    lunch_from: int
    lunch_to: int
    business_start: int
    business_end: int
    visit_min: int = VISIT_MIN
    cap: int = CAP
    travel_guess_min: int = TRAVEL_GUESS_MIN
    step: int = STEP
    hour_from: int = HOUR_FROM
    hour_to: int = HOUR_TO

    @property
    def pad(self) -> int:
        return self.buffer_min + self.travel_guess_min

    @classmethod
    def from_config(cls, config: SchedulingConfig, *, cap: int = CAP) -> StrictRules:
        return cls(
            buffer_min=config.visit_buffer_min,
            lunch_min=config.lunch_duration_min,
            lunch_from=minutes_of(config.lunch_window_start),
            lunch_to=minutes_of(config.lunch_window_end),
            business_start=minutes_of(config.business_start),
            business_end=minutes_of(config.business_end),
            cap=cap,
        )

    def as_dict(self) -> dict[str, Any]:
        """API の ``rules`` (画面の決まりの文章に使う)。"""
        return {
            "visit_min": self.visit_min,
            "cap": self.cap,
            "buffer_min": self.buffer_min,
            "travel_guess_min": self.travel_guess_min,
            "lunch_min": self.lunch_min,
            "lunch_from": hhmm(self.lunch_from),
            "lunch_to": hhmm(self.lunch_to),
            "hours": [self.hour_from, self.hour_to],
        }


@dataclass(frozen=True)
class KaipokeLeave:
    """カイポケ個別業務の休み (種類は ``leave.classify_kaipoke_leave`` が決めたもの)。"""

    kind: str
    subject: str


@dataclass
class StaffDay:
    """職員 × 日の材料 (§2-2)。勤務日でない日は作らない。"""

    staff_id: str
    day: date
    #: 勤務の型の窓 (時刻が空なら営業の時刻)。
    shift: Interval
    #: 主担当 (職員名１) の件数。
    n: int = 0
    #: 職員名１〜３ のどれかに入っている訪問の時間 (PAD は足していない)。
    visits: list[Interval] = field(default_factory=list)
    #: 予定 (朝会など) の時間。前後に足さずに塞ぐ。
    events: list[Interval] = field(default_factory=list)
    #: らく助の週だけの休み・時間変更 (``staff_weekly_overrides.override_type``)。
    rk_type: str | None = None
    rk_time: Interval | None = None
    #: カイポケの休み (同じ日に 2 件以上なら、いちばん広く休む 1 件)。
    kp_off: KaipokeLeave | None = None
    #: 種類が分からない休み (時間を塞ぐ・件名を内訳に出す)。
    kp_unknown: list[tuple[int, int, str]] = field(default_factory=list)
    #: 入職前 (見る範囲の材料で、その日より前にカイポケの行を持たない)。
    not_employed: bool = False


def leave_breadth(kind: str) -> int:
    """休みの広さ (同じ日に 2 件以上あるとき、広いほうを使う)。"""
    if kind in leave.FULL_DAY_OFF:
        return 2
    if kind in leave.HALF_DAY_OFF:
        return 1
    return 0


def _why_kind(kind: str) -> str:
    return WHY_FULL if kind in leave.FULL_DAY_OFF else WHY_HALF


def _window(
    sd: StaffDay, rules: StrictRules, use_off: bool
) -> tuple[bool, int, int, str, str | None]:
    """その日の勤務の窓。(休みか, 開始, 終了, 理由, 理由の種類)。"""
    ws, we = sd.shift
    if not use_off:
        return False, ws, we, "", None
    why, why_kind = "", None
    if sd.rk_type == "custom_time" and sd.rk_time is not None:
        ws, we = sd.rk_time
        why, why_kind = "時間変更", WHY_TIME
    elif sd.rk_type in leave.LEAVE_TYPES:
        label = f"{LEAVE_LABELS.get(sd.rk_type, sd.rk_type)}（らく助）"
        w = leave.leave_window(sd.rk_type, time_of(ws), time_of(we))
        if w is None:
            return True, ws, we, label, _why_kind(sd.rk_type)
        ws, we = minutes_of(w[0]), minutes_of(w[1])
        why, why_kind = label, _why_kind(sd.rk_type)
    if sd.kp_off is not None:
        label = f"{sd.kp_off.subject}（カイポケ）"
        w = leave.leave_window(sd.kp_off.kind, time_of(sd.shift[0]), time_of(sd.shift[1]))
        if w is None:
            return True, ws, we, label, _why_kind(sd.kp_off.kind)
        ws, we = max(ws, minutes_of(w[0])), min(we, minutes_of(w[1]))
        why, why_kind = label, _why_kind(sd.kp_off.kind)
    return False, ws, we, why, why_kind


def _merge(busy: Iterable[Interval]) -> tuple[list[int], list[int]]:
    """塞がった区間を重ならない並びにまとめる (開始の並び, 終了の並び)。長さ 0 は捨てる。"""
    starts: list[int] = []
    ends: list[int] = []
    for a, b in sorted(x for x in busy if x[0] < x[1]):
        if ends and a <= ends[-1]:
            ends[-1] = max(ends[-1], b)
        else:
            starts.append(a)
            ends.append(b)
    return starts, ends


def free_hours(
    busy_visits: Sequence[Interval],
    blocks: Sequence[Interval],
    ws: int,
    we: int,
    rules: StrictRules,
) -> list[int]:
    """[ws, we) の中で新しい訪問を始められる時台 (表に出す範囲だけ)。

    既存の訪問は前後に PAD を足して塞ぎ、予定はそのまま塞ぐ。昼休みは「既存の塞がり＋
    新しい訪問 (PAD 込み)」のどれとも重ならない ``lunch_min`` 分が窓の中に残ること。
    """
    pad = rules.pad
    starts, ends = _merge([(a - pad, b + pad) for a, b in busy_visits] + list(blocks))

    def hit(a: int, b: int) -> bool:
        # 終わりが a より後の最初の区間が、b より前に始まっていれば重なる。
        i = bisect_right(ends, a)
        return i < len(starts) and starts[i] < b

    if rules.lunch_min > 0:
        lunch_starts = [
            s
            for s in range(rules.lunch_from, rules.lunch_to - rules.lunch_min + 1, rules.step)
            if not hit(s, s + rules.lunch_min)
        ]
        if not lunch_starts:
            return []
        first_lunch, last_lunch = lunch_starts[0], lunch_starts[-1]
    hours: set[int] = set()
    for t in range(ws, we - rules.visit_min + 1, rules.step):
        hour = t // 60
        if hour < rules.hour_from or hour > rules.hour_to or hour in hours:
            continue
        en = t + rules.visit_min
        if hit(t, en):
            continue
        if rules.lunch_min > 0 and not (
            first_lunch + rules.lunch_min <= t - pad or last_lunch >= en + pad
        ):
            continue
        hours.add(hour)
    return sorted(hours)


def _mode(sd: StaffDay, rules: StrictRules, use_off: bool) -> dict[str, Any]:
    is_off, ws, we, why, why_kind = _window(sd, rules, use_off)
    out: dict[str, Any] = {"why": why, "why_kind": why_kind}
    if use_off:
        unknown = "・".join(dict.fromkeys(subject for _, _, subject in sd.kp_unknown))
        out["unknown_leave"] = unknown or None
    if is_off:
        return {"state": "off", "hours": [], **out}
    if sd.n >= rules.cap:
        return {"state": "full", "hours": [], **out}
    blocks = list(sd.events)
    if use_off:
        blocks += [(a, b) for a, b, _ in sd.kp_unknown]
    hours = free_hours(sd.visits, blocks, ws, we, rules)
    return {"state": "free" if hours else "tight", "hours": hours, **out}


def compute_cell(sd: StaffDay, rules: StrictRules) -> dict[str, Any]:
    """1 人 1 日のマスの材料 (休みの扱いの両方)。"""
    if sd.not_employed:
        return {"n": sd.n, "state": "not_employed"}
    return {
        "n": sd.n,
        "state": "ok",
        "real": _mode(sd, rules, use_off=True),
        "ignore": _mode(sd, rules, use_off=False),
    }


def compute_strict(
    staff_days: Iterable[StaffDay], rules: StrictRules
) -> dict[str, dict[str, dict[str, Any]]]:
    """職員 id → 日 (ISO) → マスの材料。"""
    cells: dict[str, dict[str, dict[str, Any]]] = {}
    for sd in staff_days:
        cells.setdefault(sd.staff_id, {})[sd.day.isoformat()] = compute_cell(sd, rules)
    return cells


def triangle_min(n_weeks: int) -> int:
    """△ になる最少の週数 (4→3・6→4・8→5、ずれたら ceil(週数×5/8))。"""
    return _TRIANGLE_TABLE.get(n_weeks, math.ceil(n_weeks * 5 / 8))


def grade(ok: int, counted: int, total: int) -> str:
    """見られた週 ``counted``・見る週 ``total`` のうち ``ok`` 週で入れたときの記号。"""
    if counted * 2 < total:
        return MARK_NONE
    if ok == counted:
        return MARK_OK
    if ok >= triangle_min(counted):
        return MARK_MID
    return MARK_NG


def summarize(
    days: dict[str, str],
    cells: dict[str, dict[str, dict[str, Any]]],
    staff_ids: Sequence[str],
    mondays: Sequence[date],
    mode: str,
    weekdays: Sequence[int],
    rules: StrictRules,
) -> dict[tuple[int, int], dict[str, Any]]:
    """画面と同じマスの判定 (§2-6)。``days`` は日 (ISO) → ``ok`` / ``holiday`` / ``no_data``。

    返り値: (曜日, 時台) → 記号・入れた週の数・見られた週の数・週ごとの入れる人数。
    """
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for wd in weekdays:
        counted: list[str] = []
        for monday in mondays:
            d = (monday + timedelta(days=wd)).isoformat()
            if days.get(d) != "ok":
                continue
            if not any(cells.get(s, {}).get(d, {}).get("state") == "ok" for s in staff_ids):
                continue
            counted.append(d)
        for hour in range(rules.hour_from, rules.hour_to + 1):
            per_week = []
            for d in counted:
                n_free = 0
                for s in staff_ids:
                    cell = cells.get(s, {}).get(d)
                    if cell and cell.get("state") == "ok" and hour in cell[mode]["hours"]:
                        n_free += 1
                per_week.append(n_free)
            ok = sum(1 for x in per_week if x > 0)
            out[(wd, hour)] = {
                "mark": grade(ok, len(counted), len(mondays)),
                "ok": ok,
                "counted": len(counted),
                "per_week": per_week,
            }
    return out


__all__ = [
    "CAP",
    "HOUR_FROM",
    "HOUR_TO",
    "LEAVE_LABELS",
    "MARK_MID",
    "MARK_NG",
    "MARK_NONE",
    "MARK_OK",
    "MAX_WEEKS",
    "STEP",
    "TRAVEL_GUESS_MIN",
    "VISIT_MIN",
    "WEEK_CHOICES",
    "WHY_FULL",
    "WHY_HALF",
    "WHY_TIME",
    "WHY_UNKNOWN",
    "KaipokeLeave",
    "StaffDay",
    "StrictRules",
    "compute_cell",
    "compute_strict",
    "free_hours",
    "grade",
    "leave_breadth",
    "minutes_of",
    "summarize",
    "time_of",
    "triangle_min",
]
