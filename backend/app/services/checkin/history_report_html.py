"""打刻履歴の A4 印刷 HTML 化 — 純関数レンダラ (Phase 1).

正典設計書: ``docs/plans/visit-history-design-2026-09-30.md`` §3-3。

**体裁は 2026-09-30 にお客様へ渡した月次レポートと同じ** (PO 確認)。手本 =
``docs/tools/visit-history/build_report.py`` の HTML: 画面上で A4 の用紙が 1 枚ずつ
並び、各用紙に見出し・出力日時・フッターの注記・「1 / 5」のページ番号、上部のバーに
枚数と「印刷 / PDF に保存」ボタン。

二段構え (JavaScript が動かなくても読めて印刷できる):

1. **サーバが返すのは表を含む完全な HTML**。これだけで読めるし、CSS の印刷フロー
   (``thead{display:table-header-group}`` ＋ ``tr{break-inside:avoid}``) で改ページ
   できる。``page_break=True`` の節には ``data-pb`` が付き ``break-before:page``。
2. **埋め込みスクリプトがそれを用紙へ組み直す**。行を 1 つずつ流し込み、用紙から
   あふれたら次の用紙へ送る (固定行数で切らない)。組み直しは元の HTML の **複製**
   から行い、全部できてから元を外す — 途中で失敗したら 1. の姿に戻る (行が消えない)。

手本の道具はデータを JSON でスクリプトへ渡していたが、ここでは **データを DOM に
置き、スクリプトは定数** にしてある (氏名をスクリプトへ埋め込まない)。

1 枚目: 注意書き・件数・「読み方」・看護師別の件数表。続いて明細。
DB にも現在時刻にも触らない (出力時刻は ``generated_at`` で受け取る)。
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import UTC, date, datetime

from app.services.checkin.history import (
    JST,
    REPORT_TITLE,
    HistoryRow,
    caution_note,
    period_label,
    reading_notes,
    report_remarks,
    staff_counts,
    summarize,
)

_WEEKDAYS = "月火水木金土日"

_GROUP_LABELS: dict[str, str] = {"staff": "看護師別", "date": "日付順", "patient": "患者別"}

# 「サインで記録」(signature-checkin-design §5-1 Q7) を含めて「QR・サインの時刻です」。
_FOOTER_NOTE = (
    "到着・退出は QR・サインの時刻です"
    "（QR は家に入ってから読むため、実際の到着より数分遅いことがあります）。"
)
# 明細に調整のある行が含まれるときの注記 (その行の到着・退出は読み取った時刻ではない)。
_FOOTER_NOTE_ADJUSTED = (
    "到着・退出は QR・サインの時刻です。備考に「調整」とある行は、"
    "スタッフが実際に着いた（出た）時刻に合わせた時刻で、読み取った時刻は備考にあります。"
)

# 見た目は手本 (build_report.py) の CSS を移植。違いは 2 つ:
#   * 用紙の固定高さ・あふれ隠しは ``html.paged`` (スクリプトが付ける) のときだけ。
#     付かなければ 1 枚の長い紙として読め、印刷は CSS のフローで改ページする。
#   * 用紙の高さは画面も印刷も 296mm (手本は画面 297mm / 印刷 296mm)。画面で測った
#     とおりに印刷へ収まるようにするため。印刷時はあふれ隠しも外す (万一ずれても
#     行が消えるより、はみ出す方がまし)。
# ``@page{margin:0}`` はクラスで切り替えられないので、スクリプトが <style> を足す。
_CSS = """
html{color-scheme:light}
body{margin:0;background:#e9ecef;color:#1e2a33;
  font-family:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;
  font-size:9pt;line-height:1.5}
.bar{position:sticky;top:0;background:#fff;border-bottom:1px solid #d7dde2;padding:8px 16px;
  display:flex;gap:12px;align-items:center;font-size:13px;z-index:2}
