/**
 * 訪問記録（PC `/records`）の表示ヘルパ — 状態バッジ・日時・要約 1 行目。
 *
 * モバイルの `VisitRecordCard` と**同じ語彙**にそろえる（文字起こし中 / 要約済み /
 * 失敗 / 要紐付け）。PC は一覧で「確認済み」を別カラムに出すので、バッジ側は
 * 処理の状態だけを表す（モックの b-ok/b-wait/b-err に対応）。
 */

import type { VisitRecordingRead } from '@/lib/queries/visit-recordings';

export type RecordBadgeVariant = 'secondary' | 'success' | 'warning' | 'info' | 'destructive';

/** 一覧・詳細で選べる状態フィルタ（BE の `status` にそのまま渡す）。 */
export const RECORD_STATUS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'transcribing', label: '文字起こし中' },
  { value: 'summarized', label: '要約済み' },
  { value: 'failed', label: '失敗' },
  { value: 'unlinked', label: '要紐付け' },
];

/** 状態 → バッジの見た目と文言（未知の状態は「受付済み」に落とす）。 */
export function recordStatusMeta(status: string): { label: string; variant: RecordBadgeVariant } {
  switch (status) {
    case 'transcribing':
      return { label: '文字起こし中', variant: 'info' };
    case 'summarized':
      return { label: '要約済み', variant: 'success' };
    case 'failed':
      return { label: '失敗', variant: 'destructive' };
    case 'unlinked':
      return { label: '要紐付け', variant: 'warning' };
    default:
      return { label: '受付済み', variant: 'secondary' };
  }
}

const WEEKDAYS = ['日', '月', '火', '水', '木', '金', '土'] as const;

