/**
 * 音声記録が使う IndexedDB の最小ラッパ（設計 §10-5）。
 *
 * localStorage を使わないのは Blob を置けないため。録音チャンク（`voice-chunks`）と
 * 未送信の録音（`voice-pending`）、送れなかった録音（`voice-failed`）はどれも Blob を
 * 持つので、同じ DB に 3 ストアで置き、ここだけが `indexedDB` に触れる。
 *
 * 割り切り:
 *   - **index は張らない**。件数は「1 訪問ぶんの録音チャンク」「未送信数件」で、
 *     `getAll()` してから JS で絞っても実測で問題にならない。index を持たない分、
 *     テストの IndexedDB 代用実装も小さく済む。
 *   - **読み出しは落ちない**。プライベートブラウズ / SSR では空配列を返し、
 *     呼び出し側はメモリ上の控えで動き続ける（録音自体は止めない）。
 *   - **書き込みは落ちる**（レビュー C-1）。`idbPut` だけは例外で、保存できたかを
 *     嘘にしない。`put` の `onsuccess` はトランザクション確定を意味しない
 *     （quota 超過はコミット時に abort する）ので、**`oncomplete` まで待って**
 *     resolve し、`onabort` / `onerror` は reject する。呼び出し側はこの失敗を
 *     受けて「端末に保存できませんでした」の出口へ分岐する。
 */

export const VOICE_DB_NAME = 'rakusuke-voice';
/** v2: `voice-failed`（4xx で送れなかった録音の退避先・レビュー C-3）を追加。 */
export const VOICE_DB_VERSION = 2;

/** 録音中のチャンク（`dataavailable` ごと）。キーは `${sessionId}:${index}`。 */
export const CHUNK_STORE = 'voice-chunks';
/** 未送信の録音（メタ + Blob）。キーは entry の `id`。 */
export const PENDING_STORE = 'voice-pending';
/** 送れなかった録音（4xx）。キューから外すが**捨てない**置き場。 */
export const FAILED_STORE = 'voice-failed';

const ALL_STORES = [CHUNK_STORE, PENDING_STORE, FAILED_STORE] as const;

function idbAvailable(): boolean {
  return typeof window !== 'undefined' && typeof window.indexedDB !== 'undefined';
}

/** IDBRequest を Promise 化する（読み出し用）。 */
function req<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('IndexedDB request failed'));
  });
}

function asError(value: unknown, fallback: string): Error {
  if (value instanceof Error) return value;
  if (value && typeof value === 'object' && typeof (value as Error).message === 'string') {
    return new Error((value as Error).message);
  }
  return new Error(fallback);
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

/**
 * DB を開く（失敗・非対応は null）。同時呼び出しは 1 本に畳む。
 *
 * PWA は同じ端末で複数タブ / 複数ウィンドウが開く（レビュー H-A）:
 *   - `onblocked` … 別タブが古いバージョンを掴んだままで開けない。**待たずに
 *     null を返す**（`idbPut` が throw → C-1 の「端末に保存できませんでした」へ
 *     分岐する）。ここで宙吊りにすると保存ボタンが永久に返ってこない。
 *   - `onversionchange` … 別タブが新しいバージョンへ上げようとしている。掴んだまま
 *     だと向こうが `onblocked` で止まるので、こちらから閉じて記憶も捨てる
 *     （次の呼び出しで開き直す）。
 */
export function openVoiceDb(): Promise<IDBDatabase | null> {
  if (!idbAvailable()) return Promise.resolve(null);
  if (dbPromise) return dbPromise;
  dbPromise = new Promise<IDBDatabase | null>((resolve) => {
    let open: IDBOpenDBRequest;
    try {
      open = window.indexedDB.open(VOICE_DB_NAME, VOICE_DB_VERSION);
    } catch {
      resolve(null);
      return;
    }
    open.onupgradeneeded = () => {
      const db = open.result;
      for (const store of ALL_STORES) {
        if (!db.objectStoreNames.contains(store)) {
          db.createObjectStore(store, { keyPath: store === CHUNK_STORE ? 'key' : 'id' });
        }
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
    // 別タブが掴んだままで開けない — 待たずに諦める（呼び出し側が出口を出す）。
    open.onblocked = () => resolve(null);
  });
  return dbPromise;
}

/** テスト用: 開いた DB の記憶を捨てる（`indexedDB` を差し替えた後に呼ぶ）。 */
export function resetVoiceDbForTest(): void {
  dbPromise = null;
}

/**
 * 1 件 put（上書き）。**保存できなければ throw する**（レビュー C-1）。
 *
 * quota 超過やプライベートブラウズを黙って握りつぶすと、「保存しました」と言った
 * 直後に音声が消える。トランザクションが `complete` するまで成功と言わない。
 */
export async function idbPut(store: string, value: unknown): Promise<void> {
  const db = await openVoiceDb();
  if (!db) throw new Error('この端末では音声を保存できません');
  await new Promise<void>((resolve, reject) => {
    let tx: IDBTransaction;
    try {
      tx = db.transaction([store], 'readwrite');
    } catch (err) {
      reject(asError(err, 'IndexedDB transaction failed'));
      return;
    }
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(asError(tx.error, '音声を端末に保存できませんでした'));
    tx.onerror = () => reject(asError(tx.error, '音声を端末に保存できませんでした'));
    try {
      tx.objectStore(store).put(value as never);
    } catch (err) {
      reject(asError(err, '音声を端末に保存できませんでした'));
    }
  });
}

/** ストア全件。失敗・非対応は空配列。 */
export async function idbGetAll<T>(store: string): Promise<T[]> {
  const db = await openVoiceDb();
  if (!db) return [];
  try {
    const rows = await req(db.transaction([store], 'readonly').objectStore(store).getAll());
    return (rows ?? []) as T[];
  } catch {
    return [];
  }
}

/** キー指定で 1 件削除。 */
export async function idbDelete(store: string, key: IDBValidKey): Promise<void> {
  const db = await openVoiceDb();
  if (!db) return;
  try {
    await req(db.transaction([store], 'readwrite').objectStore(store).delete(key));
  } catch {
    /* ignore */
  }
}

/**
 * 複数キーを **1 トランザクションで** 削除する（レビュー N-2）。
 *
 * 録音チャンクは 10 秒に 1 件 = 60 分で 360 件になる。1 件ずつ `idbDelete` を
 * 回すとトランザクションを 360 回開くことになり、途中でタブが落ちれば削除が
 * 半端なまま残る。まとめて開けば「消えるか、残るか」のどちらかになる。
 */
export async function idbDeleteMany(store: string, keys: IDBValidKey[]): Promise<void> {
  if (keys.length === 0) return;
  const db = await openVoiceDb();
  if (!db) return;
  try {
    const objectStore = db.transaction([store], 'readwrite').objectStore(store);
    await Promise.all(keys.map((key) => req(objectStore.delete(key))));
  } catch {
    /* ignore */
  }
}
