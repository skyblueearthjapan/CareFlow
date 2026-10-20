"""休みの種類と境目の時刻、カイポケの件名からの種類の判定 (1 か所にまとめる).

正典: ``docs/plans/shared-foundation-leave-acceptance-2026-10-20.md`` §0-1。
13:00・12:30・``"off"`` などの値はここだけに書き、ほかはここ (フロントは
``frontend/lib/leave.ts``) を読む。
"""

from __future__ import annotations

import unicodedata
from datetime import time
from typing import Final, Literal

from app.services.scheduling.constants import DEFAULT_BUSINESS_END, DEFAULT_BUSINESS_START

#: 終日の休み (有給も終日休み)。
FULL_DAY_OFF: Final = frozenset({"off", "paid_off"})
#: 半日の休み。
HALF_DAY_OFF: Final = frozenset({"am_off", "pm_off"})
#: 休みの種類 (``custom_time`` は休みではない)。
LEAVE_TYPES: Final = FULL_DAY_OFF | HALF_DAY_OFF

#: 午前休の人はこの時刻から勤務。
AM_OFF_UNTIL: Final = time(13, 0)
#: 午後休の人はこの時刻から休み。
PM_OFF_FROM: Final = time(12, 30)

#: カイポケの件名から決めた種類。``unknown`` = 休みの語はあるが種類を決められない。
KaipokeLeaveKind = Literal["off", "am_off", "pm_off", "paid_off", "unknown"]

#: 「休」を含むが休みではない語 (勤務・業務の予定)。先に取り除いてから休みの語を探す。
LEAVE_TITLE_EXCLUSIONS: Final[tuple[str, ...]] = ("休憩", "休日出勤", "休日当番", "休薬")
#: これを含めば休みの件名 (「休み」「公休」「夏季休暇」などは「休」で拾う)。
LEAVE_TITLE_MARKERS: Final[tuple[str, ...]] = (
    "休",
    "有給",
    "有休",
    "年休",
    "欠勤",
    "忌引",
    "病欠",
)
#: 有給 (「年休」= 年次有給休暇)。
_PAID_WORDS: Final = ("有給", "有休", "年休")
#: 午前休・午後休 (NFKC・大文字にしてから探す)。
_AM_WORDS: Final = ("AM", "午前")
_PM_WORDS: Final = ("PM", "午後")
#: 終日の休み (公休) とみなす語。
_FULL_DAY_WORDS: Final = ("代休", "欠勤", "病欠", "忌引", "公休")

#: 時刻で決めるときの幅 (分)。始まりは勤務の始まり＋30 分以内、終わりは勤務の終わり−60 分以降。
_START_SLACK_MIN: Final = 30
_END_SLACK_MIN: Final = 60
#: 午前休の終わり・午後休の始まりの幅。
_AM_END_RANGE: Final = (time(12, 0), time(14, 0))
_PM_START_RANGE: Final = (time(12, 0), time(13, 30))


def leave_window(
    leave_type: str | None, shift_start: time, shift_end: time
) -> tuple[time, time] | None:
    """休みの種類と勤務時間 → その日に働ける時間 (開始, 終了)。終日休みなら None。

    休みでない種類 (``custom_time``・None など) は勤務時間をそのまま返す。
    半休で働ける時間が残らないとき (勤務が 13:00 までの人の午前休など) も None。
    """
    if leave_type in FULL_DAY_OFF:
        return None
    if leave_type == "am_off":
        start = max(shift_start, AM_OFF_UNTIL)
        return (start, shift_end) if start < shift_end else None
    if leave_type == "pm_off":
        end = min(shift_end, PM_OFF_FROM)
        return (shift_start, end) if shift_start < end else None
    return (shift_start, shift_end)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def classify_kaipoke_leave(
    subject: str | None,
    start: time | None = None,
    end: time | None = None,
    shift_start: time | None = None,
    shift_end: time | None = None,
    *,
    business_hours: tuple[time, time] = (DEFAULT_BUSINESS_START, DEFAULT_BUSINESS_END),
) -> KaipokeLeaveKind | None:
    """カイポケの個別業務の件名＋時刻 → 休みの種類。休みでなければ None。

    共通の土台 §0-1 の表を上から順に当て、最初に当たったものを返す:

    1. 「休憩」「休日出勤」「休日当番」「休薬」を除いたあと休みの語が無い → None
    2. 「有給」「有休」「年休」→ ``paid_off``
    3. 「AM」「午前」→ ``am_off``
    4. 「PM」「午後」→ ``pm_off``
    5. 「代休」「欠勤」「病欠」「忌引」「公休」→ ``off``
    6. 時刻が無い → ``off``
    7. 始まり ≤ 勤務の始まり＋30 分 かつ 終わり ≥ 勤務の終わり−60 分 → ``off``
    8. 始まり ≤ 勤務の始まり＋30 分 かつ 終わりが 12:00〜14:00 → ``am_off``
    9. 始まりが 12:00〜13:30 かつ 終わり ≥ 勤務の終わり−60 分 → ``pm_off``
    10. それ以外 → ``unknown``

    勤務時刻が無いときは ``business_hours`` (呼び出し側が ``SchedulingConfig`` の
    ``business_start/end`` を渡す・既定 9:30〜18:00) で代える。
    """
    text = unicodedata.normalize("NFKC", subject or "").strip()
    for word in LEAVE_TITLE_EXCLUSIONS:
        text = text.replace(word, "")
    if not any(word in text for word in LEAVE_TITLE_MARKERS):
        return None
    upper = text.upper()
    if any(word in upper for word in _PAID_WORDS):
        return "paid_off"
    if any(word in upper for word in _AM_WORDS):
        return "am_off"
    if any(word in upper for word in _PM_WORDS):
        return "pm_off"
    if any(word in upper for word in _FULL_DAY_WORDS):
        return "off"
    if start is None or end is None:
        return "off"
    if shift_start is None or shift_end is None:
        shift_start, shift_end = business_hours
    starts_early = _minutes(start) <= _minutes(shift_start) + _START_SLACK_MIN
    ends_late = _minutes(end) >= _minutes(shift_end) - _END_SLACK_MIN
    if starts_early and ends_late:
        return "off"
    if starts_early and _AM_END_RANGE[0] <= end <= _AM_END_RANGE[1]:
        return "am_off"
    if _PM_START_RANGE[0] <= start <= _PM_START_RANGE[1] and ends_late:
        return "pm_off"
    return "unknown"


def is_leave_event(subject: str | None) -> bool:
    """件名が休み (種類を問わない) か。``classify_kaipoke_leave`` の薄い包み。"""
    return classify_kaipoke_leave(subject) is not None


__all__ = [
    "AM_OFF_UNTIL",
    "FULL_DAY_OFF",
    "HALF_DAY_OFF",
    "LEAVE_TITLE_EXCLUSIONS",
    "LEAVE_TITLE_MARKERS",
    "LEAVE_TYPES",
    "PM_OFF_FROM",
    "KaipokeLeaveKind",
    "classify_kaipoke_leave",
    "is_leave_event",
    "leave_window",
]
