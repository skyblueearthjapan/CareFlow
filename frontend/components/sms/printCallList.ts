/**
 * 「お電話でお伝えする方」の一覧を印刷用に開く (A4 縦)。
 * 載せるのは、お名前・SMS でお知らせできない理由・訪問の予定 (日と時刻) だけ。
 * 電話番号と本文は載せない。ほかの A4 の出力と同じく、新しい窓に HTML を開く。
 */

export interface CallListRow {
  name: string;
  reason: string;
  visits?: string;
}

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function buildCallListHtml(title: string, rows: CallListRow[]): string {
  const body = rows
    .map(
      (r, i) =>
        `<tr><td class="n">${i + 1}</td><td>${esc(r.name)} 様</td><td>${esc(r.reason)}</td><td>${esc(
          r.visits ?? '',
        )}</td><td class="chk"></td></tr>`,
    )
    .join('');
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>${esc(title)}</title>
<style>
@page{size:A4 portrait;margin:14mm}
body{font-family:"Noto Sans JP",sans-serif;font-size:13px;color:#111}
h1{font-size:18px;margin:0 0 8px}
p{margin:0 0 10px}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #666;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eee}
td.n{width:2.5em;text-align:right}
td.chk{width:5em}
</style></head><body>
<h1>${esc(title)}</h1>
<p>SMS でお知らせできない方です。お電話でお伝えしたら、右の欄に印を付けてください。</p>
<table><thead><tr><th>#</th><th>お名前</th><th>理由</th><th>訪問の予定</th><th>済</th></tr></thead>
<tbody>${body}</tbody></table>
<script>window.onload=function(){window.print()}</script>
</body></html>`;
}

/** 新しい窓で開いて印刷する。開けなかったら false。 */
export function openCallListPrint(title: string, rows: CallListRow[]): boolean {
  if (typeof window === 'undefined') return false;
  const win = window.open('', '_blank');
  if (!win) return false;
  const url = URL.createObjectURL(
    new Blob([buildCallListHtml(title, rows)], { type: 'text/html;charset=utf-8' }),
  );
  win.location.href = url;
  try {
    win.opener = null;
  } catch {
    /* 一部ブラウザで読み取り専用 */
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return true;
}
