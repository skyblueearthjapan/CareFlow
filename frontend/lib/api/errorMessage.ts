/**
 * API エラーから現場向けの日本語メッセージを取り出す共有ヘルパ。
 *
 * 背景 (2026-08-07):
 *   `fetcher` / `apiFetch` が投げる `ApiError` の `message` は
 *   `"API 422 Unprocessable Entity (/api/v1/...)"` という機械向け文字列で、
 *   **理由はすべて `body` 側に入っている**。呼び出し側の多くが `e.message` だけを
 *   toast に出していたため、BE が「◯曜 枠0 のピン留めされた枠を変更・削除しよう
 *   としています」と丁寧に返しているのに、画面には `API 422` としか出ていなかった。
 *
 * 対応する `detail` の形 (FastAPI が返しうる 3 系統すべて):
 *   1. `{"detail": "文字列"}`                       … 素の HTTPException
 *   2. `{"detail": {"message": "...", "violations": [{"message": "..."}]}}`
 *                                                   … 構造化 HTTPException
 *                                                     (例: PFV の pinned 保護)
 *   3. `{"detail": [{"loc": [...], "msg": "..."}]}`  … pydantic の検証エラー
 *
 * どれにも当てはまらなければ元の `Error.message` にフォールバックするので、
 * 既存の呼び出しを置き換えても表示が悪化することはない。
 */
import { ApiError } from '@/lib/api-client';

const DEFAULT_FALLBACK = '不明なエラーが発生しました';

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** 配列の各要素から最初に見つかった非空文字列プロパティを拾う (要素が文字列ならそれ自体)。 */
function collectMessages(items: readonly unknown[], keys: readonly string[]): string[] {
  const out: string[] = [];
  for (const item of items) {
    if (typeof item === 'string') {
      if (item.length > 0) out.push(item);
      continue;
    }
    const rec = asRecord(item);
    if (rec === null) continue;
    for (const key of keys) {
      const v = rec[key];
      if (typeof v === 'string' && v.length > 0) {
        out.push(v);
        break;
      }
    }
  }
  return out;
}

/**
 * `ApiError` (またはその他の Error) から表示用メッセージを組み立てる。
 *
 * 複数の指摘がある場合は改行区切りで返す。toast でも Alert でもそのまま出せる。
 */
export function apiErrorMessage(error: unknown, fallback: string = DEFAULT_FALLBACK): string {
  const generic = error instanceof Error && error.message.length > 0 ? error.message : fallback;
  if (!(error instanceof ApiError)) return generic;

  const body = asRecord(error.body);
  const detail = body === null ? undefined : body.detail;

  // 1. 素の文字列 detail.
  if (typeof detail === 'string' && detail.length > 0) return detail;

  // 3. pydantic 検証エラー (配列).
  if (Array.isArray(detail)) {
    const msgs = collectMessages(detail, ['msg', 'message']);
    if (msgs.length > 0) return msgs.join('\n');
  }

  // 2. 構造化 detail ({message, violations}).
  const detailRec = asRecord(detail);
  if (detailRec !== null) {
    const head = typeof detailRec.message === 'string' ? detailRec.message : null;
    const violations = Array.isArray(detailRec.violations)
      ? collectMessages(detailRec.violations, ['message'])
      : [];
    const parts = [head, ...violations].filter((s): s is string => s !== null && s.length > 0);
    if (parts.length > 0) return parts.join('\n');
    if (typeof detailRec.code === 'string' && detailRec.code.length > 0) return detailRec.code;
  }

  return generic;
}
