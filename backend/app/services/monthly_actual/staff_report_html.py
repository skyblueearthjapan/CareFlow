"""月の看護師別の実績の A4 印刷 HTML — 純関数レンダラ.

作り方は打刻履歴の A4 (``services/checkin/history_report_html.py``) に合わせる: 同じ CSS と
用紙へ流し込むスクリプトを使い、サーバは表を含む完全な HTML を返す (スクリプトが
動かなくても読めて印刷できる)。印刷は上のバーの「印刷 / PDF に保存」でブラウザの印刷へ。

1 枚目: 要約 (件数)・この表の読み方・看護師別の表 (訪問／時間と移動の 2 表)。
続いて看護師ごとに 1 枚 (週ごとの件数・患者数・提供時間・移動)、全件の一覧、要確認。
DB にも現在時刻にも触らない (出力時刻は ``generated_at`` で受け取る)。
事業所の名前はコードに書かず、事業所の情報 (``station_name``) から入れる。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from app.services.checkin.actuals import JST
from app.services.checkin.history_report_html import _CSS, _SCRIPT, _e, _section, _thead
from app.services.monthly_actual import judge as rules
from app.services.monthly_actual.staff_report import (
    RULE_NO_DEPARTURE,
    RULE_SHORT_STAY,
    MonthStaffReport,
    StaffMonth,
    VisitLine,
)

REPORT_TITLE = "看護師別の月の実績"
_WEEKDAYS = "月火水木金土日"
_FOOTER_NOTE = (
    "到着・退出はらく助の実績（QR・サイン。後から合わせた時刻を含む）です。"
    "移動距離は拠点と訪問先を直線でつないだ目安です。"
)

#: 看護師別の表 (見出し, 幅)。
_VISIT_COLS = (
    ("看護師", "36mm"),
    ("拠点", "16mm"),
    ("訪問の件数", "17mm"),
    ("稼働日数", "15mm"),
    ("1 日あたり", "16mm"),
    ("利用者の数", "17mm"),
    ("週の平均利用者数", "22mm"),
    ("2 名訪問・同行", ""),
)
_TIME_COLS = (
    ("看護師", "36mm"),
    ("提供時間の合計", "22mm"),
    ("1 件の平均滞在", "21mm"),
    ("到着＋35 分", "18mm"),
    ("移動の合計", "19mm"),
    ("1 日あたり", "17mm"),
    ("1 件あたり", "17mm"),
    ("備考", ""),
)
_WEEK_COLS = (
    ("週", "36mm"),
    ("稼働日数", "18mm"),
    ("訪問の件数", "20mm"),
    ("利用者の数", "20mm"),
    ("提供時間", "22mm"),
    ("移動", "20mm"),
    ("1 件あたりの移動", ""),
)
_LINE_COLS = (
    ("日付", "17mm"),
    ("看護師", "25mm"),
    ("利用者", "28mm"),
    ("予定", "22mm"),
    ("到着", "13mm"),
    ("退出", "13mm"),
    ("提供", "13mm"),
    ("備考", ""),
)
_ASK_COLS = (
    ("日付", "17mm"),
    ("看護師", "28mm"),
    ("利用者", "30mm"),
    ("予定", "22mm"),
    ("確認すること", ""),
)


def _date_cell(d: date) -> str:
    return f"{d.month}/{d.day} ({_WEEKDAYS[d.weekday()]})"


def _week_cell(ws: date, we: date) -> str:
    return f"{ws.month}/{ws.day}（{_WEEKDAYS[ws.weekday()]}）〜{we.month}/{we.day}"


def _num(value: float | int | None, unit: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:g}{unit}" if isinstance(value, float) else f"{value}{unit}"


def _km(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} km"


def _td(value: str, cls: str = "n") -> str:
    return f'<td class="{cls}">{_e(value)}</td>'


def _rule_note(line: VisitLine) -> str:
    parts: list[str] = []
    if line.rule == RULE_SHORT_STAY:
        parts.append(
            f"滞在 {line.stay_min} 分（{rules.SHORT_STAY_MINUTES} 分未満）→ 到着＋"
            f"{rules.SHORT_STAY_END_MINUTES} 分"
        )
    elif line.rule == RULE_NO_DEPARTURE:
        parts.append(f"退出の記録なし → 到着＋{rules.SHORT_STAY_END_MINUTES} 分")
    if line.arrival is None and not line.future:
        parts.append("到着の記録なし（提供時間に入れていません）")
    if line.no_show:
        parts.append("未訪問の記録あり")
    return "／".join(parts)


def _staff_note(s: StaffMonth) -> str:
    parts: list[str] = []
    if s.no_arrival_count:
        parts.append(f"到着なし {s.no_arrival_count} 件")
    if s.skipped_legs:
        parts.append(f"座標の無い区間 {s.skipped_legs}")
    return "／".join(parts)


def _ask_lines(report: MonthStaffReport) -> list[tuple[VisitLine, str]]:
    """要確認 (1 訪問 1 行・理由は「／」でつなぐ)。今日より後の日の到着なしは出さない。"""
    asks: list[tuple[VisitLine, str]] = []
    for line in report.visits:
        reasons: list[str] = []
        if line.arrival is None and not line.future:
            reasons.append("到着の記録がありません。提供時間に入れていません。")
        elif line.rule == RULE_NO_DEPARTURE:
            reasons.append(
                f"退出の記録がありません。到着＋{rules.SHORT_STAY_END_MINUTES} 分にしました。"
            )
        if line.no_show:
            reasons.append("未訪問の記録があります。件数には数えています。")
        if reasons:
            asks.append((line, "／".join(reasons)))
    return asks


def _until_note(report: MonthStaffReport) -> str | None:
    """月の途中のときの注記 (到着の記録なしは今日まで)。"""
    if report.counted_until is None:
        return None
    d = report.counted_until
    return (
        f"月の途中です。到着の記録なしの数と要確認は、今日（{d.month}/{d.day}）までの訪問です。"
        "これより後の日の訪問は数えていません。"
    )


def _cover(report: MonthStaffReport) -> str:
    t = report.totals()
    kpi = [
        ("看護師", f"{t['staff_count']}", "名"),
        ("訪問", f"{t['visits']}", "件"),
        ("2 名訪問・同行", f"{t['accompany_visits']}", "件"),
        ("提供時間", f"{t['provided_min_total']:,}", "分"),
        ("到着＋35 分", f"{t['short_stay_count'] + t['no_departure_count']}", "件"),
        ("移動", f"{t['km_total']:.1f}", "km"),
    ]
    notes = [
        "訪問の件数は、主担当（空ならコースの担当）として受け持った訪問です。取消・削除は数えません。",
        "2 名訪問・同行は、2 人目（副担当・同行）として入った件数です。訪問の件数・稼働日数・"
        "利用者の数・移動には入れていません。",
        "稼働日数は訪問のあった日数、1 日あたりは訪問の件数 ÷ 稼働日数です。",
        "週の平均利用者数は、月曜はじまりの週（この月に入る日だけ）ごとの利用者の人数を、"
        "訪問のあった週で平均した値です。",
        f"提供時間は、らく助の到着〜退出です。{rules.SHORT_STAY_MINUTES} 分未満の訪問と退出の記録が"
        f"無い訪問は、決まりどおり到着＋{rules.SHORT_STAY_END_MINUTES} 分にしています。"
        "1 件の平均滞在は、提供時間の合計 ÷ 到着の記録がある件数です。",
        "移動は、所属の拠点 → 1 件目 → … → 最後 → 拠点 を直線でつないだ距離の合計（日ごと）です。"
        "座標の無い区間は数えていません。",
    ]
    until = _until_note(report)
    if until:
        notes.insert(0, until)
    visit_rows = "".join(
        "<tr>"
        + _td(s.name, "")
        + _td(s.office_short or "—", "")
        + _td(_num(s.visits, " 件"))
        + _td(_num(s.days, " 日"))
        + _td(_num(s.per_day, " 件"))
        + _td(_num(s.patients, " 名"))
        + _td(_num(s.weekly_patients_avg, " 名"))
        + _td(_num(s.accompany_visits, " 件"))
        + "</tr>"
        for s in report.staff
    )
    time_rows = "".join(
        "<tr>"
        + _td(s.name, "")
        + _td(f"{s.provided_min_total:,} 分")
        + _td(_num(s.avg_stay_min, " 分"))
        + _td(_num(s.short_stay_count + s.no_departure_count, " 件"))
        + _td(_km(s.km_total))
        + _td(_km(s.km_per_day))
        + _td(_km(s.km_per_visit))
        + _td(_staff_note(s), "rm")
        + "</tr>"
        for s in report.staff
    )
    return (
        '<div><div class="kpi">'
        + "".join(f"<span>{_e(n)}<b>{_e(v)}</b> {_e(u)}</span>" for n, v, u in kpi)
        + "</div></div>\n"
        "<div><h2>この表の読み方</h2><ul>"
        + "".join(f"<li>{_e(n)}</li>" for n in notes)
        + "</ul></div>\n"
        + _section(
            "看護師別（訪問）",
            f"{len(report.staff)} 名",
            f"<table>{_thead(_VISIT_COLS)}<tbody>{visit_rows}</tbody></table>",
        )
        + _section(
            "看護師別（時間と移動）",
            f"到着＋35 分 = {rules.SHORT_STAY_MINUTES} 分未満と退出の記録なし",
            f"<table>{_thead(_TIME_COLS)}<tbody>{time_rows}</tbody></table>",
        )
        + "\n"
    )


def _per_staff(report: MonthStaffReport) -> str:
    parts: list[str] = []
    for s in report.staff:
        rows = "".join(
            "<tr>"
            + _td(_week_cell(w.start, w.end), "")
            + _td(_num(w.days, " 日"))
            + _td(_num(w.visits, " 件"))
            + _td(_num(w.patients, " 名"))
            + _td(f"{w.provided_min:,} 分")
            + _td(_km(w.km))
            + _td(_km(round(w.km / w.visits, 2) if w.visits else None))
            + "</tr>"
            for w in s.weeks
        )
        total = (
            '<tr style="font-weight:700">'
            + _td("月の合計", "")
            + _td(_num(s.days, " 日"))
            + _td(_num(s.visits, " 件"))
            + _td(_num(s.patients, " 名"))
            + _td(f"{s.provided_min_total:,} 分")
            + _td(_km(s.km_total))
            + _td(_km(s.km_per_visit))
            + "</tr>"
        )
        sub = (
            f"{s.office_short or '—'}・訪問 {s.visits} 件・週の平均利用者数 "
            f"{_num(s.weekly_patients_avg, ' 名')}・2 名訪問・同行 {s.accompany_visits} 件・"
            f"平均滞在 {_num(s.avg_stay_min, ' 分')}・1 日の移動 {_km(s.km_per_day)}"
        )
        parts.append(
            _section(
                s.name,
                sub,
                f"<table>{_thead(_WEEK_COLS)}<tbody>{rows}{total}</tbody></table>",
                page_break=True,
            )
        )
    return "".join(parts)


def _line_rows(lines: Sequence[VisitLine]) -> str:
    return "".join(
        "<tr>"
        + _td(_date_cell(ln.day), "")
        + _td(ln.staff_name, "")
        + _td(ln.patient or "—", "")
        + _td(ln.plan, "")
        + _td(ln.arrival or "—", "b")
        + _td(ln.departure or "—", "b")
        + _td(_num(ln.provided_min, "分"), "")
        + _td(_rule_note(ln), "rm")
        + "</tr>"
        for ln in lines
    )


def _all_lines(report: MonthStaffReport) -> str:
    if not report.visits:
        return '<div><h2>全件の一覧</h2><div class="empty">この月の訪問はありません。</div></div>'
    return _section(
        "全件の一覧",
        f"{len(report.visits)} 件（日付順）",
        f"<table>{_thead(_LINE_COLS)}<tbody>{_line_rows(report.visits)}</tbody></table>",
        page_break=True,
    )


def _asks(report: MonthStaffReport) -> str:
    asks = _ask_lines(report)
    skipped = [s for s in report.staff if s.skipped_legs]
    parts: list[str] = []
    if asks:
        rows = "".join(
            "<tr>"
            + _td(_date_cell(ln.day), "")
            + _td(ln.staff_name, "")
            + _td(ln.patient or "—", "")
            + _td(ln.plan, "")
            + _td(text, "rm")
            + "</tr>"
            for ln, text in asks
        )
        parts.append(
            _section(
                "要確認",
                f"{len(asks)} 件",
                f"<table>{_thead(_ASK_COLS)}<tbody>{rows}</tbody></table>",
                page_break=True,
            )
        )
    else:
        parts.append(
            '<div><h2>要確認</h2><div class="empty">確認することはありません。</div></div>'
        )
    if skipped:
        parts.append(
            "<div><h2>移動の座標が無い区間</h2><ul>"
            + "".join(
                f"<li>{_e(s.name)}：{s.skipped_legs} 区間（拠点か訪問先の座標が無いため、"
                "移動に数えていません）</li>"
                for s in skipped
            )
            + "</ul></div>"
        )
    return "".join(parts)


def render_month_staff_report_html(
    report: MonthStaffReport,
    *,
    station_name: str | None,
    generated_at: datetime,
) -> str:
    """月の看護師別の実績 → 自己完結 HTML (A4 縦)。"""
    year, mon = (int(x) for x in report.month.split("-"))
    label = f"{year} 年 {mon} 月"
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    generated = generated_at.astimezone(JST).strftime("%Y/%m/%d %H:%M")
    org = [station_name] if station_name else []
    org.append(f"出力 {generated}")
    title = f"{REPORT_TITLE}　{label}"
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(REPORT_TITLE)} {_e(label)}</title>\n"
        "<style>" + _CSS + "</style>\n"
        "</head>\n<body>\n"
        f'<div class="bar"><b>{_e(REPORT_TITLE)} {_e(label)}</b><span id="info"></span>'
        '<span class="sp"></span>'
        '<button onclick="window.print()">印刷 / PDF に保存</button></div>\n'
        '<div class="sheet" id="flow">\n'
        f"<header><h1>{_e(title)}</h1>"
        f'<div class="org">{"<br>".join(_e(c) for c in org)}</div></header>\n'
        '<div class="body">\n'
        + _cover(report)
        + _per_staff(report)
        + _all_lines(report)
        + _asks(report)
        + "\n</div>\n"
        f'<footer><span>{_e(_FOOTER_NOTE)}</span><span class="pg"></span></footer>\n'
        "</div>\n"
        '<div id="sheets"></div>\n'
        "<script>" + _SCRIPT + "</script>\n"
        "</body>\n</html>"
    )


__all__ = ["REPORT_TITLE", "render_month_staff_report_html"]
