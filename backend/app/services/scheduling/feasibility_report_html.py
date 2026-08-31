"""実現性チェックの A4 縦レポート (自己完結 HTML) を組み立てる.

外部リソース (フォント / CSS / JS) を読み込まない 1 ファイル完結。ブラウザの印刷で
A4 縦 / PDF に落とせる。``feasibility_check.FeasibilityReport`` を入力にする純粋関数。
"""

from __future__ import annotations

import html
from collections import Counter
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.services.scheduling.feasibility_check import (
    HARD_KINDS,
    KIND_IMPOSSIBLE,
    KIND_NO_COORD,
    KIND_NO_LUNCH,
    KIND_OVERLAP,
    KIND_PAIR_NOT_SAME_START,
    KIND_PAIR_OVER,
    KIND_PAIR_SHORT,
    KIND_TIGHT,
    KIND_WATCH,
    ROAD_FACTOR,
    UNASSIGNED_STAFF_LABEL,
    FeasibilityReport,
    fmt_hm,
)

_WD = "月火水木金土日"
_JST = ZoneInfo("Asia/Tokyo")

_LEVEL = {
    "ok": ("", ""),
    "overlap": ("bad", "重なり"),
    "impossible": ("bad", "移動不可"),
    "pair_over": ("bad", "同住所3名以上"),
    "tight": ("warn", "バッファ不足"),
    "watch": ("note", "要注意"),
    "pair_seq": ("note", "同住所ペア(連続)"),
}

_CSS = """
:root{--paper:#fff;--ink:#1e2a33;--soft:#55656f;--rule:#d7dde2;--rule2:#9aa7b0;--tint:#f3f6f8;--brand:#e15a7f;--ok:#2f7d5b;--ok-t:#e8f3ee;--warn:#a8691a;--warn-t:#fbf1e2;--bad:#a8392f;--bad-t:#fbebe9;--note:#2e5570;--note-t:#eaf1f7}
html{color-scheme:light}
body{margin:0;background:#e9ecef;color:var(--ink);font-family:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;font-size:9.5pt;line-height:1.5;font-feature-settings:"palt"}
.sheet{background:var(--paper);width:210mm;margin:12mm auto;padding:14mm;box-sizing:border-box;box-shadow:0 2px 14px rgba(20,30,40,.12)}
.toolbar{width:210mm;margin:10mm auto 0;display:flex;justify-content:flex-end;gap:8px}
.toolbar button{font:inherit;font-size:10pt;padding:6px 14px;border:1px solid var(--rule2);background:#fff;color:var(--ink);border-radius:3px;cursor:pointer}
.toolbar button:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
.toolbar .hint{font-size:9pt;color:var(--soft);align-self:center;margin-right:auto}
h1{font-size:15pt;margin:0;line-height:1.35;text-wrap:balance}
.head{display:grid;grid-template-columns:1fr auto;gap:5mm;align-items:end;padding-bottom:3mm;border-bottom:2px solid var(--ink)}
.eyebrow{font-size:8.5pt;letter-spacing:.12em;color:var(--soft);margin-bottom:1mm}
.meta{font-size:8.5pt;color:var(--soft);text-align:right;white-space:nowrap;line-height:1.6} .meta b{color:var(--ink);font-weight:500}
.sum{display:flex;gap:3mm;margin:4mm 0 3mm;flex-wrap:wrap}
.stat{border:1px solid var(--rule);border-radius:3px;padding:2mm 3.5mm;background:var(--tint);min-width:26mm}
.stat .l{font-size:8pt;color:var(--soft)} .stat .v{font-size:14pt;font-weight:700;font-variant-numeric:tabular-nums}
.stat.bad .v{color:var(--bad)} .stat.warn .v{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;margin-bottom:2mm}
th,td{padding:1.2mm 1.6mm;border-bottom:1px solid var(--rule);vertical-align:top;text-align:left}
thead th{font-size:8pt;color:var(--soft);font-weight:500;background:var(--tint);border-bottom:1px solid var(--rule2)}
.grid th{font-weight:700;white-space:nowrap} .grid td.c{text-align:center;font-weight:700;width:22mm}
.grid td.ok{background:var(--ok-t)} .grid td.warn{background:var(--warn-t)} .grid td.bad{background:var(--bad-t)} .grid small{font-weight:400;font-size:8pt}
.blk{margin-top:4mm}
.blk h2{font-size:11pt;margin:0 0 1.5mm;padding:1.5mm 2.5mm;background:var(--ink);color:#fff;display:flex;gap:3mm;align-items:baseline;break-after:avoid}
.blk h2 .d{font-weight:400;opacity:.85} .blk h2 .badge{margin-left:auto}
td.t{white-space:nowrap;color:var(--soft);width:22mm} td.n{font-weight:700;white-space:nowrap} td.a{color:var(--soft);font-size:8.5pt} td.note{font-size:8.5pt}
.role{font-size:8pt;font-weight:400;color:var(--soft);border:1px solid var(--rule2);border-radius:3px;padding:0 3px}
tr.bad td{background:var(--bad-t)} tr.warn td{background:var(--warn-t)} tr.note td{background:var(--note-t)}
.pill{display:inline-block;font-size:8pt;line-height:1;padding:3px 6px;border-radius:9px;white-space:nowrap}
.pill.ok{background:var(--ok-t);color:var(--ok)} .pill.warn{background:var(--warn-t);color:var(--warn)} .pill.bad{background:var(--bad-t);color:var(--bad)} .pill.note{background:var(--note-t);color:var(--note)}
.blk h2 .pill{background:#fff}
tbody tr{break-inside:avoid}
.legend{font-size:8.5pt;color:var(--soft);margin:0 0 2mm}
.foot{margin-top:5mm;padding-top:2.5mm;border-top:1px solid var(--rule);font-size:8.5pt;color:var(--soft);display:flex;justify-content:space-between}
@page{size:A4 portrait;margin:11mm 12mm}
@media print{body{background:#fff;font-size:9pt} .sheet{width:auto;margin:0;padding:0;box-shadow:none} .toolbar{display:none} thead{display:table-header-group}}
@media (max-width:800px){.sheet,.toolbar{width:auto;margin:0;padding:4mm} .head{grid-template-columns:1fr} .meta{text-align:left;white-space:normal} .blk h2{flex-wrap:wrap}}
"""


