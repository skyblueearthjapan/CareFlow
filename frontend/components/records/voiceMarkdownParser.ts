/**
 * 音声記録 v2 の本文（AI が書いた Markdown）の小さな読み取り（設計 v2 §3 P4）。
 *
 * 扱うのは 見出し・箇条書き（入れ子は 1 段）・番号・表（パイプ）・太字・段落・
 * 時刻 `[m:ss]`（1 時間以上は `[h:mm:ss]`）だけ。**結果は文字の並びで返し、HTML は
 * 作らない** — 表示側は React の通常の表示で出すので、AI の文に `<script>` や
 * リンクが混ざっても文字のまま出る（`dangerouslySetInnerHTML` は使わない）。
 *
 * 同じ規則の Python 版が A4 レポート（`backend/app/services/voice/record_report_html.py`）
 * にある。片方を変えたら、もう片方もそろえる。
 * 手本: PC-Assistant `MinutesWriter.MarkdownBlocks`。
 */

/** 本文の中の 1 つの文字のまとまり。`time` は元の発言の時刻（経過秒）。 */
export type MdInline =
  | { kind: 'text'; text: string; strong: boolean }
  | { kind: 'time'; sec: number; strong: boolean };

export interface MdListItem {
  inlines: MdInline[];
  /** 1 段だけの入れ子の箇条書き。 */
  children: MdInline[][];
}

export type MdBlock =
  | { kind: 'heading'; level: number; inlines: MdInline[] }
  | { kind: 'para'; inlines: MdInline[] }
  | { kind: 'bullets'; items: MdListItem[] }
  | { kind: 'numbers'; items: { num: number; inlines: MdInline[] }[] }
  | { kind: 'table'; head: MdInline[][]; rows: MdInline[][][] };

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const BULLET_RE = /^(\s*)(?:[-*+]\s+|・\s*)(.*)$/;
const NUMBER_RE = /^(\s*)(\d{1,3})(?:[.)]\s+|．\s*)(.*)$/;
const RULE_RE = /^(-{3,}|\*{3,}|_{3,})$/;
const TABLE_RULE_RE = /^:?-+:?$/;
/** `[m:ss]` / `[h:mm:ss]`。 */
const TIME_RE = /\[(\d{1,3}):(\d{2})(?::(\d{2}))?\]/g;

/** `m:ss` / `h:mm:ss`（括弧なし）→ 経過秒。読めなければ null。 */
export function parseTimeLabel(label: string | null | undefined): number | null {
  const m = /^\s*\[?(\d{1,3}):(\d{2})(?::(\d{2}))?\]?\s*$/.exec(label ?? '');
  if (!m) return null;
  return toSeconds(m[1] ?? '', m[2] ?? '', m[3]);
}

function toSeconds(a: string, b: string, c: string | undefined): number | null {
  const x = Number(a);
  const y = Number(b);
  if (c === undefined) {
    return y < 60 ? x * 60 + y : null;
  }
  const z = Number(c);
  return y < 60 && z < 60 ? x * 3600 + y * 60 + z : null;
}

