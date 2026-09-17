/**
 * 未送信の音声記録キュー（設計 §2-2 / §10-5）。
 *
 * 訪問先は電波が弱い。録音は**端末に残ってさえいれば後で必ず送れる**ので、
 * 打刻キュー（`lib/checkin-queue.ts`）と同じ約束で運ぶ:
 *   - **黙って消えない**: TTL では消さない。送信できたときだけ取り除く。
 *   - **4xx でも消さない**（レビュー C-3）: 再送しても直らない確定エラーは
 *     キューから外すが `voice-failed` ストアへ**音声ごと退避**し、画面から
 *     再送 / 端末保存 / 明示削除ができる。破棄を不可逆にしない。
 *   - **ネット障害 / 5xx / 401 は残す**: 401 は一度だけトークンを更新して再試行し、
 *     それでも駄目なら残す（次のマウント = 新しいセッションで送る）。
 *   - **ルート不在は残す**（レビュー C-2 / M-A）: BE をロールバックした瞬間だけ返る
 *     404（detail が空 か Starlette 既定の `"Not Found"`）/ 405、詰まりの
 *     408 / 423 / 429、Cloudflare の HTML エラーページは「サーバの確定回答」では
 *     ないのでキューに残す。日本語 detail 付きの 404（担当外の訪問です 等）は
 *     確定回答なので `voice-failed` へ。ただし残す判定にも上限を置く
 *     （{@link VOICE_MAX_ATTEMPTS} 回で退避）。
 *   - **staffId 毎に直列**: 同じ entry を並行 POST しない（タブ跨ぎは Web Locks）。
 *
 * 打刻と違い Blob を持つため置き場は localStorage ではなく IndexedDB
 * （`lib/voice/idb.ts` の `voice-pending` / `voice-failed`）。
 */
'use client';

import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { toast } from '@/components/ui/sonner';
import { FAILED_STORE, idbDelete, idbGetAll, idbPut, PENDING_STORE } from '@/lib/voice/idb';

/** `POST /api/v1/visit-recordings`（multipart・設計 §10-3）。 */
export const VISIT_RECORDINGS_PATH = '/api/v1/visit-recordings';

/** 未送信 1 件（メタ + 音声本体）。 */
export interface PendingVoice {
  /** IndexedDB のキー。`clientId` があれば同じ値（＝再送しても 1 件に畳める）。 */
  id: string;
  /**
   * BE へ送る `client_id`（UUID v4）。作れない端末では null で、その場合は
   * **送らない**（レビュー M-C）。
   */
  clientId?: string | null;
  staffId: string;
  visitId?: string | null;
  patientId?: string | null;
  /** 端末時刻（ISO 8601）。サーバは `recorded_at` として読む。 */
  recordedAt: string;
  durationSec: number;
  mimeType: string;
  blob: Blob;
  consent: boolean;
  attempts: number;
  lastError?: string | null;
  /** Epoch ms — キュー投入時刻（表示・順序用。失効には使わない）。 */
  queuedAt: number;
}

/** 送れなかった録音（4xx）。`voice-failed` ストアの行。 */
export interface FailedVoice extends PendingVoice {
  /** 退避した時刻（epoch ms）。 */
  droppedAt: number;
  /** 送れなかった理由（利用者へ見せる文言）。 */
  reason: string;
}

/** 4xx で退避した entry と理由。 */
export interface DroppedVoice {
  entry: PendingVoice;
  reason: string;
}

/**
 * 送信できた 1 件（キューの行 → サーバ上の録音）。
 *
 * **「いま保存したのはどれか」を推測させないため**の対応表（2026-09-18 是正）。
 * これが無いと、保存直後の画面は「自分の未紐付け一覧のいちばん新しい行」を
 * 今しがたの録音だと決め打ちするしかなく、2 本続けて録ると取り違える。
 *
 * `recordingId` は 202 の `VisitRecordingRead.id`。409（`client_id` 重複＝前回の
 * 送信が届いていた）でも BE が既存行を返せばその id が入る。id を読めない応答
 * （古い BE・本文なし）では null — 呼び出し側は「送れたが id は不明」として扱う。
 */
