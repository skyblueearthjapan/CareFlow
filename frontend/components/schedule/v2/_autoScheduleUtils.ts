/**
 * Auto-schedule v2 共通ユーティリティ.
 *
 * DiffAddDialog / FullOptimizeDialog / ResetToFixedButton で重複していた
 * 小関数を集約する。
 */
import { ApiError } from '@/lib/api-client';

export function formatErr(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return '不明なエラー';
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