/** ISO 日時 → `M/D (曜)`。読めない値は空文字（行は落とさない）。 */
export function formatRecordDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getMonth() + 1}/${d.getDate()} (${WEEKDAYS[d.getDay()]})`;
}

/** ISO 日時 → `HH:MM`。 */
export function formatRecordTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** ISO 日時 → `YYYY/MM/DD (曜) HH:MM`（詳細ダイアログのヘッダ）。 */
export function formatRecordDateTime(iso: string | null | undefined): string {
  if (!iso) return '--';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '--';
  const ymd = `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(
    d.getDate(),
  ).padStart(2, '0')}`;
  return `${ymd} (${WEEKDAYS[d.getDay()]}) ${formatRecordTime(iso)}`;
}

/** 秒 → `M:SS` / `H:MM:SS`。負値・非数は空文字。 */
export function formatDurationSec(sec: number | null | undefined): string {
  if (typeof sec !== 'number' || !Number.isFinite(sec) || sec < 0) return '';
  const total = Math.floor(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  return h > 0 ? `${h}:${mm}:${String(s).padStart(2, '0')}` : `${mm}:${String(s).padStart(2, '0')}`;
}

/** 要約 JSON の見出し（`free` だけ日本語に読み替える・モバイルと同じ規則）。 */
export function summarySectionLabel(key: string): string {
  return key === 'free' ? 'その他' : key;
}

export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/**
 * 一覧の「要約（1 行目）」。`summary_text` の先頭の非空行を返す。
 * 無ければ状態に応じた代替文（文字起こし中 / 失敗 / 要紐付け）、それも無ければ「—」。
 */
export function summaryFirstLine(rec: VisitRecordingRead): string {
  const line = (rec.summary_text ?? '')
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l !== '');
  if (line) return line;
  switch (rec.status) {
    case 'transcribing':
    case 'uploaded':
      return '文字起こし中…';
    case 'failed':
      return rec.error_message ?? '処理に失敗しました。再処理してください。';
    case 'unlinked':
      return '患者未紐付け（録音後に選択が必要）';
    default:
      return '—';
  }
}

/**
 * 要約の描き方（PC `/records` とモバイル `VisitRecordCard` で**共通の 1 規則**）。
 *
 *   1. `summary_edited_at` があれば `summary_text`（人が直した本文）を平文で出す
 *      — 手を入れたのに AI の JSON が出続けるのは事故。
 *   2. 無ければ `summary` JSON を見出し＋バイタルグリッドで構造表示する
 *      — 読みやすさは構造表示のほうが上なので、未編集ならこちらが本命。
 *   3. JSON も無ければ `summary_text` を平文で出す（BE が本文しか持たない場合）。
 *
 * 片方の画面だけ規則を変えると「PC では直した要約、モバイルでは古い要約」に
 * なるため、判定はここ 1 箇所に置く。
 */
export type SummaryDisplayMode = 'text' | 'json' | 'empty';

export function summaryDisplayMode(
  rec: Pick<VisitRecordingRead, 'summary' | 'summary_text' | 'summary_edited_at'>,
): SummaryDisplayMode {
  const hasText = !!rec.summary_text && rec.summary_text.trim() !== '';
  const hasJson = !!rec.summary && Object.keys(rec.summary).length > 0;
  if (rec.summary_edited_at && hasText) return 'text';
  if (hasJson) return 'json';
  return hasText ? 'text' : 'empty';
}

/**
 * 要約 JSON → 人が直せるプレーンテキスト。
 *
 * 「編集」は `summary_text` を書き換える契約なので、`summary_text` がまだ無い
 * （BE が JSON しか持っていない）記録でも編集を始められるよう、JSON から
 * 見出し付きの本文を組み立てて初期値にする。
 */
export function summaryToText(rec: VisitRecordingRead): string {
  if (rec.summary_text && rec.summary_text.trim() !== '') return rec.summary_text;
  const summary = rec.summary;
  if (!summary) return '';
  const blocks: string[] = [];
  for (const [key, value] of Object.entries(summary)) {
    const label = summarySectionLabel(key);
    if (Array.isArray(value)) {
      const items = value.map((v) => String(v)).filter((v) => v.trim() !== '');
      if (items.length > 0) blocks.push(`【${label}】\n${items.map((i) => `・${i}`).join('\n')}`);
    } else if (isPlainObject(value)) {
      const pairs = Object.entries(value)
        .filter(([, v]) => v !== null && String(v).trim() !== '')
        .map(([k, v]) => `${k}: ${String(v)}`);
      if (pairs.length > 0) blocks.push(`【${label}】\n${pairs.join('\n')}`);
    } else {
      const text = value == null ? '' : String(value).trim();
      if (text !== '') blocks.push(`【${label}】\n${text}`);
    }
  }
  return blocks.join('\n\n');
}

/** 文字起こし全文の 1 発言（`transcript_json` の寛容パース結果）。 */
export interface TranscriptSegment {
  speaker: string | null;
  /** 秒。無ければ null。 */
  offsetSec: number | null;
  text: string;
}

/** 秒 → `MM:SS`（話者行のタイムスタンプ）。 */
export function formatOffset(sec: number | null): string {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return '';
  const total = Math.floor(sec);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * `transcript_json` の寛容パース。
 *
 * BE のプロンプトは JSON を返すが、形は `prompt_version` で動きうる。配列でも
 * `{segments: [...]}` でも読み、話者・開始秒・本文の**別名を許す**。1 件も
 * 取れなければ null を返し、呼び出し側は素の `transcript` にフォールバックする。
 */
export function parseTranscriptSegments(raw: unknown): TranscriptSegment[] | null {
  const rows: unknown[] = Array.isArray(raw)
    ? raw
    : isPlainObject(raw) && Array.isArray(raw.segments)
      ? raw.segments
      : [];
  const out: TranscriptSegment[] = [];
  for (const row of rows) {
    if (!isPlainObject(row)) continue;
    const text = firstString(row, ['text', 'content', 'utterance']);
    if (!text) continue;
    out.push({
      speaker: firstString(row, ['speaker', 'speaker_label', 'role']),
      offsetSec: firstNumber(row, ['start', 'start_sec', 'offset', 'offset_sec', 't']),
      text,
    });
  }
  return out.length > 0 ? out : null;
}

function firstString(row: Record<string, unknown>, keys: string[]): string | null {
  for (const k of keys) {
    const v = row[k];
    if (typeof v === 'string' && v.trim() !== '') return v.trim();
  }
  return null;
}

function firstNumber(row: Record<string, unknown>, keys: string[]): number | null {
  for (const k of keys) {
    const v = row[k];
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v))) return Number(v);
  }
  return null;
}

/**
 * 話者ラベルの色（モック ⑥: 看護師=ピンク / 患者=ティール / それ以外=無彩色）。
 * ラベルは自由文なので**部分一致**で寄せる。
 */
export function speakerTone(speaker: string | null): string {
  const s = speaker ?? '';
  if (s.includes('看護') || s.includes('スタッフ') || s.includes('nurse')) {
    return 'border-brand-primary bg-brand-primary-light text-brand-primary-hover';
  }
  if (s.includes('患者') || s.includes('利用者') || s.includes('patient')) {
    return 'border-info-strong bg-info-bg text-info-strong';
  }
  return 'border-border-default bg-bg-muted text-text-secondary';
}

/**
 * UUID 形式か（URL クエリの素性検査・レビュー L-3）。
 *
 * `?patient=` などは人が手で書き換えられるので、そのまま BE へ渡さない。
 * 形が違う値は「指定なし」として無視する（400 を撒かない・誤絞り込みもしない）。
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string | null | undefined): boolean {
  return !!value && UUID_RE.test(value);
}

/** UUID ならそのまま、そうでなければ空文字（URL クエリの受理）。 */
export function acceptUuid(value: string | null | undefined): string {
  return isUuid(value) ? (value as string) : '';
}