export interface SentVoice {
  /** キューの行 id（`PendingVoice.id`＝`client_id`）。 */
  entryId: string;
  /** サーバ上の録音 id（読めなければ null）。 */
  recordingId: string | null;
}

export interface VoiceFlushResult {
  /** 送信できた件数。 */
  sent: number;
  /** 送れず残った件数。 */
  remaining: number;
  /** 4xx で `voice-failed` へ退避した entry（理由付き）。 */
  dropped: DroppedVoice[];
  /** 退避された録音の総数（バナー表示用）。 */
  failed: number;
  /** 送信できた行とサーバ上の録音 id の対応（送信順）。 */
  sentEntries: SentVoice[];
}

export interface VoiceAuth {
  accessToken: string | null;
  refreshToken?: string | null;
}

/**
 * 端末側の一意キー（レビュー M-C）。UUID v4 を作れなければ null。
 *
 * `crypto.randomUUID` は**セキュアコンテキストにしか無い**。社内 LAN の `http://`
 * や古い WebView では `undefined` になるので `getRandomValues` から RFC4122 v4 を
 * 自分で組み立てる。どちらも無い端末では **`client_id` を送らない** — `Date.now()`
 * 由来の値は 2 台で衝突しうるので、BE 側の重複判定に使わせてはならない。
 */
export function newVoiceClientId(): string | null {
  try {
    if (typeof crypto === 'undefined') return null;
    if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    if (typeof crypto.getRandomValues === 'function') {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40; // version 4
      bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80; // variant 10xx
      const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
      return [
        hex.slice(0, 8),
        hex.slice(8, 12),
        hex.slice(12, 16),
        hex.slice(16, 20),
        hex.slice(20),
      ].join('-');
    }
  } catch {
    /* 非対応 — client_id 無しで送る */
  }
  return null;
}