def _wlabel(d: date) -> str:
    return f"{d.month}/{d.day}（{_WD[d.weekday()]}）"


def _esc(s: str) -> str:
    return html.escape(s or "")


def render_feasibility_html(report: FeasibilityReport) -> str:
    """レポート → 自己完結 HTML 文字列."""
    c = Counter(f.kind for f in report.findings)
    days = [
        report.week_start + timedelta(days=i)
        for i in range((report.week_end - report.week_start).days + 1)
    ]
    # 行のキーは staff_key (staff.id) — 同名スタッフを 1 行に潰さない (レビュー LOW-6)。
    staff_label = {t.staff_key or t.staff: t.staff for t in report.timelines}
    staffs = sorted(
        staff_label,
        key=lambda k: (staff_label[k] == UNASSIGNED_STAFF_LABEL, staff_label[k], k),
    )
    by_key = {(t.staff_key or t.staff, t.day): t for t in report.timelines}
    generated_jst = report.generated_at.astimezone(_JST)
    cfg = report.config

    # --- スタッフ × 曜日 の一覧 ---
    grid_rows = []
    for s in staffs:
        cells = []
        for d in days:
            fs = [
                f
                for f in report.findings
                if (f.staff_key or f.staff) == s and f.day == d and f.kind != KIND_NO_LUNCH
            ]
            tl = by_key.get((s, d))
            n = len(tl.items) if tl else 0
            bad = sum(1 for f in fs if f.kind in HARD_KINDS)
            warn = len(fs) - bad
            cls = "bad" if bad else ("warn" if warn else ("ok" if n else ""))
            extra = (f"<br><small>❗{bad}</small>" if bad else "") + (
                f"<br><small>△{warn}</small>" if warn else ""
            )
            cells.append(f'<td class="c {cls}">{n or ""}{extra}</td>')
        grid_rows.append(f"<tr><th>{_esc(staff_label[s])}</th>{''.join(cells)}</tr>")

    # --- スタッフ・日ごとの時間軸 ---
    sections = []
    for tl in report.timelines:
        rows = []
        for it in tl.items:
            cls, label = _LEVEL.get(it.level, ("", ""))
            when = f"{fmt_hm(it.start_min)}–{fmt_hm(it.end_min)}"
            what = _esc(it.name) + (
                f' <span class="role">{_esc(it.role)}</span>' if it.role != "主" else ""
            )
            pill = f'<span class="pill {cls}">{label}</span>' if label else ""
            rows.append(
                f'<tr class="{cls}"><td class="t">{when}</td><td class="n">{what}</td>'
                f'<td class="a">{_esc(it.address[:28])}</td><td class="note">{_esc(it.note)}</td><td>{pill}</td></tr>'
            )
        # バッジは findings から数える (item.level を持たない指摘も漏らさない・レビュー HIGH-5)。
        tkey = tl.staff_key or tl.staff
        day_fs = [
            f for f in report.findings if (f.staff_key or f.staff) == tkey and f.day == tl.day
        ]
        n_bad = sum(1 for f in day_fs if f.severity == "hard")
        n_warn = sum(1 for f in day_fs if f.severity == "soft")
        n_info = sum(1 for f in day_fs if f.kind == KIND_NO_COORD)
        badge = (f'<span class="pill bad">重大 {n_bad}</span> ' if n_bad else "") + (
            f'<span class="pill warn">注意 {n_warn}</span> ' if n_warn else ""
        )
        if n_info:
            badge += f'<span class="pill note">座標なし {n_info}</span> '
        if tl.lunch_free_min < cfg.lunch_duration_min and any(x.kind == "visit" for x in tl.items):
            badge += f'<span class="pill note">昼休み最長{tl.lunch_free_min}分</span>'
        if not badge:
            badge = '<span class="pill ok">問題なし</span>'
        sections.append(
            f'<section class="blk"><h2><span>{_esc(tl.staff)}</span><span class="d">{_wlabel(tl.day)}</span>'
            f'<span class="badge">{badge}</span></h2>'
            "<table><thead><tr><th>時間</th><th>訪問先／予定</th><th>住所</th><th>判定メモ</th><th></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )

    same_addr = c[KIND_PAIR_SHORT] + c[KIND_PAIR_NOT_SAME_START] + c[KIND_PAIR_OVER]
    stats = (
        f'<div class="stat bad"><div class="l">重なり</div><div class="v">{c[KIND_OVERLAP]}</div></div>'
        f'<div class="stat bad"><div class="l">移動不可</div><div class="v">{c[KIND_IMPOSSIBLE]}</div></div>'
        f'<div class="stat warn"><div class="l">バッファ不足</div><div class="v">{c[KIND_TIGHT]}</div></div>'
        f'<div class="stat warn"><div class="l">要注意（実走行）</div><div class="v">{c[KIND_WATCH]}</div></div>'
        f'<div class="stat warn"><div class="l">同住所ルール</div><div class="v">{same_addr}</div></div>'
        f'<div class="stat"><div class="l">昼休み{cfg.lunch_duration_min}分なし</div><div class="v">{c[KIND_NO_LUNCH]}</div></div>'
    )
    title = f"実現性チェック {report.iso_year}-W{report.iso_week:02d}"
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        '<div class="toolbar"><span class="hint">A4 縦で印刷・PDF 保存できます</span>'
        '<button type="button" onclick="window.print()">印刷 / PDF に保存</button></div>\n'
        '<main class="sheet">\n'
        '<header class="head"><div><div class="eyebrow">らく助 スケジュール 実現性チェック（移動・重なり・バッファ・同住所ルール）</div>'
        f"<h1>{_wlabel(report.week_start)}〜{_wlabel(report.week_end)}　スタッフ別 時間軸判定</h1></div>"
        f'<div class="meta">判定 <b>{generated_jst:%Y-%m-%d %H:%M} JST</b><br>'
        f"前提 <b>直線距離÷{cfg.travel_speed_kmh:g}km/h・バッファ{cfg.visit_buffer_min}分</b>（らく助の設定値）<br>"
        f"データ <b>訪問 {report.visit_count} 件・イベント {report.event_count} 件（読み取り）</b></div></header>\n"
        f'<div class="sum">{stats}</div>\n'
        '<p class="legend">❗＝重なり／移動不可／同住所3名以上（成立しない）　△＝バッファ不足／要注意／同住所ルール（成立はするが余裕・ルール逸脱）。'
        "数字はその日の予定件数（同行含む）。<b>同住所ルール</b>＝同じ住所の2名は同時刻スタートで max(サービス合計, 90分) を占有。次の訪問はその占有終了後。"
        f"要注意＝直線×{ROAD_FACTOR:g} の実走行想定。朝会（所属拠点）からの出発も判定。</p>\n"
        f'<table class="grid"><thead><tr><th></th>{"".join(f"<th>{_wlabel(d)}</th>" for d in days)}</tr></thead>'
        f"<tbody>{''.join(grid_rows)}</tbody></table>\n"
        f"{''.join(sections)}\n"
        f'<div class="foot"><span>らく助 実現性チェック</span><span>{generated_jst:%Y-%m-%d %H:%M} 判定</span></div>\n'
        "</main>\n</body>\n</html>\n"
    )


__all__ = ["render_feasibility_html"]
