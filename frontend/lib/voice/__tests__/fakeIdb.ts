/**
 * テスト用の最小 IndexedDB 代用（`lib/voice/idb.ts` が触る API だけ）。
 *
 * `fake-indexeddb` は依存に無い。ここで要るのは 3 点だけ:
 *   - `open` → `onupgradeneeded` → `onsuccess`
 *   - `transaction` が **`oncomplete` / `onabort` を持つ**こと
 *     （`idbPut` が put の onsuccess ではなく確定を待つのを縛るため）
 *   - `failWrites` で quota 超過と同じ `onabort` を起こせること
 *
 * ファイル名が `*.test.ts` ではないので vitest には収集されない。
 */

interface FakeRequest<T> {
  result?: T;
  error?: unknown;
  onsuccess?: () => void;
  onerror?: () => void;
  onblocked?: () => void;
  onupgradeneeded?: () => void;
}

/** `openVoiceDb` が触る DB 側の口（`close` / `onversionchange`・レビュー H-A）。 */
interface FakeDb {
  onversionchange?: () => void;
  close: () => void;
}

export interface FakeIdb {
  /** ストア名 → キー → 値。テストから中身を直接覗ける。 */
  stores: Map<string, Map<string, unknown>>;
  /** 書き込みを quota 超過として abort させるスイッチ。 */
  state: { failWrites: boolean; closed: number };
  /** 開かれた DB（`onversionchange` を撃つため）。 */
  db: FakeDb;
}

export interface FakeIdbOptions {
  /** `open` を `onblocked` で返す（別タブが古い DB を掴んだまま）。 */
  blocked?: boolean;
}

export function installFakeIndexedDB(options: FakeIdbOptions = {}): FakeIdb {
  const stores = new Map<string, Map<string, unknown>>();
  const state = { failWrites: false, closed: 0 };

  function request<T>(exec: () => T): FakeRequest<T> {
    const req: FakeRequest<T> = {};
    queueMicrotask(() => {
      try {
        req.result = exec();
        req.onsuccess?.();
      } catch (err) {
        req.error = err;
        req.onerror?.();
      }
    });
    return req;
  }

  const db: FakeDb & Record<string, unknown> = {
    close: () => {
      state.closed += 1;
    },
    objectStoreNames: { contains: (name: string) => stores.has(name) },
    createObjectStore: (name: string) => {
      stores.set(name, new Map());
      return {};
    },
    transaction: (names: string[]) => {
      const tx: {
        error: unknown;
        oncomplete?: () => void;
        onabort?: () => void;
        onerror?: () => void;
        objectStore: (name: string) => unknown;
      } = { error: null, objectStore: () => ({}) };
      tx.objectStore = (name: string) => {
        const store = stores.get(name) ?? new Map<string, unknown>();
        stores.set(name, store);
        void names;
        return {
          put: (value: Record<string, unknown>) => {
            queueMicrotask(() => {
              if (state.failWrites) {
                // quota 超過はコミット時に abort する（put 自体は成功して見える）。
                tx.error = new Error('QuotaExceededError');
                tx.onabort?.();
                return;
              }
              store.set(String(value.id ?? value.key), value);
              tx.oncomplete?.();
            });
            return {};
          },
          getAll: () => request(() => Array.from(store.values())),
          delete: (key: IDBValidKey) =>
            request(() => {
              store.delete(String(key));
              return undefined;
            }),
        };
      };
      return tx;
    },
  };

  Object.defineProperty(window, 'indexedDB', {
    configurable: true,
    value: {
      open: () => {
        const req: FakeRequest<typeof db> = {};
        queueMicrotask(() => {
          if (options.blocked) {
            // 別タブが古いバージョンを掴んだまま — onsuccess は永久に来ない。
            req.onblocked?.();
            return;
          }
          req.result = db;
          req.onupgradeneeded?.();
          req.onsuccess?.();
        });
        return req;
      },
    },
  });
  return { stores, state, db };
}