/** IndexedDB のキー（`client_id` を作れない端末でも行は要る）。 */
function genLocalKey(): string {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') return '';
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

/** MIME からアップロードのファイル名を決める（サーバは `device_mime` を正とする）。 */
export function audioFileName(mimeType: string): string {
  const base = (mimeType || '').split(';')[0] ?? '';
  if (base.includes('mp4')) return 'recording.m4a';
  if (base.includes('ogg')) return 'recording.ogg';
  if (base.includes('mpeg')) return 'recording.mp3';
  if (base.includes('wav')) return 'recording.wav';
  return 'recording.webm';
}

/**
 * 未送信キューへ積む。作られた entry を返す。
 *
 * **保存できなければ `null`**（レビュー C-1）。呼び出し側は「保存しました」と
 * 言わずに、手元の Blob を握ったまま別の出口（再保存 / ダウンロード / 直接送信）を
 * 出す責任がある。
 *
 * `clientId` は呼び出し側が先に決めて渡せる（レビュー M-B）。画面が「直接送信」へ
 * 切り替えても同じキーで送るため、**キューと直接送信で 1 つの値**を共有する。
 */
export async function enqueueVoice(
  input: Omit<PendingVoice, 'id' | 'attempts' | 'queuedAt' | 'lastError'>,
): Promise<PendingVoice | null> {
  if (typeof window === 'undefined' || !input.staffId) return null;
  const clientId = input.clientId ?? newVoiceClientId();
  const entry: PendingVoice = {
    ...input,
    clientId,
    id: clientId ?? genLocalKey(),
    attempts: 0,
    lastError: null,
    queuedAt: Date.now(),
  };
  try {
    await idbPut(PENDING_STORE, entry);
  } catch (err) {
    console.warn('[voice] 未送信キューへ保存できませんでした', err);
    return null;
  }
  return entry;
}

/** そのスタッフの未送信一覧（古い順）。 */
export async function listVoice(staffId: string): Promise<PendingVoice[]> {
  if (!staffId) return [];
  const rows = await idbGetAll<PendingVoice>(PENDING_STORE);
  return rows.filter((r) => r.staffId === staffId).sort((a, b) => a.queuedAt - b.queuedAt);
}

/** そのスタッフの未送信件数。 */
export async function countVoice(staffId: string): Promise<number> {
  return (await listVoice(staffId)).length;
}

/** 1 件取り除く。 */
export async function removeVoice(id: string): Promise<void> {
  await idbDelete(PENDING_STORE, id);
}

// -- 送れなかった録音（`voice-failed`・レビュー C-3） -------------------------

/** そのスタッフの「送れなかった録音」一覧（新しい順）。 */
export async function listFailedVoice(staffId: string): Promise<FailedVoice[]> {
  if (!staffId) return [];
  const rows = await idbGetAll<FailedVoice>(FAILED_STORE);
  return rows.filter((r) => r.staffId === staffId).sort((a, b) => b.droppedAt - a.droppedAt);
}

/** そのスタッフの「送れなかった録音」件数。 */
export async function countFailedVoice(staffId: string): Promise<number> {
  return (await listFailedVoice(staffId)).length;
}

/** 明示削除（画面で確認してから呼ぶ）。 */
export async function removeFailedVoice(id: string): Promise<void> {
  await idbDelete(FAILED_STORE, id);
}

/**
 * 退避した録音をキューへ戻す（「再送」）。戻せたら true。
 *
 * 破棄理由が BE 側の修正で消えることはある（患者紐付け・権限）ので、現場が
 * 自分の判断でもう一度送れる道を残す。
 */
export async function requeueFailedVoice(id: string): Promise<boolean> {
  const rows = await idbGetAll<FailedVoice>(FAILED_STORE);
  const row = rows.find((r) => r.id === id);
  if (!row) return false;
  // 退避用の 2 項目（`droppedAt` / `reason`）は持ち越さずに組み直す。
  const entry: PendingVoice = {
    id: row.id,
    clientId: row.clientId ?? null,
    staffId: row.staffId,
    visitId: row.visitId,
    patientId: row.patientId,
    recordedAt: row.recordedAt,
    durationSec: row.durationSec,
    mimeType: row.mimeType,
    blob: row.blob,
    consent: row.consent,
    attempts: 0,
    lastError: null,
    queuedAt: Date.now(),
  };
  try {
    await idbPut(PENDING_STORE, entry);
  } catch (err) {
    console.warn('[voice] 再送キューへ戻せませんでした', err);
    return false;
  }
  await idbDelete(FAILED_STORE, id);
  return true;
}

/**
 * 4xx の entry を `voice-failed` へ退避する。移せたら true（レビュー L-B）。
 *
 * 移せなかったときは **pending に残したまま false** を返す。呼び出し側はこれを
 * `dropped` に数えない — 「送れなかった録音から再送できます」と案内しても、その
 * 一覧に無いからである（案内できる出口が無いなら案内しない）。
 */
async function moveToFailed(entry: PendingVoice, reason: string): Promise<boolean> {
  const row: FailedVoice = { ...entry, droppedAt: Date.now(), reason };
  try {
    await idbPut(FAILED_STORE, row);
  } catch (err) {
    // 退避先にも置けない（容量）。せめてキューには残す = 次回また試す。
    console.warn('[voice] 退避ストアへ移せませんでした', err);
    return false;
  }
  await removeVoice(entry.id);
  return true;
}

/** 破棄理由（利用者へ見せる文言）。 */
function dropReason(status: number, detail: string | null): string {
  if (detail) return detail;
  if (status === 413) return '音声が大きすぎるため';
  if (status === 415) return '対応していない音声形式のため';
  if (status === 404) return '対象の訪問が見つからないため';
  if (status === 403) return '権限が無いため';
  return `送信できないため（${status}）`;
}

/** 応答ボディ（生テキストと JSON の `detail`）。 */
interface ResponseBody {
  text: string | null;
  detail: string | null;
}

async function readBody(res: Response): Promise<ResponseBody> {
  let text: string | null = null;
  try {
    text = await res.text();
  } catch {
    return { text: null, detail: null };
  }
  if (!text) return { text: null, detail: null };
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === 'object') {
      const d = (body as Record<string, unknown>).detail;
      if (typeof d === 'string') return { text, detail: d };
    }
  } catch {
    /* JSON ではない（HTML エラーページなど） */
  }
  return { text, detail: null };
}

/** Starlette がルート不在で返す既定 detail（実体は `"Not Found"`・比較は小文字で）。 */
const ROUTE_MISSING_DETAIL = 'not found';