/** 経過秒 → 「12:05 ごろ」（1 時間以上は「1:02:05 ごろ」）。時刻は約 20〜60 秒ずれる（§8）。 */
export function aroundLabel(sec: number): string {
  const total = Math.max(0, Math.floor(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  const clock = h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
  return `${clock} ごろ`;
}

/** 押したときに再生を始める位置（時刻の 15 秒前・0 未満は 0）。 */
export const SEEK_LEAD_SEC = 15;
export function seekStartSec(sec: number): number {
  return Math.max(0, sec - SEEK_LEAD_SEC);
}

/** 1 行の中身 → 太字と時刻に分ける。閉じていない `**` は文字のまま残す。 */
export function parseInline(text: string): MdInline[] {
  const parts = text.replace(/`/g, '').split('**');
  const out: MdInline[] = [];
  parts.forEach((part, k) => {
    const closed = k % 2 === 1 && k < parts.length - 1;
    const piece = k % 2 === 1 && !closed ? `**${part}` : part;
    pushWithTimes(out, piece, closed);
  });
  return out;
}

function pushWithTimes(out: MdInline[], text: string, strong: boolean): void {
  let last = 0;
  for (const m of text.matchAll(TIME_RE)) {
    const sec = toSeconds(m[1] ?? '', m[2] ?? '', m[3]);
    if (sec === null) continue;
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: 'text', text: text.slice(last, at), strong });
    out.push({ kind: 'time', sec, strong });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last), strong });
}

function cellsOf(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

/** Markdown の本文 → 表示用のまとまり。 */
export function parseVoiceMarkdown(markdown: string | null | undefined): MdBlock[] {
  const lines = (markdown ?? '').replace(/\r\n?/g, '\n').split('\n');
  const blocks: MdBlock[] = [];
  let code = false;
  for (let i = 0; i < lines.length; i++) {
    const raw = (lines[i] ?? '').replace(/\s+$/, '');
    const t = raw.trim();
    if (t.startsWith('```')) {
      code = !code;
      continue;
    }
    if (code) {
      if (t) blocks.push({ kind: 'para', inlines: [{ kind: 'text', text: raw, strong: false }] });
      continue;
    }
    if (!t || RULE_RE.test(t)) continue;

    let m: RegExpExecArray | null;
    if ((m = HEADING_RE.exec(t))) {
      blocks.push({
        kind: 'heading',
        level: (m[1] ?? '#').length,
        inlines: parseInline(m[2] ?? ''),
      });
    } else if (t.startsWith('|')) {
      const rows: string[][] = [];
      for (; i < lines.length && (lines[i] ?? '').trim().startsWith('|'); i++) {
        const cells = cellsOf(lines[i] ?? '');
        if (!cells.every((c) => TABLE_RULE_RE.test(c))) rows.push(cells);
      }
      i--;
      const [head, ...body] = rows;
      if (!head) continue;
      if (body.length === 0) {
        blocks.push({ kind: 'para', inlines: parseInline(head.join('　')) });
      } else {
        const n = head.length;
        blocks.push({
          kind: 'table',
          head: head.map(parseInline),
          rows: body.map((r) => Array.from({ length: n }, (_, k) => parseInline(r[k] ?? ''))),
        });
      }
    } else if ((m = BULLET_RE.exec(raw))) {
      const depth = (m[1] ?? '').replace(/\t/g, '  ').length >= 2 ? 1 : 0;
      const inlines = parseInline((m[2] ?? '').trim());
      const prev = blocks[blocks.length - 1];
      if (depth === 1 && prev?.kind === 'bullets' && prev.items.length > 0) {
        prev.items[prev.items.length - 1]?.children.push(inlines);
      } else if (prev?.kind === 'bullets') {
        prev.items.push({ inlines, children: [] });
      } else {
        blocks.push({ kind: 'bullets', items: [{ inlines, children: [] }] });
      }
    } else if ((m = NUMBER_RE.exec(raw))) {
      const item = { num: Number(m[2]), inlines: parseInline((m[3] ?? '').trim()) };
      const prev = blocks[blocks.length - 1];
      if (prev?.kind === 'numbers') prev.items.push(item);
      else blocks.push({ kind: 'numbers', items: [item] });
    } else {
      const body = t.startsWith('>') ? t.replace(/^>+/, '').trim() : t;
      blocks.push({ kind: 'para', inlines: parseInline(body) });
    }
  }
  return blocks;
}

/** まとまり → 記号と時刻を除いた文字（一覧の 1 行目など）。 */
export function inlinePlainText(inlines: MdInline[]): string {
  return inlines
    .map((x) => (x.kind === 'text' ? x.text : ''))
    .join('')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * 本文の最初の意味のある行（Markdown の記号と時刻を除く）。
 *
 * 見出し（「# 訪問記録」など）は中身を言わないことが多いので、段落・箇条書き・
 * 番号を先に探し、それが無いときだけ見出しを使う。
 */
export function markdownFirstLine(markdown: string | null | undefined): string {
  const blocks = parseVoiceMarkdown(markdown);
  let heading = '';
  for (const b of blocks) {
    let text = '';
    if (b.kind === 'para') text = inlinePlainText(b.inlines);
    else if (b.kind === 'bullets') text = inlinePlainText(b.items[0]?.inlines ?? []);
    else if (b.kind === 'numbers') text = inlinePlainText(b.items[0]?.inlines ?? []);
    else if (b.kind === 'heading' && !heading) heading = inlinePlainText(b.inlines);
    if (text) return text;
  }
  return heading;
}
