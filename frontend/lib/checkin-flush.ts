/**
 * 未送信打刻の再送 (`checkin-queue` の flush 実体)。
 *
 * 訪問詳細 (`/m/today/{visitId}`) だけでなく QR ランディング (`/q/{token}`) からも
 * 呼べるよう、ページから切り出した共有モジュール。予定外訪問 (`adhoc_arrival`) は
 * `/q` で退避されるため、次に QR を読んだ時点 (= 電波が戻っている可能性が高い)
 * でも再送を試みたい、というのが分離の理由。
 *
 * 送信先はキューの `kind` で決まる:
 *   arrival / departure / no_show … `POST /visits/{id}/{checkin|checkout|no-show}`
 *   adhoc_arrival                 … `POST /visits/adhoc-checkin` (visit をサーバが生成)
 */
import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  DropPendingError,
  flushPending,
  type FlushResult,
  type PendingEntry,
  type PendingKind,
} from '@/lib/checkin-queue';

/** 予定外訪問の到着打刻 (設計 §4-3)。visit はサーバが生成して返す。 */
export const ADHOC_CHECKIN_PATH = '/api/v1/visits/adhoc-checkin';

const REST_PATH: Record<Exclude<PendingKind, 'adhoc_arrival'>, string> = {
  arrival: 'checkin',
  departure: 'checkout',
  no_show: 'no-show',
};

/**
 * サーバが記録を確実に受け取っていないと言える失敗か — fetch レベルの
 * ネットワークエラー、または 5xx (サーバ側の一時障害)。これらだけをローカル退避 +
 * 再送キュー行きにする。4xx (404/409 含む) はサーバの確定回答なので退避しない。
 */
export function isServerUnreachable(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true; // fetch threw → network error
  return err.status >= 500;
}

/** ApiError の body から backend の `detail` 文字列を取り出す。 */
export function detailOf(err: unknown): string | null {
  if (err instanceof ApiError && err.body && typeof err.body === 'object') {
    const d = (err.body as Record<string, unknown>).detail;
    if (typeof d === 'string') return d;
  }
  return null;
}

/** 再送不能な 4xx の破棄理由 (利用者へ通知する文言)。 */
export function dropReasonOf(err: unknown): string {
  const status = err instanceof ApiError ? err.status : null;
  if (status === 404) return detailOf(err) ?? '無効なQRのため';
  if (status === 409) return detailOf(err) ?? '対象外の患者のため';
  return detailOf(err) ?? '送信できないため';
}

/** キュー entry の送信先パス。 */
function pathOf(entry: PendingEntry): string {
  if (entry.kind === 'adhoc_arrival') return ADHOC_CHECKIN_PATH;
  return `/api/v1/visits/${entry.visit_id}/${REST_PATH[entry.kind]}`;
}

/**
 * 保留 entry を 1 件再 POST する (ベストエフォート)。届いたら resolve。
 * 再試行しても直らない 4xx (無効/別患者の QR) は {@link DropPendingError} を
 * throw し、`flushPending` がキューから取り除いたうえで理由を呼び出し元へ返す
 * (黙って消さない)。サーバ未達 (ネットワーク / 5xx) は生のエラーで reject し、
 * entry はキューに残って次回再試行される。
 */
export async function postPending(
  entry: PendingEntry,
  accessToken: string | null,
  refreshToken: string | null,
): Promise<void> {
  try {
    await fetcher(pathOf(entry), {
      method: 'POST',
      body: JSON.stringify(entry.payload),
      accessToken,
      refreshToken,
    });
  } catch (err) {
    if (isServerUnreachable(err)) throw err; // keep queued (network / 5xx)
    // definitive 4xx → won't succeed on retry; drop it WITH a reason.
    throw new DropPendingError(dropReasonOf(err));
  }
}

/** そのスタッフの保留分をすべて再送する。 */
export function flushCheckinQueue(
  staffId: string,
  accessToken: string | null,
  refreshToken: string | null,
): Promise<FlushResult> {
  return flushPending(staffId, (entry) => postPending(entry, accessToken, refreshToken));
}
