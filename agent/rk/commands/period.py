"""rk period scope — 期間（日付の範囲）を週ごとの「組む日／触らない日」に分ける（読むだけ・agent-period-build-design §3-2）。

  rk period scope --from 2026-12-01 --to 2026-12-31     … 範囲そのまま
  rk period scope --month 2026-12                       … その月の 1 日〜末日（PO 2026-11-16）

返す JSON（スキルの手順がこの形を読む。キーを変えない）:
  {from, to, today,
   weeks: [{monday, iso_year, iso_week,
            date_from, date_to,          … この週の組む日の始め・終わり（無ければ null）。clear-week・generate-week にそのまま渡す
            build_days: [...],           … 範囲の中で、今日より前でない日
            frozen_days: [...],          … この週の、範囲の外の日と過ぎた日（理由は days[].frozen_reason）
            days: [{date, weekday, in_scope, past, build, frozen_reason, visits, unassigned, checked_in, cancelled, staff_off}],
            level_args}],                … rk level week に渡す引数（組む日が無ければ null）
   summary: [日本語の短い行]}
「過ぎた日」= 日本時間の今日より前。過ぎた日は組む日にしない。件数は読み取り専用の SQL（sql/period_days.sql）から。
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from rk import RkError

MAX_DAYS = 62  # 2 か月ほど。これより長い範囲は分けて読む
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WD_JA = "月火水木金土日"
JST = timezone(timedelta(hours=9))
COUNT_KEYS = ("visits", "unassigned", "checked_in", "cancelled", "staff_off")


def today_jst() -> date:
    return datetime.now(JST).date()


def _d(text: str, what: str) -> date:
    try:
        if not isinstance(text, str) or len(text) != 10:
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise RkError(f"{what} は YYYY-MM-DD の日付です（{text}）") from None


def month_range(month: str) -> tuple[date, date]:
    """'2026-12' → (2026-12-01, 2026-12-31)。"""
    try:
        y, m = (int(x) for x in str(month).split("-"))
        last = calendar.monthrange(y, m)[1]
        return date(y, m, 1), date(y, m, last)
    except (ValueError, TypeError):
        raise RkError(f"--month は YYYY-MM の形です（{month}）") from None


def check_span(d_from: date, d_to: date) -> None:
    if d_from > d_to:
        raise RkError(f"始めの日 {d_from} が終わりの日 {d_to} より後です")
    n = (d_to - d_from).days + 1
    if n > MAX_DAYS:
        raise RkError(f"範囲が長すぎます（{n} 日）。{MAX_DAYS} 日までにして、分けて読んでください")


def label(d: date) -> str:
    return f"{d.month}/{d.day}（{WD_JA[d.weekday()]}）"


def touched_span(d_from: date, d_to: date) -> tuple[date, date]:
    """範囲がかかる週の、最初の月曜〜最後の日曜（件数を読む範囲）。"""
    return d_from - timedelta(days=d_from.weekday()), d_to + timedelta(days=6 - d_to.weekday())


def build_scope(d_from: date, d_to: date, today: date, counts: dict[str, dict] | None = None) -> dict:
    """日付の計算だけ（本番に触らない）。counts = {"YYYY-MM-DD": {visits, unassigned, ...}}（無い日は 0）。"""
    check_span(d_from, d_to)
    counts = counts or {}
    first, last = touched_span(d_from, d_to)
    weeks = []
    monday = first
    while monday <= last:
        iso_year, iso_week, _ = monday.isocalendar()
        days, build, frozen = [], [], []
        for i in range(7):
            d = monday + timedelta(days=i)
            in_scope = d_from <= d <= d_to
            past = d < today
            is_build = in_scope and not past
            reason = None if is_build else ("out_of_scope" if not in_scope else "past")
            c = counts.get(d.isoformat()) or {}
            days.append({"date": d.isoformat(), "weekday": WEEKDAYS[i], "in_scope": in_scope, "past": past,
                         "build": is_build, "frozen_reason": reason,
                         **{k: int(c.get(k) or 0) for k in COUNT_KEYS}})
            (build if is_build else frozen).append(d.isoformat())
        weeks.append({
            "monday": monday.isoformat(), "iso_year": iso_year, "iso_week": iso_week,
            "date_from": build[0] if build else None, "date_to": build[-1] if build else None,
            "build_days": build, "frozen_days": frozen, "days": days,
            "level_args": (f"--week {monday.isoformat()} " + " ".join(f"--day {x}" for x in build)) if build else None,
        })
        monday += timedelta(days=7)
    return {"from": d_from.isoformat(), "to": d_to.isoformat(), "today": today.isoformat(),
            "weeks": weeks, "summary": summary_lines(d_from, d_to, weeks)}


def summary_lines(d_from: date, d_to: date, weeks: list[dict]) -> list[str]:
    all_days = [x for w in weeks for x in w["days"]]
    build = [x for x in all_days if x["build"]]
    outside = [x for x in all_days if x["frozen_reason"] == "out_of_scope"]
    past = [x for x in all_days if x["frozen_reason"] == "past"]
    n = (d_to - d_from).days + 1
    lines = [f"{label(d_from)}〜{label(d_to)}（{n} 日）は {len(weeks)} 週にまたがります"]
    if build:
        lines.append(f"組む日 {len(build)} 日: {label(date.fromisoformat(build[0]['date']))}〜"
                     f"{label(date.fromisoformat(build[-1]['date']))}（週ごとの date_from〜date_to）")
    else:
        lines.append("組む日はありません（範囲の日はすべて過ぎています）")
    lines.append(f"触らない日 {len(outside) + len(past)} 日（範囲の外 {len(outside)} 日・過ぎた日 {len(past)} 日）")
    has = [x for x in build if x["visits"]]
    if has:
        lines.append(f"組む日のうち、もう予定がある日 {len(has)} 日（訪問 {sum(x['visits'] for x in has)} 件・"
                     f"担当なし {sum(x['unassigned'] for x in has)} 件）: "
                     + "・".join(label(date.fromisoformat(x["date"])) for x in has))
    else:
        lines.append("組む日には、まだ予定がありません（型から作れます）")
    chk = [x for x in build if x["checked_in"]]
    if chk:
        lines.append("組む日のうち、打刻・実績のある訪問がある日（その訪問は空にしても残ります）: "
                     + "・".join(f"{label(date.fromisoformat(x['date']))} {x['checked_in']} 件" for x in chk))
    return lines


def read_counts(run_sql: Callable[[str, dict], Any], d_from: date, d_to: date) -> dict[str, dict]:
    first, last = touched_span(d_from, d_to)
    rows = run_sql("period_days", {"date_from": first.isoformat(), "date_to": last.isoformat()}) or []
    if not isinstance(rows, list):
        raise RkError("日ごとの件数を読めませんでした（SQL の結果が想定外）")
    return {str(r.get("visit_date"))[:10]: r for r in rows if isinstance(r, dict)}


def parse_range(args) -> tuple[date, date]:
    if args.month:
        if args.date_from or args.date_to:
            raise RkError("--month と --from/--to は一緒に使えません")
        return month_range(args.month)
    if not args.date_from or not args.date_to:
        raise RkError("--from と --to（または --month YYYY-MM）が要ります")
    return _d(args.date_from, "--from"), _d(args.date_to, "--to")


def cmd_scope(args) -> int:
    from rk import sqlrun
    d_from, d_to = parse_range(args)
    check_span(d_from, d_to)  # つなぐ前に範囲を確かめる

    def run(name: str, params: dict) -> Any:
        text = sqlrun.sql_path(args.cfg, name).read_text(encoding="utf-8")
        filled, _ = sqlrun.fill_params(text, params)
        return sqlrun.run_sql(args.cfg, text, filled, name=name)

    out = build_scope(d_from, d_to, today_jst(), read_counts(run, d_from, d_to))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("period", help="期間を週ごとの「組む日／触らない日」に分ける（読むだけ）")
    sub = p.add_subparsers(dest="period_cmd", metavar="<scope>")
    q = sub.add_parser("scope", help="期間の週ごとの組む日・触らない日・日ごとの件数")
    q.add_argument("--from", dest="date_from", help="始めの日 YYYY-MM-DD")
    q.add_argument("--to", dest="date_to", help="終わりの日 YYYY-MM-DD")
    q.add_argument("--month", help="その月の 1 日〜末日（YYYY-MM）")
    q.set_defaults(func=cmd_scope)
    p.set_defaults(func=lambda args: (p.print_help(), 1)[1])