/**
 * 再試行の上限（レビュー M-A）。ここに達したら `voice-failed` へ落とす。
 *
 * 「残す」判定が続く entry（恒久的な 429、直らないプロキシ設定）は、上限が無いと
 * マウントのたびに送信を試し続けて端末の電池と通信を食う。20 回＝現場の 1 日に
 * 何度開いても届かなかった、の意味。捨てはしない（画面から再送できる）。
 */
export const VOICE_MAX_ATTEMPTS = 20;

/** 応答が API ではなく HTML（Cloudflare / プロキシのエラーページ）か。 */
function isHtmlBody(text: string | null): boolean {
  if (!text) return false;
  return text.trimStart().startsWith('<');
}

/**
 * 「サーバの確定回答ではない」4xx か（レビュー C-2 — `checkin-flush.ts` の
 * {@link isRouteMissing} と同じ規則）。
 *
 *   405 … このルートに POST が無い = BE ロールバック中。無条件で残す。
 *   404 … `detail` が空（HTML / プロキシ）か Starlette 既定の `"Not Found"` の
 *         **ときだけ**残す。BE が返す業務上の 404（担当外の訪問です・対象の訪問が
 *         見つかりません等）は日本語の detail を持つ確定回答なので `voice-failed`
 *         へ落とす（レビュー M-A）。
 *   408 / 423 / 429 … タイムアウト / ロック / レート制限。時間が解決する。
 *   HTML ボディ … Cloudflare が返した = BE まで届いていない。
 */
function isRetryableClientError(status: number, body: ResponseBody): boolean {
  if (isHtmlBody(body.text)) return true;
  if (status === 405) return true;
  if (status === 408 || status === 423 || status === 429) return true;
  if (status === 404) {
    if (!body.detail) return true;
    return body.detail.trim().toLowerCase() === ROUTE_MISSING_DETAIL;
  }
  return false;
}

