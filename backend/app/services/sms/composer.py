"""予定のお知らせ (SMS) の文面づくり (DB を読まない純粋な関数).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §1・§3・§4。

* 事業所名と電話は ``business_profile`` (``station_name``・``contact_tel``) から渡す
  (コードに事業所の名前を書かない)。
* 文面には **姓だけ** を入れる (PO 決定)。姓は ``patients.name`` の最初の空白
  (半角・全角) より前。空白が無い名前は、姓と名を分けられないので名前の全体を使う。
* 週の言い方は、送る日 (日本時間) とその週の関係で「今週の」「来週の」「M/Dからの週の」。
* 1 行 = 1 回の訪問の開始時刻 ``M/D(曜)H:MM``。同じ日・同じ時刻は 1 行 (2 名で伺う訪問・
  旧形式の 2 行持ちの 2 名体制)。同じ日に 2 回なら 2 行。

文字数と通数は見込み。確かな通数は、照会で KDDI が返す ``splitNum``。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta
from math import ceil
from typing import Literal

WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")

#: KDDI が受け付ける本文の上限 (dst=0 のとき・仕様書 ver3.1 2-1)。
MAX_SMS_CHARS = 660

#: 1 通あたりの見込みの金額 (税込・円)。確かな額は KDDI の請求書。
YEN_PER_SEGMENT = 11

# 何通分に分かれるか (仕様書「SMS の長文配信時の分割数について」・設計 §1)。
# (1 通に収まる文字数, 2 通分以上のときの 1 通あたりの文字数)。
#   docomo: 1〜70 = 1 通 / 71〜132 = 2 通 / その後 66 文字ごとに 1 通
#   au・楽天: 1〜70 = 1 通 / 71〜134 = 2 通 / その後 67 文字ごとに 1 通
# SoftBank は照会の splitNum がいつも 1 と返るので、見込みは docomo の表で数える。
# ここは見込み。確かな値は KDDI の照会が返す ``splitNum``。
SEGMENT_TABLES: dict[str, tuple[int, int]] = {
    "docomo": (70, 66),
    "au": (70, 67),
    "rakuten": (70, 67),
}
#: 見込みに使う表 (いちばん通数が多くなる docomo)。
ESTIMATE_CARRIER = "docomo"


def surname_of(name: str | None) -> str:
    """``patients.name`` から姓を取る (最初の半角・全角の空白より前・空白が無ければ全体)."""
    s = (name or "").strip()
    for i, ch in enumerate(s):
        if ch in (" ", "　"):
            return s[:i]
    return s


def count_chars(text: str) -> int:
    """通数の見込みに使う文字数 (au・docomo は改行 1 つを 2 文字と数える)."""
    return len(text) + text.count("\n")


def estimate_segments(chars: int, carrier: str = ESTIMATE_CARRIER) -> int:
    """文字数 (:func:`count_chars`) → 何通分か (見込み)."""
    single, per = SEGMENT_TABLES.get(carrier, SEGMENT_TABLES[ESTIMATE_CARRIER])
    if chars <= single:
        return 1
    return ceil(chars / per)


def week_label(week_start: date, today: date) -> str:
    """「今週の」「来週の」「M/Dからの週の」(``today`` は日本時間の送る日)."""
    this_monday = today - timedelta(days=today.weekday())
    if week_start == this_monday:
        return "今週の"
    if week_start == this_monday + timedelta(days=7):
        return "来週の"
    return f"{week_start.month}/{week_start.day}からの週の"


def format_day(d: date) -> str:
    return f"{d.month}/{d.day}({WEEKDAY_JA[d.weekday()]})"


def format_time(t: time) -> str:
    return f"{t.hour}:{t.minute:02d}"


def format_slot(d: date, t: time) -> str:
    """``12/1(火)10:00`` の形 (時は 0 を付けない・分は 2 桁)."""
    return f"{format_day(d)}{format_time(t)}"


def unique_slots(slots: Iterable[tuple[date, time]]) -> list[tuple[date, time]]:
    """日・開始時刻の重なりを 1 つにして、日・時刻の順に並べる."""
    return sorted({(d, time(t.hour, t.minute)) for d, t in slots})


def slots_to_json(slots: Sequence[tuple[date, time]]) -> list[dict[str, str]]:
    """``patient_week_notices.visits`` に残す形 ``[{"date": "2026-12-01", "start": "10:00"}]``."""
    return [{"date": d.isoformat(), "start": f"{t.hour:02d}:{t.minute:02d}"} for d, t in slots]


def compose_week_notice(
    *,
    station_name: str,
    contact_tel: str,
    surname: str,
    week_start: date,
    today: date,
    slots: Sequence[tuple[date, time]],
) -> str:
    """週のお知らせ (丁寧・日ごとに改行・設計 §4)."""
    lines = [
        f"{station_name} です。{surname}様の{week_label(week_start, today)}"
        "ご訪問予定をお知らせします。"
    ]
    lines.extend(format_slot(d, t) for d, t in unique_slots(slots))
    lines.append(
        f"時刻は前後することがあります。ご都合が悪いときは{contact_tel}までご連絡ください。"
    )
    return "\n".join(lines)


ChangeKind = Literal["time", "date", "cancel", "add"]


@dataclass(frozen=True)
class VisitChange:
    """1 つの変更. ``time``/``date`` は前 (``old_*``) と後 (``new_*``)、
    ``cancel`` は前だけ、``add`` は後だけを持つ."""

    kind: ChangeKind
    old_date: date | None = None
    old_start: time | None = None
    new_date: date | None = None
    new_start: time | None = None


def _change_line(c: VisitChange) -> str:
    if c.kind == "time":
        assert c.old_date and c.old_start and c.new_start
        return f"{format_slot(c.old_date, c.old_start)}→{format_time(c.new_start)}"
    if c.kind == "date":
        assert c.old_date and c.old_start and c.new_date and c.new_start
        return f"{format_slot(c.old_date, c.old_start)}→{format_slot(c.new_date, c.new_start)}"
    if c.kind == "cancel":
        assert c.old_date and c.old_start
        return f"{format_slot(c.old_date, c.old_start)} お休み"
    assert c.new_date and c.new_start
    return f"{format_slot(c.new_date, c.new_start)} 追加"


def compose_change_notice(
    *,
    station_name: str,
    contact_tel: str,
    surname: str,
    changes: Sequence[VisitChange],
) -> str:
    """変更のお知らせ (設計 §4).

    変更が 1 つなら設計の 3 つの形 (時刻・日の変更 / お休み / 追加)。
    2 つ以上なら「ご訪問の変更です。」の後に 1 行ずつ並べる。
    頭の【】には ``station_name`` をそのまま入れる (短い名前の設定はまだ無い)。
    """
    if not changes:
        raise ValueError("changes is empty")
    head = f"【{station_name}】{surname}様"
    if len(changes) == 1:
        c = changes[0]
        if c.kind in ("time", "date"):
            return f"{head} ご訪問の変更です。{_change_line(c)} ご都合が悪いときは{contact_tel}へ"
        if c.kind == "cancel":
            assert c.old_date and c.old_start
            return (
                f"{head} {format_slot(c.old_date, c.old_start)}のご訪問はお休みになりました。"
                f"ご不明な点は{contact_tel}へ"
            )
        assert c.new_date and c.new_start
        return (
            f"{head} {format_slot(c.new_date, c.new_start)}にご訪問が加わりました。"
            f"ご都合が悪いときは{contact_tel}へ"
        )
    lines = [f"{head} ご訪問の変更です。"]
    lines.extend(_change_line(c) for c in changes)
    lines.append(f"ご都合が悪いときは{contact_tel}へ")
    return "\n".join(lines)
