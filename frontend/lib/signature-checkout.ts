/**
 * サインで記録 — 退出の送信と、圏外のときのサインの画像の置き場
 * (設計 `docs/plans/signature-checkin-design-2026-10-06.md` §4・§5-1)。
 *
 * 退出は `POST /api/v1/visits/{id}/checkout-signature` (multipart: `image` + 位置・時刻・
 * `client_id`)。`client_id` は 1 回の記録につき 1 個の UUID で、同じ値の再送はサーバが
 * 1 件に畳む (圏外で退避した記録の再送が重なっても二重にならない)。
 *
 * 圏外のとき: 時刻・位置は今の未送信キュー (`checkin-queue.ts`・localStorage) に
 * `kind: 'departure_signature'` で積み、**画像は IndexedDB** に `client_id` をキーに置く。
 * IndexedDB に置けない端末では、画像を data URL にしてキューの控え
 * (`payload.image_data_url`) に入れる (PO 決定 2026-10-07: QRなしの退出に落とさない)。
 * 再送 (`checkin-flush.ts`) が画像を読み出して、サインとして送る。
 *
 * IndexedDB は音声記録 (`lib/voice/idb.ts`) とは別の DB にする (版の上げ下げを
 * 互いに縛らない)。読み出しは落ちない (null)・書き込みは落ちる (保存できたかを嘘に
 * しない・音声と同じ割り切り)。
 */
import { ApiError } from '@/lib/api-client';

export const SIGNATURE_DB_NAME = 'rakusuke-signature';
export const SIGNATURE_DB_VERSION = 1;
/** 未送信のサインの画像。キーは `id` (= `client_id`)。 */
export const SIGNATURE_STORE = 'signature-images';

/** 退出の送信に載せる時刻・位置 (`CheckInPayload` と同じ意味)。 */
export interface SignatureCheckoutPayload {
  lat?: number;
  lng?: number;
  accuracy?: number;
  /** 「サインして退出を記録」を押した瞬間 (ISO 8601)。 */
  at: string;
  reason?: string;
  is_override?: boolean;
  /** 再送の冪等キー (UUID)。 */
  client_id: string;
}

export function signatureCheckoutPath(visitId: string): string {
  return `/api/v1/visits/${visitId}/checkout-signature`;
}

/** サインの画像の取り出し (Bearer 必須・見るたびにサーバの監査ログに残る)。 */
export function signatureImagePath(signatureId: string): string {
  return `/api/v1/visit-signatures/${signatureId}/image`;
}

