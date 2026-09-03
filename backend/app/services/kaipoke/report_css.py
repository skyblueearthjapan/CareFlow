"""印刷レポート共通 CSS (A4 縦・らく助 × カイポケ)。

正典 = ``docs/plans/sync-result-report-design.md`` §4「印刷（A4 縦）規則」。
見た目の手本 = ``docs/reports/2026-09-03-w37-kaipoke-sync-report.html``
(PO 確認済みの手作り報告書) — トークン/`.sheet`/表/タグ/ボックスをそのまま踏襲する。

`report_css` は文字列定数なので、f-string へ埋め込むときは **連結** で入れること
(f-string に入れると `{}` のエスケープが必要になり読めなくなる)。
"""

from __future__ import annotations

# NOTE: 改ページ規則の意図
#   * `.cover{break-after:page}`        … 1 ページ目は要約だけで閉じる
#   * `.pb{break-before:page}`          … 明細/補足の先頭で改ページ
#   * `section.day.compact`             … 行数 ≤ 14 の日だけ塊で割らない
#   * `thead{display:table-header-group}` … 割れた表の見出しを各ページに繰り返す
#   * `tr{break-inside:avoid}`          … 行の途中では切らない
#   * `section.appendix{break-inside:avoid}` … 補足は改ページせず入るなら続けて置く
#
# フッタは **固定 (position:fixed) にしない**。Chrome は fixed 要素を
# 「@page マージンの内側 = 本文領域」に対して配置し、各ページへ複製する。
# 本文もその同じ下端まで流れるので、下マージンをいくら広げても本文領域の下端と
# フッタが一緒に上がるだけで、最終行への重なりは消えない (実測: 8 ページ PDF で
# 最終行が潰れた)。CSS の @page マージンボックス (`@bottom-center`) は Chrome 未対応。
# → PO 確認済みの手本 (2026-09-03-w37…html) と同じく**通常フローの静的フッタ**を
#    表紙末尾と文書末尾の 2 箇所に出す。
REPORT_CSS = """
:root{--ink:#1e2a33;--soft:#55656f;--rule:#d7dde2;--rule2:#9aa7b0;--tint:#f3f6f8;
  --ok:#2f7d5b;--ok-t:#f4faf7;--ng:#a8392f;--ng-t:#fbebe9;--warn:#9a6b00;--warn-t:#fff7e6}
html{color-scheme:light}
body{margin:0;background:#e9ecef;color:var(--ink);
  font-family:"BIZ UDPGothic","Noto Sans JP","Hiragino Sans","Yu Gothic UI",Meiryo,sans-serif;
  font-size:9pt;line-height:1.5;font-feature-settings:"palt"}
.sheet{background:#fff;width:210mm;margin:8mm auto;padding:13mm 14mm;box-sizing:border-box;
  box-shadow:0 2px 14px rgba(20,30,40,.12)}
h1{font-size:15pt;margin:0;border-bottom:2px solid var(--ink);padding-bottom:2mm;letter-spacing:.02em}
h2{font-size:10.8pt;margin:5mm 0 1.5mm;padding-left:2.5mm;border-left:3px solid var(--ink)}
h3{font-size:9.4pt;margin:3mm 0 1mm;color:var(--soft)}
p{margin:1mm 0 2mm}
.meta{color:var(--soft);font-size:8.6pt;margin:2mm 0 4mm;display:flex;flex-wrap:wrap;gap:1mm 5mm}
.lead{background:var(--tint);border-left:4px solid var(--rule2);padding:3mm 4mm;margin:0 0 4mm;font-size:9.6pt}
.lead.green{background:var(--ok-t);border-left-color:var(--ok)}
.lead.amber{background:var(--warn-t);border-left-color:var(--warn)}
.lead.red{background:var(--ng-t);border-left-color:var(--ng)}
.lead b{font-weight:700}
.kpi{display:flex;gap:3mm;margin:0 0 5mm;flex-wrap:wrap}
.kpi span{background:var(--tint);border:1px solid var(--rule);padding:1.5mm 3.5mm;border-radius:2mm;font-size:8.8pt}
.kpi b{font-size:12pt;margin-left:1mm}
.kpi b.ok{color:var(--ok)} .kpi b.ng{color:var(--ng)} .kpi b.warn{color:var(--warn)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;margin:1mm 0 2mm;font-size:8.4pt}
th{text-align:left;font-size:8pt;color:var(--soft);border-bottom:1px solid var(--rule2);
  padding:1.2mm 1.8mm;background:var(--tint)}
td{border-bottom:1px solid var(--rule);padding:1.2mm 1.8mm;vertical-align:top}
td.n{text-align:right;white-space:nowrap}
td.k{white-space:nowrap;font-weight:700}
td.t{white-space:nowrap}
td.res{white-space:nowrap}
/* 変更内容: 「16:00 熊澤 → 17:15 髙梨」を矢印の前後でだけ折り返す
   (項目の途中で折り返すと全行が 2 行になり、明細が倍のページ数になる)。 */
td.chg{color:var(--soft);white-space:normal;word-break:keep-all}
td.chg .nw{white-space:nowrap}
tr.ok td{background:var(--ok-t)} tr.ng td{background:var(--ng-t)} tr.warn td{background:var(--warn-t)}
.tag{display:inline-block;padding:0 1.6mm;border-radius:1.5mm;font-size:8pt;font-weight:700;white-space:nowrap}
.tag.ok{background:var(--ok-t);color:var(--ok);border:1px solid #bfe0cf}
.tag.ng{background:var(--ng-t);color:var(--ng);border:1px solid #efc4bf}
.tag.warn{background:var(--warn-t);color:var(--warn);border:1px solid #f1d9a0}
.tag.muted{background:var(--tint);color:var(--soft);border:1px solid var(--rule)}
.box{background:var(--tint);border:1px solid var(--rule);padding:2.5mm 3.5mm;margin:2mm 0 3mm;border-radius:1.5mm}
.box.warn{background:var(--warn-t);border-color:#f1d9a0}
.box.ng{background:var(--ng-t);border-color:#efc4bf}
ol,ul{margin:1mm 0 2mm;padding-left:5.5mm} li{margin:.6mm 0}
.small{font-size:8.2pt;color:var(--soft)}
.cnt{color:var(--soft);font-weight:400;font-size:8.5pt;margin-left:2mm}
.toolbar{width:210mm;margin:8mm auto 0;display:flex;justify-content:flex-end}
.toolbar button{font:inherit;font-size:10pt;padding:5px 14px;border:1px solid var(--rule2);
  background:#fff;border-radius:3px;cursor:pointer}
.pfoot{margin-top:6mm;padding-top:2mm;border-top:1px solid var(--rule);font-size:8pt;
  color:var(--soft)}
.cover{break-after:page}
.pb{break-before:page}
section.appendix{break-inside:avoid}
@page{size:A4 portrait;margin:12mm 13mm}
@media print{
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .sheet{width:auto;margin:0;padding:0;box-shadow:none}
  .toolbar{display:none}
  h2,h3{break-after:avoid}
  tr,.box,.lead,.kpi,.pfoot{break-inside:avoid}
  thead{display:table-header-group}
  .cover{break-after:page}
  .pb{break-before:page}
  section.day.compact{break-inside:avoid}
  section.appendix{break-inside:avoid}
}
"""
