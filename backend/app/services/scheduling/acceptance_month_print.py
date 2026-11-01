"""受け入れ枠「月で見る」の A4 横 1 枚 (自己完結 HTML).

外部のフォント・CSS・JS を読まない 1 ファイル。ブラウザの印刷で A4 横 / PDF にする
(実現性チェックの A4 と同じ作り)。入力は ``build_month_view`` の応答 (純粋な関数)。
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

_WD = "月火水木金土日"
_JST = ZoneInfo("Asia/Tokyo")
_SOURCE_TEXT = {
    "plan": "らく助の予定で数えています（先の月）",
    "record": "らく助の訪問の記録で数えています（取消・削除を除く）",
    "mixed": "終わった日はらく助の訪問の記録、今日から先はらく助の予定で数えています",
}

_CSS = """
:root{--paper:#fff;--ink:#1e2a33;--soft:#55656f;--rule:#d7dde2;--rule2:#9aa7b0;--tint:#f3f6f8;--brand:#e15a7f;
--u0:#ffffff;--u1:#fdf3e4;--u2:#fbdcb4;--u3:#f6b77c;--u4:#e9824d;--ok:#2f7d5b;--mid:#a8691a;--ng:#55656f}
html{color-scheme:light}
body{margin:0;background:#e9ecef;color:var(--ink);font-family:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;font-size:9pt;line-height:1.45;font-feature-settings:"palt"}
.sheet{background:var(--paper);width:297mm;margin:10mm auto;padding:9mm 10mm;box-sizing:border-box;box-shadow:0 2px 14px rgba(20,30,40,.12)}
.toolbar{width:297mm;margin:8mm auto 0;display:flex;justify-content:flex-end;gap:8px}
.toolbar button{font:inherit;font-size:10pt;padding:6px 14px;border:1px solid var(--rule2);background:#fff;color:var(--ink);border-radius:3px;cursor:pointer}
.toolbar .hint{font-size:9pt;color:var(--soft);align-self:center;margin-right:auto}
h1{font-size:14pt;margin:0;line-height:1.3}
.head{display:grid;grid-template-columns:1fr auto;gap:5mm;align-items:end;padding-bottom:2mm;border-bottom:2px solid var(--ink)}
.eyebrow{font-size:8pt;letter-spacing:.1em;color:var(--soft)}
.meta{font-size:8pt;color:var(--soft);text-align:right;line-height:1.55} .meta b{color:var(--ink);font-weight:500}
.body{display:grid;grid-template-columns:minmax(0,1fr) 78mm;gap:5mm;margin-top:3mm}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
.heat th{font-size:8pt;color:var(--soft);font-weight:500;padding:.6mm 1mm;text-align:center}
.heat th.t{text-align:right;width:12mm;white-space:nowrap}
.heat td{border:1px solid #fff;height:7.6mm;padding:0 1.2mm;position:relative;text-align:center;line-height:1.1}
.heat td.u0{background:var(--u0);border-color:var(--rule)} .heat td.u1{background:var(--u1)} .heat td.u2{background:var(--u2)} .heat td.u3{background:var(--u3)} .heat td.u4{background:var(--u4)}
.heat td.none{background:var(--tint);color:var(--soft)}
.heat td.crowd{background-image:repeating-linear-gradient(135deg,rgba(168,57,47,.28) 0 1.2px,transparent 1.2px 5px)}
.heat .m{font-weight:700;font-size:10pt} .heat .m.ok{color:var(--ok)} .heat .m.mid{color:var(--mid)} .heat .m.ng{color:var(--ng)}
.heat .p{font-size:7.5pt;margin-left:1mm} .heat .tr{position:absolute;right:.6mm;top:.2mm;font-size:7pt}
.heat .tr.up{color:#a8392f} .heat .tr.down{color:#2e5570}
.side h2{font-size:9.5pt;margin:0 0 1mm;padding:.8mm 2mm;background:var(--ink);color:#fff}
.side table{margin-bottom:3mm} .side th,.side td{padding:.6mm 1.2mm;border-bottom:1px solid var(--rule);text-align:right}
.side th:first-child,.side td:first-child{text-align:left}
.side thead th{font-size:7.5pt;color:var(--soft);font-weight:500;background:var(--tint)}
.legend{display:flex;flex-wrap:wrap;gap:1.5mm 3mm;font-size:7.5pt;color:var(--soft);margin:2mm 0 0}
.sw{display:inline-block;width:4mm;height:3mm;vertical-align:-.4mm;border:1px solid var(--rule);margin-right:.8mm}
.note{font-size:7.5pt;color:var(--soft);margin:1mm 0 0;line-height:1.5}
.stats{display:flex;gap:2mm;margin-bottom:3mm;flex-wrap:wrap}
.stat{border:1px solid var(--rule);border-radius:3px;padding:1mm 2mm;background:var(--tint)}
.stat .l{font-size:7.5pt;color:var(--soft)} .stat .v{font-size:12pt;font-weight:700}
@page{size:A4 landscape;margin:8mm 9mm}
@media print{body{background:#fff} .sheet{width:auto;margin:0;padding:0;box-shadow:none} .toolbar{display:none}
.heat td,.heat td.crowd,.sw,.side h2{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _pct(value: float | None) -> str:
    return "－" if value is None else f"{round(value * 100)}%"


def _num(value: float | None) -> str:
    return "－" if value is None else f"{value:.1f}"


def util_band(value: float | None, crowded_at: float) -> str:
    """稼働率 → 背景の段 (u0〜u4)。u4 は混みの基準以上。"""
    if value is None:
        return "none"
    if value >= crowded_at:
        return "u4"
    if value >= crowded_at - 0.2:
        return "u3"
    if value >= crowded_at - 0.4:
        return "u2"
    if value > 0:
        return "u1"
    return "u0"


_MARK_CLASS = {"○": "ok", "△": "mid", "×": "ng", "－": "ng"}


def _month_label(value: str) -> str:
    year, month = value.split("-")
    return f"{int(year)} 年 {int(month)} 月"


def render_month_html(view: dict[str, Any], *, now: datetime | None = None) -> str:
    """``build_month_view`` の応答 → A4 横 1 枚の HTML。"""
    rules = view["rules"]
    crowded_at = float(rules["crowded_utilization"])
    office = (
        "全て"
        if view["office_id"] is None
        else next((o["name"] for o in view["offices"] if o["id"] == view["office_id"]), "")
    )
    names = {s["id"]: s["name"] for s in view["staff"]}
    chosen = [names[i] for i in view["selected_staff_ids"] if i in names]
    generated = (now or datetime.now(UTC)).astimezone(_JST)

    head_cells = "".join(f"<th>{_WD[wd]}</th>" for wd in view["weekdays"])
    rows = []
    for slot in view["slots"]:
        tds = []
        for wd in view["weekdays"]:
            c = view["cells"].get(f"{wd}-{slot}")
            if c is None or c["counted"] == 0:
                tds.append('<td class="none">－</td>')
                continue
            band = util_band(c["utilization"], crowded_at)
            cls = f"{band}{' crowd' if c['crowded'] else ''}"
            trend = ""
            if c.get("trend"):
                up = c["trend"] == "▲"
                trend = f'<span class="tr {"up" if up else "down"}">{c["trend"]}</span>'
            tds.append(
                f'<td class="{cls}"><span class="m {_MARK_CLASS[c["mark"]]}">{c["mark"]}</span>'
                f'<span class="p">{_pct(c["utilization"])}</span>{trend}</td>'
            )
        rows.append(f'<tr><th class="t">{_esc(slot)}</th>{"".join(tds)}</tr>')

    everyone = {r["weekday"]: r for r in view.get("weekday_summary_all") or []}
    wd_rows = "".join(
        f"<tr><td>{_WD[r['weekday']]}</td>"
        f"<td>{_num(everyone.get(r['weekday'], {}).get('visits_per_day'))}</td>"
        f"<td>{_pct(everyone.get(r['weekday'], {}).get('utilization'))}</td>"
        f"<td>{_num(r['visits_per_day'])}</td>"
        f"<td>{_pct(r['utilization'])}</td><td>{r['days']}/{r['total']}</td></tr>"
        for r in view["weekday_summary"]
    )

    def _peak(items: list[dict[str, Any]], fmt) -> str:
        return (
            "、".join(f"{_WD[p['weekday']]} {_esc(p['slot'])}（{fmt(p['value'])}）" for p in items)
            or "－"
        )

    counts = view["counts"]
    prev_util = view.get("prev_utilization")
    diff = (
        "" if prev_util is None or view["utilization"] is None else f"（前の月 {_pct(prev_util)}）"
    )
    lunch_text = (
        f"昼休み {rules['lunch_min']} 分を {rules['lunch_from']}〜{rules['lunch_to']} の間に取れること"
        if view["lunch"]
        else "昼休みは考えない"
    )
    title = f"受け入れ枠 {_month_label(view['month'])}（{office}）"
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        '<div class="toolbar"><span class="hint">A4 横で印刷・PDF 保存できます</span>'
        '<button type="button" onclick="window.print()">印刷 / PDF に保存</button></div>\n'
        '<main class="sheet">\n'
        '<header class="head"><div><div class="eyebrow">らく助 受け入れ枠（月で見る）谷＝空き・山＝混み</div>'
        f"<h1>{_esc(_month_label(view['month']))}　{_esc(office)}</h1></div>"
        f'<div class="meta">作成 <b>{generated:%Y-%m-%d %H:%M}</b><br>'
        f"担い手 <b>{len(chosen)} 人</b>・休み <b>{'実際のとおり' if view['mode'] == 'real' else '無いとする'}</b><br>"
        f"{_esc(_SOURCE_TEXT.get(view['source'], ''))}</div></header>\n"
        '<div class="body"><section>'
        f'<table class="heat"><thead><tr><th class="t"></th>{head_cells}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<div class="legend">'
        '<span><span class="sw" style="background:var(--u1)"></span>稼働率 低い</span>'
        '<span><span class="sw" style="background:var(--u2)"></span>中</span>'
        '<span><span class="sw" style="background:var(--u3)"></span>高い</span>'
        f'<span><span class="sw" style="background:var(--u4)"></span>{_pct(crowded_at)} 以上（混み）</span>'
        '<span><span class="sw" style="background:repeating-linear-gradient(135deg,rgba(168,57,47,.5) 0 1px,#fff 1px 4px)"></span>'
        "斜線＝新しい訪問は入れない方がよい</span>"
        f"<span>▲▼＝前の月より稼働率が {rules['trend_points']} ポイント以上上がった・下がった</span>"
        "</div>"
        f'<p class="note">○＝その月のすべての週で、新しい訪問 {rules["visit_min"]} 分を入れられる職員がいた。'
        "△＝4 週のうち 3 週・5 週のうち 4 週以上で入れられた。×＝それより少ない。－＝数えられる日が足りない。"
        f"決まり：1 日 {rules['cap']} 件まで・前後 {rules['buffer_min'] + rules['travel_guess_min']} 分の間・"
        f"{_esc(lunch_text)}。祝日は平日として数え、訪問が {rules['open_day_min_visits']} 件に満たない日は数えていません。</p>"
        "</section>\n"
        '<aside class="side">'
        '<div class="stats">'
        f'<div class="stat"><div class="l">稼働率（月）</div><div class="v">{_pct(view["utilization"])}</div>'
        f'<div class="l">{_esc(diff)}</div></div>'
        f'<div class="stat"><div class="l">○ / △ / ×</div><div class="v">{counts["ok"]} / {counts["mid"]} / {counts["ng"]}</div></div>'
        f'<div class="stat"><div class="l">混みのマス</div><div class="v">{counts["crowded"]}</div></div>'
        "</div>"
        "<h2>曜日ごとの合計</h2>"
        '<table><thead><tr><th rowspan="2">曜日</th><th colspan="2">事業所全体</th>'
        '<th colspan="2">担い手</th><th rowspan="2">数えた日</th></tr>'
        "<tr><th>件数/日</th><th>稼働率</th><th>件数/日</th><th>稼働率</th></tr></thead>"
        f"<tbody>{wd_rows}</tbody></table>"
        '<p class="note">件数は主担当の件数（事業所全体＝マネージャー・新人を含む全員の主担当の件数、'
        "担い手＝担い手の主担当の件数）。数えた日は担い手の日。空き（○△×）は担い手だけで数えています。"
        "勤務の型は今のものを使っているため、過去の月は空きが多めに出ることがあります。"
        "入職より前の日は数えていません。</p>"
        "<h2>ピークの時間帯</h2>"
        f'<p class="note">稼働率：{_peak(view["peaks"]["utilization"], _pct)}</p>'
        f'<p class="note">始まる訪問（1 日あたり）：{_peak(view["peaks"]["starts"], _num)}</p>'
        "<h2>担い手</h2>"
        f'<p class="note">{_esc("・".join(chosen) or "（選ばれていません）")}</p>'
        "</aside></div>\n</main>\n</body>\n</html>\n"
    )


__all__ = ["render_month_html", "util_band"]
