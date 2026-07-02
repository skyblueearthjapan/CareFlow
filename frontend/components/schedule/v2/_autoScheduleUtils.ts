/**
 * Auto-schedule v2 共通ユーティリティ.
 *
 * DiffAddDialog / FullOptimizeDialog / ResetToFixedButton で重複していた
 * 小関数を集約する。
 */
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import type {
  ApplyIndividualRequest,
  ApplyIndividualResponse,
} from '@/lib/schemas/v2/autoScheduleV2';

export function formatErr(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return '不明なエラー';
}

// ─── P0-2 Commit 3: apply-individual H10 (昼休み) の確認 → force 再送 ────────────

/** FastAPI エラー body から detail 文言を取り出す (str / [{msg}] の両形に対応). */
function extractApiDetail(body: unknown): string | undefined {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      return d
        .map((x) =>
          x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : '',
        )
        .filter(Boolean)
        .join(' / ');
    }
  }
  return undefined;
}

/** H10 昼休み違反による 422 か判定する (detail に「昼休み」を含む 422). */
export function isLunchBreak422(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 422) return false;
  const detail = extractApiDetail(err.body);
  return typeof detail === 'string' && detail.includes('昼休み');
}

/**
 * apply-individual を実行し、H10 昼休み 422 のときのみ確認ダイアログを出して
 * `force_lunch: true` で再送する。
 * 戻り値: 採用成功時はレスポンス / ユーザーが昼休み確認を拒否したら null。
 * それ以外のエラーは throw する (呼出側の既存 catch でトースト表示)。
 */
export async function applyIndividualWithLunchConfirm(
  mutateAsync: (payload: ApplyIndividualRequest) => Promise<ApplyIndividualResponse>,
  payload: ApplyIndividualRequest,
): Promise<ApplyIndividualResponse | null> {
  try {
    return await mutateAsync(payload);
  } catch (err) {
    if (!isLunchBreak422(err)) throw err;
    const ok =
      typeof window !== 'undefined' &&
      window.confirm(
        'この枠は昼休みと重複します。それでも固定枠として適用しますか？\n(適用すると昼休みは警告として記録されます)',
      );
    if (!ok) return null;
    return await mutateAsync({ ...payload, force_lunch: true });
  }
}

/**
 * apply-individual / apply-week-only レスポンスの warnings (string[]) を toast 表示する。
 * 多数の場合は最初の 3 件 + 「他 N 件」に要約。空 / undefined のときは何も出さない。
 */
export function toastApplyWarnings(warnings: readonly string[] | undefined): void {
  if (!warnings || warnings.length === 0) return;
  const shown = warnings.slice(0, 3);
  for (const w of shown) toast.warning(w);
  if (warnings.length > shown.length) {
    toast.warning(`他 ${warnings.length - shown.length} 件の警告があります`);
  }
}

export function formatDelta(km: number): string {
  if (km === 0) return '±0km';
  const sign = km > 0 ? '+' : '';
  return `${sign}${km.toFixed(1)}km`;
}

/** Pydantic は "HH:MM:SS" で返してくるので末尾を切る. */
export function trimSeconds(t: string): string {
  return t.length >= 5 ? t.slice(0, 5) : t;
}
