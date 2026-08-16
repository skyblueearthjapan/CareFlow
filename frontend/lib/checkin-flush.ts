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
  if (status === 410) return detailOf(err) ?? 'QRが再発行され無効になったため';
  return detailOf(err) ?? '送信できないため';
}

/** キュー entry の送信先パス。 */
function pathOf(entry: PendingEntry): string {
  if (entry.kind === 'adhoc_arrival') return ADHOC_CHECKIN_PATH;
  return `/api/v1/visits/${entry.visit_id}/${REST_PATH[entry.kind]}`;
}

/** FastAPI / リバースプロキシがルート不在で返す既定 detail (大文字小文字は無視)。 */
const ROUTE_MISSING_DETAIL = 'not found';

/**
 * 「サーバにそのルートがまだ無い」ことを示す応答か (最終レビュー M-1)。
 *
 * `POST /visits/adhoc-checkin` は本機能で新設したルートなので、**BE をロールバック
 * した瞬間だけ** 404 (ルート不在) / 405 (メソッド不許可) が返る。これを他の 4xx と
 * 同じ「サーバの確定回答」として破棄すると、オフライン退避した**予定外訪問の記録が
 * 永久に消える** (現場は打刻済みのつもりでいる)。BE が戻れば必ず送れるので、
 * retryable としてキューに残す。
 *
 * ただし患者 QR に起因する 404 (`resolve_qr_patient` の `QR token not found` 等) は
 * 再送しても直らないため従来どおり破棄する。ルート不在の 404 は detail が無い
 * (HTML エラーページ / プロキシ) か FastAPI 既定の `Not Found` なので、そこで
 * 見分ける。405 は QR 起因では起こり得ないので無条件に保持する。
 *
 * 対象は `adhoc_arrival` のみ。通常の `/visits/{id}/checkin` 等は旧 BE にも存在する
 * ため、そちらの 404 は「無効な QR」として破棄する従来動作を保つ。
 */
function isRouteMissing(entry: PendingEntry, err: unknown): boolean {
  if (entry.kind !== 'adhoc_arrival') return false;
  if (!(err instanceof ApiError)) return false;
  if (err.status === 405) return true;
  if (err.status !== 404) return false;
  const detail = detailOf(err);
  return detail === null || detail.trim().toLowerCase() === ROUTE_MISSING_DETAIL;
}

/**
 * 保留 entry を 1 件再 POST する (ベストエフォート)。届いたら resolve。
 * 再試行しても直らない 4xx (無効/別患者の QR) は {@link DropPendingError} を
 * throw し、`flushPending` がキューから取り除いたうえで理由を呼び出し元へ返す
 * (黙って消さない)。サーバ未達 (ネットワーク / 5xx) と、旧 BE にルートが無いだけの
 * 404/405 ({@link isRouteMissing}) は生のエラーで reject し、entry はキューに残って
 * 次回再試行される。
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
    // 旧 BE にルートが無いだけ (404/405) — BE 復帰で送れるのでキューに残す。
    if (isRouteMissing(entry, err)) throw err;
    // definitive 4xx → won't succeed on retry; drop it WITH a reason.
    throw new DropPendingError(dropReasonOf(err));
  }
}

/**
 * 実行中 / 予約済みの flush (staffId 毎)。
 *
 * 再送は複数の画面 (一覧 / 訪問詳細 / QR ランディング) と `online` イベントから
 * 同時に呼ばれる。ガードが無いと**同じ entry を並行 POST**してしまい、先に届いた
 * 方が成功・後から届いた方が 409 (対象外) を受けて「送信できませんでした」と
 * 誤通知したうえで、実際には記録済みの控えを破棄してしまう。ここで staffId 毎に
 * 直列化し、後発の呼び出しは前の flush の完了後にキューを読み直す (= 既に送信済み
 * の entry は消えているので二重 POST にならない)。
 */
const inFlight = new Map<string, Promise<FlushResult>>();

/** そのスタッフの保留分をすべて再送する (同一 staffId の並行実行は直列化)。 */
export function flushCheckinQueue(
  staffId: string,
  accessToken: string | null,
  refreshToken: string | null,
): Promise<FlushResult> {
  const prev = inFlight.get(staffId);
  const next = (prev ? prev.then(noop, noop) : Promise.resolve()).then(() =>
    flushPending(staffId, (entry) => postPending(entry, accessToken, refreshToken)),
  );
  inFlight.set(staffId, next);
  void next.then(
    () => release(staffId, next),
    () => release(staffId, next),
  );
  return next;
}

function noop(): void {
  /* 前の flush の結果 / 失敗は後発の実行可否に影響させない。 */
}

function release(staffId: string, settled: Promise<FlushResult>): void {
  if (inFlight.get(staffId) === settled) inFlight.delete(staffId);
}