/** 1 回の記録の `client_id`。UUID を作れない端末では時刻と乱数で代える。 */
export function newSignatureClientId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    /* fall through */
  }
  // UUID の形にする (サーバは UUID として読む)。
  const hex = Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join(
    '',
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

export function buildSignatureForm(payload: SignatureCheckoutPayload, image: Blob): FormData {
  const form = new FormData();
  form.append('image', image, image.type === 'image/jpeg' ? 'signature.jpg' : 'signature.png');
  if (payload.lat != null) form.append('lat', String(payload.lat));
  if (payload.lng != null) form.append('lng', String(payload.lng));
  if (payload.accuracy != null) form.append('accuracy', String(payload.accuracy));
  form.append('at', payload.at);
  if (payload.reason) form.append('reason', payload.reason);
  if (payload.is_override) form.append('is_override', 'true');
  form.append('client_id', payload.client_id);
  return form;
}

async function refreshAccessToken(refreshToken: string): Promise<string | null> {
  try {
    const res = await fetch('/api/v1/auth/refresh', {
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

function parseBody(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * サインで退出を送る。応答は `VisitRead`。
 *
 * multipart の boundary はブラウザに書かせる (`fetcher` は Content-Type を JSON に
 * 決め打つので使わない)。失敗は `fetcher` と同じ形: ネットワーク障害は fetch の例外の
 * まま、応答のある失敗は `ApiError` (status と body)。`isServerUnreachable` /
 * `detailOf` がそのまま使える。401 はリフレッシュして 1 度だけもう一度送る。
 */
export async function postSignatureCheckout<T = unknown>(
  visitId: string,
  payload: SignatureCheckoutPayload,
  image: Blob,
  accessToken: string | null,
  refreshToken: string | null,
): Promise<T> {
  const path = signatureCheckoutPath(visitId);
  let token = accessToken;
  for (let attempt = 0; attempt < 2; attempt++) {
    const res = await fetch(path, {
      method: 'POST',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: buildSignatureForm(payload, image),
      cache: 'no-store',
    });
    if (res.status === 401 && attempt === 0 && refreshToken) {
      const fresh = await refreshAccessToken(refreshToken);
      if (fresh) {
        token = fresh;
        continue;
      }
    }
    const body = parseBody(await res.text());
    if (!res.ok) throw new ApiError(`API ${res.status} (${path})`, res.status, body);
    return body as T;
  }
  /* c8 ignore next */
  throw new ApiError(`API 401 (${path})`, 401, null);
}

// ---------------------------------------------------------------------------
// IndexedDB (圏外のときのサインの画像)
// ---------------------------------------------------------------------------

interface StoredSignature {
  id: string;
  blob: Blob;
  saved_at: number;
}

function idbAvailable(): boolean {
  return typeof window !== 'undefined' && typeof window.indexedDB !== 'undefined';
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

/**
 * DB を開く (失敗・非対応は null)。同時呼び出しは 1 本に畳む。
 *
 * **開けなかった結果 (null) は覚えない**: 別タブが古い版を掴んでいた・一時的に失敗した
 * だけのことがあるので、次の呼び出しでまた開きに行く (一度の失敗で、その画面を開いて
 * いる間ずっと画像を読めない = 退出の記録を捨てる、にしない)。
 */
function openSignatureDb(): Promise<IDBDatabase | null> {
  if (!idbAvailable()) return Promise.resolve(null);
  if (dbPromise) return dbPromise;
  const opening = new Promise<IDBDatabase | null>((resolve) => {
    let open: IDBOpenDBRequest;
    try {
      open = window.indexedDB.open(SIGNATURE_DB_NAME, SIGNATURE_DB_VERSION);
    } catch {
      resolve(null);
      return;
    }
    open.onupgradeneeded = () => {
      const db = open.result;
      if (!db.objectStoreNames.contains(SIGNATURE_STORE)) {
        db.createObjectStore(SIGNATURE_STORE, { keyPath: 'id' });
      }
    };
    open.onsuccess = () => {
      const db = open.result;
      db.onversionchange = () => {
        db.close();
        dbPromise = null;
      };
      resolve(db);
    };
    open.onerror = () => resolve(null);
    open.onblocked = () => resolve(null);
  });
  dbPromise = opening;
  void opening.then((db) => {
    if (db === null && dbPromise === opening) dbPromise = null;
  });
  return opening;
}

/** テスト用: 開いた DB の記憶を捨てる (`indexedDB` を差し替えた後に呼ぶ)。 */
export function resetSignatureDbForTest(): void {
  dbPromise = null;
}

/**
 * 未送信のサインの画像を端末に置く。**保存できなければ throw する** (トランザクションが
 * 確定するまで成功と言わない)。呼び出し側は失敗したら localStorage の控えに画像を
 * 入れる ({@link blobToDataUrl})。
 */
export async function saveSignatureImage(clientId: string, blob: Blob): Promise<void> {
  const db = await openSignatureDb();
  if (!db) throw new Error('この端末ではサインの画像を保存できません');
  await new Promise<void>((resolve, reject) => {
    let tx: IDBTransaction;
    try {
      tx = db.transaction([SIGNATURE_STORE], 'readwrite');
    } catch (err) {
      reject(err instanceof Error ? err : new Error('IndexedDB transaction failed'));
      return;
    }
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(new Error('サインの画像を端末に保存できませんでした'));
    tx.onerror = () => reject(new Error('サインの画像を端末に保存できませんでした'));
    try {
      const value: StoredSignature = { id: clientId, blob, saved_at: Date.now() };
      tx.objectStore(SIGNATURE_STORE).put(value);
    } catch (err) {
      reject(err instanceof Error ? err : new Error('サインの画像を端末に保存できませんでした'));
    }
  });
}

/**
 * 未送信のサインの画像を読む結果。
 *
 * - `{ ok: true, blob }` … 読めた。
 * - `{ ok: true, blob: null }` … DB は開けて、その画像は **本当に無い** (消えた)。
 * - `{ ok: false }` … DB を開けない・読めない (一時的なことがある)。**捨てない**。
 */
export type SignatureImageLoad = { ok: true; blob: Blob | null } | { ok: false };

export async function loadSignatureImage(clientId: string): Promise<SignatureImageLoad> {
  const db = await openSignatureDb();
  if (!db) return { ok: false };
  try {
    const row = await new Promise<StoredSignature | undefined>((resolve, reject) => {
      const request = db
        .transaction([SIGNATURE_STORE], 'readonly')
        .objectStore(SIGNATURE_STORE)
        .get(clientId);
      request.onsuccess = () => resolve(request.result as StoredSignature | undefined);
      request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'));
    });
    return { ok: true, blob: row?.blob ?? null };
  } catch {
    return { ok: false };
  }
}

/** 画像を data URL にする (IndexedDB に置けないとき、localStorage の控えに入れる)。 */
export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === 'string'
        ? resolve(reader.result)
        : reject(new Error('サインの画像を読めませんでした'));
    reader.onerror = () => reject(reader.error ?? new Error('サインの画像を読めませんでした'));
    reader.readAsDataURL(blob);
  });
}

/** data URL を画像に戻す (読めなければ null)。 */
export function dataUrlToBlob(dataUrl: string): Blob | null {
  const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(dataUrl);
  if (!match || !match[2]) return null;
  try {
    const binary = atob(match[3] ?? '');
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: match[1] || 'image/png' });
  } catch {
    return null;
  }
}

/** 送れた (または送れないと決まった) 画像を消す。 */
export async function deleteSignatureImage(clientId: string): Promise<void> {
  const db = await openSignatureDb();
  if (!db) return;
  try {
    await new Promise<void>((resolve, reject) => {
      const request = db
        .transaction([SIGNATURE_STORE], 'readwrite')
        .objectStore(SIGNATURE_STORE)
        .delete(clientId);
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'));
    });
  } catch {
    /* ignore */
  }
}