/** 期限切れアクセストークンを 1 度だけ更新する（失敗は null）。 */
async function refreshAccessToken(refreshToken: string): Promise<string | null> {
  try {
    const res = await fetch(`${resolveBaseUrl()}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: 'no-store',
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { access_token?: string };
    return typeof body.access_token === 'string' ? body.access_token : null;
  } catch {
    return null;
  }
}

function buildForm(entry: PendingVoice): FormData {
  const form = new FormData();
  form.append('audio', entry.blob, audioFileName(entry.mimeType));
  if (entry.visitId) form.append('visit_id', entry.visitId);
  if (entry.patientId) form.append('patient_id', entry.patientId);
  form.append('recorded_at', entry.recordedAt);
  form.append('duration_sec', String(Math.max(0, Math.round(entry.durationSec))));
  form.append('consent', entry.consent ? 'true' : 'false');
  if (entry.mimeType) form.append('device_mime', entry.mimeType);
  // 端末側の一意キー（レビュー H-4）。再送が重なっても BE 側で 1 件に畳める。
  // UUID を作れなかった端末では付けない（衝突する値を重複判定に使わせない・M-C）。
  if (entry.clientId) form.append('client_id', entry.clientId);
  return form;
}

/** 送信結果: 送れた（サーバ上の id つき） / 残す（理由つき） / 退避（理由つき）。 */
type PostOutcome =
  | { kind: 'sent'; recordingId: string | null }
  | { kind: 'keep'; reason: string }
  | { kind: 'drop'; reason: string };

/** 応答本文から `VisitRecordingRead.id` を拾う（読めなければ null）。 */
function recordingIdOf(text: string | null): string | null {
  if (!text) return null;
  try {
    const body: unknown = JSON.parse(text);
    if (body && typeof body === 'object') {
      const id = (body as Record<string, unknown>).id;
      if (typeof id === 'string' && id) return id;
    }
  } catch {
    /* JSON ではない（HTML・空） */
  }
  return null;
}

async function postVoice(entry: PendingVoice, auth: VoiceAuth): Promise<PostOutcome> {
  const url = `${resolveBaseUrl()}${VISIT_RECORDINGS_PATH}`;
  let token = auth.accessToken;
  for (let attempt = 0; attempt < 2; attempt++) {
    let res: Response;
    try {
      res = await fetch(url, {
        method: 'POST',
        // multipart の boundary はブラウザに書かせる（Content-Type は付けない）。
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: buildForm(entry),
        cache: 'no-store',
      });
    } catch (err) {
      return { kind: 'keep', reason: err instanceof Error ? err.message : 'ネットワーク障害' };
    }
    // 202 の本文は `VisitRecordingRead`。id を持ち帰ると、保存した画面が
    // 「どれが今の録音か」を推測せずに紐付けられる（2026-09-18 是正）。
    if (res.ok) return { kind: 'sent', recordingId: recordingIdOf((await readBody(res)).text) };
    // `client_id` の重複 = 前回の送信が届いていた（レビュー H-4）。成功扱いで取り除く。
    // BE が既存行を返すならその id を使う（返さなければ null）。
    if (res.status === 409) {
      return { kind: 'sent', recordingId: recordingIdOf((await readBody(res)).text) };
    }
    if (res.status === 401 && attempt === 0 && auth.refreshToken) {
      const fresh = await refreshAccessToken(auth.refreshToken);
      if (fresh) {
        token = fresh;
        continue; // 新しいトークンで 1 度だけ再試行
      }
    }
    const body = await readBody(res);
    // 401 はセッションの問題 — 次のマウント（新しいトークン）で送れるので残す。
    if (res.status === 401 || res.status >= 500) {
      return { kind: 'keep', reason: body.detail ?? `サーバ応答 ${res.status}` };
    }
    // BE 不在 / 詰まり / Cloudflare — 確定回答ではないので残す（レビュー C-2）。
    if (isRetryableClientError(res.status, body)) {
      return { kind: 'keep', reason: body.detail ?? `サーバ応答 ${res.status}` };
    }
    return { kind: 'drop', reason: dropReason(res.status, body.detail) };
  }
  return { kind: 'keep', reason: '送信できませんでした' };
}

/** 実行中 / 予約済みの flush（staffId 毎に直列化）。 */
const inFlight = new Map<string, Promise<VoiceFlushResult>>();

interface LockManagerLike {
  request<T>(name: string, fn: () => Promise<T>): Promise<T>;
  request<T>(
    name: string,
    options: { ifAvailable?: boolean },
    fn: (lock: unknown) => Promise<T>,
  ): Promise<T>;
}

/**
 * タブ跨ぎの排他（レビュー H-4）。Web Locks があれば使い、無ければ
 * in-memory の直列化（{@link inFlight}）だけで従来どおり動く。
 *
 * `interactive`（＝利用者が「保存」を押した経路・レビュー L-A）では
 * `ifAvailable: true` で**取れなければロック無しで続行**する。別タブの自動 flush が
 * ロックを握っている間、保存ボタンが返ってこないほうが害が大きい。二重 POST は
 * `client_id` の 409 で BE 側が 1 件に畳む。
 */
function withVoiceLock<T>(staffId: string, fn: () => Promise<T>, interactive = false): Promise<T> {
  try {
    const locks = (navigator as unknown as { locks?: LockManagerLike }).locks;
    if (locks && typeof locks.request === 'function') {
      const name = `voice-flush:${staffId}`;
      if (interactive) {
        // lock が null（＝取れなかった）でもそのまま進む。
        return locks.request(name, { ifAvailable: true }, () => fn());
      }
      return locks.request(name, fn);
    }
  } catch {
    /* 非対応 / 権限なし — in-memory の直列化で運ぶ */
  }
  return fn();
}

async function runFlush(staffId: string, auth: VoiceAuth): Promise<VoiceFlushResult> {
  const entries = await listVoice(staffId);
  const dropped: DroppedVoice[] = [];
  const sentEntries: SentVoice[] = [];
  let sent = 0;
  for (const entry of entries) {
    const outcome = await postVoice(entry, auth);
    if (outcome.kind === 'sent') {
      await removeVoice(entry.id);
      sent += 1;
      sentEntries.push({ entryId: entry.id, recordingId: outcome.recordingId });
      continue;
    }
    const attempts = entry.attempts + 1;
    // 上限に達した「残す」は確定扱いにする（レビュー M-A）。捨てはしない。
    const giveUp = outcome.kind === 'keep' && attempts >= VOICE_MAX_ATTEMPTS;
    if (outcome.kind === 'drop' || giveUp) {
      const reason = giveUp
        ? `${outcome.reason}（${VOICE_MAX_ATTEMPTS} 回試しても送れませんでした）`
        : outcome.reason;
      // 捨てずに退避する（レビュー C-3）。画面から再送 / 保存 / 削除ができる。
      // 退避できなければ pending に残したまま = 案内もしない（レビュー L-B）。
      if (await moveToFailed(entry, reason)) dropped.push({ entry, reason });
      continue;
    }
    // 残す — 次回（マウント / online）に再試行。回数と理由は表示・調査用。
    try {
      await idbPut(PENDING_STORE, { ...entry, attempts, lastError: outcome.reason });
    } catch {
      // 試行回数を書けなくても entry 自体は残っている（次回また送る）。
    }
  }
  return {
    sent,
    remaining: await countVoice(staffId),
    dropped,
    failed: await countFailedVoice(staffId),
    sentEntries,
  };
}

export interface VoiceFlushOptions {
  /** 利用者が待っている経路（保存ボタン）。タブ跨ぎロックを待たない（L-A）。 */
  interactive?: boolean;
}

/** そのスタッフの未送信をすべて送る（同一 staffId の並行実行は直列化）。 */
export function flushVoiceQueue(
  staffId: string,
  auth: VoiceAuth,
  options: VoiceFlushOptions = {},
): Promise<VoiceFlushResult> {
  if (typeof window === 'undefined' || !staffId) {
    return Promise.resolve({ sent: 0, remaining: 0, dropped: [], failed: 0, sentEntries: [] });
  }
  const prev = inFlight.get(staffId);
  const next = (prev ? prev.then(noop, noop) : Promise.resolve()).then(() =>
    withVoiceLock(staffId, () => runFlush(staffId, auth), options.interactive),
  );
  inFlight.set(staffId, next);
  void next.then(
    () => release(staffId, next),
    () => release(staffId, next),
  );
  return next;
}

function noop(): void {
  /* 前の flush の成否は後発の実行可否に影響させない。 */
}

function release(staffId: string, settled: Promise<VoiceFlushResult>): void {
  if (inFlight.get(staffId) === settled) inFlight.delete(staffId);
}

export interface UseVoiceFlushResult {
  /** 未送信のまま残っている音声の件数（未送信バナーに合算する）。 */
  pendingCount: number;
  /** 送れなかった録音の件数（`voice-failed`・別バナー）。 */
  failedCount: number;
  /** いま送る（マウント時 / online 時は自動で走る）。 */
  flushNow: () => Promise<void>;
  /** 件数だけ数え直す（録音を積んだ直後・一覧を操作した直後の表示更新用）。 */
  refreshPending: () => Promise<void>;
}

/**
 * 未送信音声の再送フック。載せる画面は `/m/today` と `/m/today/{visitId}`
 * （打刻の `useCheckinFlush` と同じ置き方）。退避はここでトーストにする。
 */
export function useVoiceFlush(): UseVoiceFlushResult {
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? '';
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();
  const [pendingCount, setPendingCount] = useState(0);
  const [failedCount, setFailedCount] = useState(0);

  const refreshPending = useCallback(async () => {
    if (typeof window === 'undefined' || !staffId) return;
    setPendingCount(await countVoice(staffId));
    setFailedCount(await countFailedVoice(staffId));
  }, [staffId]);

  const flushNow = useCallback(async () => {
    if (typeof window === 'undefined' || !staffId) return;
    const { sent, remaining, dropped, failed } = await flushVoiceQueue(staffId, {
      accessToken,
      refreshToken,
    });
    setPendingCount(remaining);
    setFailedCount(failed);
    if (dropped.length > 0) {
      toast.error(`音声 ${dropped.length} 件を送信できませんでした`, {
        description: `${dropped[0]?.reason ?? '送信できないため'}・「送れなかった録音」から再送できます`,
      });
    }
    if (sent > 0) {
      void qc.invalidateQueries({ queryKey: ['visit-recordings'] });
    }
  }, [staffId, accessToken, refreshToken, qc]);

  useEffect(() => {
    if (typeof window === 'undefined' || !staffId) return;
    void refreshPending();
    void flushNow();
    const onOnline = () => void flushNow();
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, [staffId, flushNow, refreshPending]);

  return { pendingCount, failedCount, flushNow, refreshPending };
}