.bar .sp{flex:1}
.bar button{height:32px;padding:0 14px;border-radius:6px;border:1px solid #1e2a33;
  background:#1e2a33;color:#fff;font-size:13px;cursor:pointer}
.sheet{background:#fff;width:210mm;margin:8mm auto;padding:12mm 13mm 10mm;box-sizing:border-box;
  box-shadow:0 2px 14px rgba(20,30,40,.12)}
.sheet header{border-bottom:2px solid #1e2a33;padding-bottom:2mm;margin-bottom:2mm;
  display:flex;justify-content:space-between;align-items:flex-end;gap:6mm}
.sheet header h1{font-size:14pt;margin:0;letter-spacing:.02em}
.sheet header .org{font-size:8.6pt;color:#55656f;text-align:right;line-height:1.4;
  white-space:nowrap}
h2{font-size:10.6pt;margin:3.5mm 0 1.4mm;padding-left:2.5mm;border-left:3px solid #1e2a33}
h2 small{font-size:8.4pt;font-weight:400;color:#55656f;margin-left:3mm}
p{margin:1mm 0 2mm}
.lead{background:#fff7e6;border-left:4px solid #9a6b00;padding:2.5mm 4mm;margin:1mm 0 3mm;
  font-size:9.4pt}
.kpi{display:flex;gap:3mm;margin:0 0 3mm;flex-wrap:wrap}
.kpi span{background:#f3f6f8;border:1px solid #d7dde2;padding:1.5mm 3.5mm;border-radius:2mm;
  font-size:8.8pt}
.kpi b{font-size:12pt;margin-left:1mm}
.empty{background:#f3f6f8;border:1px solid #d7dde2;padding:2.5mm 3.5mm;margin:2mm 0 3mm;
  border-radius:1.5mm}
ul{margin:1mm 0 2mm;padding-left:5mm} li{margin-bottom:.8mm}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:8.8pt;
  table-layout:fixed}
th{text-align:left;font-size:8pt;color:#55656f;background:#f3f6f8;
  border-bottom:1px solid #9aa7b0;padding:1.2mm 1.6mm;white-space:nowrap}
td{border-bottom:1px solid #d7dde2;padding:1.2mm 1.6mm;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
td.b{font-weight:700;font-size:9.6pt} td.n{text-align:right}
td.rm{white-space:normal;color:#55656f;font-size:8pt}
.sheet footer{border-top:1px solid #d7dde2;padding-top:1.5mm;margin-top:2mm;font-size:7.8pt;
  color:#55656f;display:flex;justify-content:space-between;gap:6mm}
html.paged .sheet{height:296mm;display:flex;flex-direction:column}
html.paged .body{flex:1;min-height:0;overflow:hidden}
@page{size:A4 portrait;margin:12mm 13mm}
@media print{
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .bar{display:none}
  .sheet{width:auto;margin:0;padding:0;box-shadow:none}
  h2{break-after:avoid}
  tr,.lead,.kpi,.empty{break-inside:avoid}
  thead{display:table-header-group}
  section[data-pb]{break-before:page}
  html.paged .sheet{width:210mm;padding:12mm 13mm 10mm;break-after:page}
  html.paged .sheet:last-child{break-after:auto}
  html.paged .body{overflow:visible}
}
"""

# サーバが返した 1 枚の長い紙 (#flow) を A4 の用紙 (#sheets) へ組み直す。
# データは持たない定数 (氏名などは DOM から読む)。手本 build_report.py の流し込みと
# 同じ方式: 1 行ずつ足して、用紙の本文があふれたら次の用紙へ送る。
_SCRIPT = """
(function () {
  var flow = document.getElementById('flow');
  var root = document.getElementById('sheets');
  if (!flow || !root) return;
  var doc = document.documentElement;
  var pageStyle = document.createElement('style');
  pageStyle.textContent = '@page{size:A4 portrait;margin:0}';
  try {
    var head = flow.querySelector('header').outerHTML;
    var foot = flow.querySelector('footer').outerHTML;
    var blocks = flow.querySelector('.body').children;
    var body = null;
    var tbody = null;
    doc.classList.add('paged');
    document.head.appendChild(pageStyle);
    flow.style.display = 'none';

    var newSheet = function () {
      var s = document.createElement('div');
      s.className = 'sheet';
      s.innerHTML = head + '<div class="body"></div>' + foot;
      root.appendChild(s);
      body = s.querySelector('.body');
    };
    var over = function () { return body.scrollHeight > body.clientHeight + 1; };
    var addBlock = function (src) {
      var el = src.cloneNode(true);
      body.appendChild(el);
      if (over() && body.children.length > 1) { newSheet(); body.appendChild(el); }
    };
    var startTable = function (src, cont) {
      var sec = document.createElement('section');
      var h = src.querySelector('h2').cloneNode(true);
      if (cont) h.insertBefore(document.createTextNode('（続き）'), h.querySelector('small'));
      var table = src.querySelector('table').cloneNode(false);
      table.appendChild(src.querySelector('thead').cloneNode(true));
      tbody = document.createElement('tbody');
      table.appendChild(tbody);
      sec.appendChild(h);
      sec.appendChild(table);
      body.appendChild(sec);
      return sec;
    };
    var addTable = function (src) {
      if (src.hasAttribute('data-pb') && body.children.length) newSheet();
      var rows = src.querySelectorAll('tbody > tr');
      var sec = startTable(src, false);
      var placed = 0;
      for (var i = 0; i < rows.length; i++) {
        var tr = rows[i].cloneNode(true);
        tbody.appendChild(tr);
        // 用紙の先頭に置いた 1 行目があふれるなら、送っても入らない。そのまま置く。
        if (over() && (body.children.length > 1 || tbody.children.length > 1)) {
          tbody.removeChild(tr);
          if (!tbody.children.length) body.removeChild(sec);
          newSheet();
          sec = startTable(src, placed > 0);
          tbody.appendChild(tr);
        }
        placed++;
      }
    };

    newSheet();
    for (var b = 0; b < blocks.length; b++) {
      if (blocks[b].tagName === 'SECTION') addTable(blocks[b]); else addBlock(blocks[b]);
    }
    var sheets = root.querySelectorAll('.sheet');
    for (var n = 0; n < sheets.length; n++) {
      sheets[n].querySelector('.pg').textContent = (n + 1) + ' / ' + sheets.length;
    }
    document.getElementById('info').textContent = 'A4 縦 ' + sheets.length + ' 枚';
    flow.parentNode.removeChild(flow);
  } catch (e) {
    // 組み直しに失敗したら、サーバが返したままの姿 (1 枚の長い紙) に戻す。
    root.innerHTML = '';
    doc.classList.remove('paged');
    if (pageStyle.parentNode) pageStyle.parentNode.removeChild(pageStyle);
    flow.style.display = '';
  }
})();
"""

# 明細の列 (見出し, 幅)。幅が空の列が残りを取る。
_COL_DATE = ("日付", "19mm")
_COL_PATIENT = ("利用者", "34mm")
_COL_PLAN = ("予定", "25mm")
_COL_NURSE = ("看護師", "26mm")
_COL_ARR = ("到着", "15mm")
_COL_DEP = ("退出", "15mm")
_COL_STAY = ("滞在", "14mm")
_COL_REMARKS = ("備考", "")


def _e(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _hm(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value is not None else "—"


def _date_cell(d: date) -> str:
    return f"{d.month}/{d.day} ({_WEEKDAYS[d.weekday()]})"


def _plan_cell(r: HistoryRow) -> str:
    return f"{r.start_time}–{r.end_time}" if r.start_time and r.end_time else "—"


def _thead(cols: Sequence[tuple[str, str]]) -> str:
    cells = "".join(
        f'<th style="width:{width}">{_e(label)}</th>' if width else f"<th>{_e(label)}</th>"
        for label, width in cols
    )
    return f"<thead><tr>{cells}</tr></thead>"


def _section(title: str, sub: str, table: str, *, page_break: bool = False) -> str:
    """表の節 (見出し ＋ 表)。スクリプトはこの単位で行を用紙へ流し込む。"""
    attr = ' data-pb="1"' if page_break else ""
    return f"<section{attr}><h2>{_e(title)}<small>{_e(sub)}</small></h2>{table}</section>"


def _detail_table(rows: Sequence[HistoryRow], *, show_patient: bool, show_nurse: bool) -> str:
    cols = [_COL_DATE]
    if show_patient:
        cols.append(_COL_PATIENT)
    cols.append(_COL_PLAN)
    if show_nurse:
        cols.append(_COL_NURSE)
    cols += [_COL_ARR, _COL_DEP, _COL_STAY, _COL_REMARKS]
    body: list[str] = []
    for r in rows:
        cells = [f"<td>{_e(_date_cell(r.visit_date))}</td>"]
        if show_patient:
            cells.append(f"<td>{_e(r.patient_name or '—')}</td>")
        cells.append(f"<td>{_e(_plan_cell(r))}</td>")
        if show_nurse:
            cells.append(f"<td>{_e(r.nurse_name or '—')}</td>")
        stay = f"{r.stay_minutes}分" if r.stay_minutes is not None else "—"
        cells += [
            f'<td class="b">{_e(_hm(r.arrival_jst))}</td>',
            f'<td class="b">{_e(_hm(r.departure_jst))}</td>',
            f"<td>{_e(stay)}</td>",
            f'<td class="rm">{_e(report_remarks(r))}</td>',
        ]
        body.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{_thead(cols)}<tbody>{''.join(body)}</tbody></table>"


def _grouped(rows: Sequence[HistoryRow], group: str) -> list[tuple[str, list[HistoryRow]]]:
    """看護師・患者ごとにまとめる (名前順・名前なしは末尾。グループ内は渡された順)。"""
    buckets: dict[object, tuple[str | None, list[HistoryRow]]] = {}
    for r in rows:
        if group == "staff":
            key: object = r.actual_staff_id or r.planned_staff_id
            name = r.nurse_name
        else:
            key, name = r.patient_id, r.patient_name
        buckets.setdefault(key, (name, []))[1].append(r)
    ordered = sorted(buckets.values(), key=lambda b: (b[0] is None, b[0] or ""))
    fallback = "（担当なし）" if group == "staff" else "（氏名なし）"
    return [(name or fallback, members) for name, members in ordered]


def _details(rows: Sequence[HistoryRow], *, group: str, page_break: bool) -> str:
    if not rows:
        return '<div><h2>明細</h2><div class="empty">該当する訪問はありません。</div></div>'
    if group == "date":
        table = _detail_table(rows, show_patient=True, show_nurse=True)
        return _section("明細", f"{len(rows)} 件", table)
    parts: list[str] = []
    for name, members in _grouped(rows, group):
        hit = sum(1 for r in members if r.arrival_at is not None)
        sub = f"{len(members)} 件"
        if hit != len(members):
            sub += f"（到着の読み取り {hit} 件）"
        table = _detail_table(members, show_patient=group == "staff", show_nurse=group == "patient")
        parts.append(_section(name, sub, table, page_break=page_break))
    return "".join(parts)


def _cover(rows: Sequence[HistoryRow]) -> str:
    """1 枚目: 注意書き・件数・「読み方」・看護師別の件数表。"""
    summary = summarize(rows)
    kpi = [
        ("この期間の訪問", summary["visits"]),
        ("到着の読み取り", summary["with_arrival"]),
        ("退出の読み取り", summary["with_departure"]),
        ("退出なし", summary["no_departure"]),
    ]
    counts = "".join(
        f"<tr><td>{_e(c.name)}</td>"
        f'<td class="n">{c.planned}</td><td class="n">{c.arrival}</td>'
        f'<td class="n">{c.departure}</td><td class="n">{c.no_departure}</td></tr>'
        for c in staff_counts(rows)
    )
    count_cols = [
        ("看護師", "42mm"),
        ("予定の件数", "28mm"),
        ("到着の読み取り", "30mm"),
        ("退出の読み取り", "30mm"),
        ("退出なし", ""),
    ]
    return (
        f'<div><div class="lead">{_e(caution_note(rows))}</div>'
        '<div class="kpi">'
        + "".join(f"<span>{_e(name)}<b>{value}</b> 件</span>" for name, value in kpi)
        + "</div></div>\n"
        "<div><h2>この表の読み方</h2><ul>"
        + "".join(f"<li>{_e(note)}</li>" for note in reading_notes(rows))
        + "</ul></div>\n"
        + _section(
            "看護師別の件数",
            "予定は担当として組まれた件数、読み取りは実際に QR を読んだ件数",
            f"<table>{_thead(count_cols)}<tbody>{counts}</tbody></table>",
        )
        + "\n"
    )


def render_history_report_html(
    rows: Sequence[HistoryRow],
    *,
    date_from: date,
    date_to: date,
    group: str = "staff",
    include_none: bool = False,
    page_break: bool = False,
    generated_at: datetime,
    scope_note: str | None = None,
) -> str:
    """打刻履歴 → 自己完結 HTML (A4 縦)。

    Args:
        rows: 絞り込み済み・日付順・``future`` を除いた行 (読み取りのない訪問も含めて渡す。
            1 枚目の件数は全体から数え、明細に載せるかは ``include_none`` で決める)。
        group: ``staff`` (看護師別) / ``date`` (日付順) / ``patient`` (患者別)。
        include_none: False なら明細は到着のある訪問だけ。
        page_break: True なら看護師・患者ごとに改ページ (``date`` では効かない)。
        generated_at: 出力時刻。
        scope_note: 絞り込みの説明 (各用紙の見出しの右に出す。無ければ出さない)。
    """
    label = period_label(date_from, date_to)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    generated = generated_at.astimezone(JST).strftime("%Y/%m/%d %H:%M")
    detail_rows = rows if include_none else [r for r in rows if r.arrival_at is not None]

    conditions = [
        _GROUP_LABELS.get(group, group),
        "読み取りのない訪問も含む" if include_none else "到着の読み取りがある訪問",
    ]
    if scope_note:
        conditions.append(scope_note)
    title = f"{REPORT_TITLE}　{label}"
    footer_note = _FOOTER_NOTE_ADJUSTED if any(r.is_adjusted for r in detail_rows) else _FOOTER_NOTE
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
        f'<div class="org">{"<br>".join(_e(c) for c in conditions)}<br>出力 {_e(generated)}</div></header>\n'
        '<div class="body">\n'
        + _cover(rows)
        + _details(detail_rows, group=group, page_break=page_break)
        + "\n</div>\n"
        f'<footer><span>{_e(footer_note)}</span><span class="pg"></span></footer>\n'
        "</div>\n"
        '<div id="sheets"></div>\n'
        "<script>" + _SCRIPT + "</script>\n"
        "</body>\n</html>"
    )
