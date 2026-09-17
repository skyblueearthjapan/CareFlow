/**
 * IndexedDB ラッパ (`lib/voice/idb.ts`) のテスト — 複数タブの扱い（レビュー H-A）。
 *
 * PWA は同じ端末で何枚も開かれる。守りたいのは 2 点だけ:
 *   - `onblocked`（別タブが古い DB を掴んだまま）で**宙吊りにしない**。null を
 *     返し、`idbPut` は throw して C-1 の「端末に保存できませんでした」へ流す。
 *   - `onversionchange`（別タブが新しい版へ上げたい）で**こちらから閉じる**。
 *     掴んだままだと向こうが `onblocked` で止まり、両方が保存できなくなる。
 */
import { describe, it, expect, beforeEach } from 'vitest';

import { installFakeIndexedDB } from './fakeIdb';
import {
  idbGetAll,
  idbPut,
  openVoiceDb,
  PENDING_STORE,
  resetVoiceDbForTest,
} from '@/lib/voice/idb';

beforeEach(() => {
  resetVoiceDbForTest();
});

describe('openVoiceDb（複数タブ・H-A）', () => {
  it('blocked（別タブが掴んだまま）は待たずに null を返す', async () => {
    installFakeIndexedDB({ blocked: true });

    expect(await openVoiceDb()).toBeNull();
    // 読み出しは落ちない（空配列）が、書き込みは嘘をつかずに throw する。
    expect(await idbGetAll(PENDING_STORE)).toEqual([]);
    await expect(idbPut(PENDING_STORE, { id: 'x' })).rejects.toThrow(
      'この端末では音声を保存できません',
    );
  });

  it('versionchange で自分の DB を閉じ、次の呼び出しで開き直す', async () => {
    const fake = installFakeIndexedDB();
    const db = await openVoiceDb();
    expect(db).not.toBeNull();

    // 別タブがバージョンを上げようとした。
    fake.db.onversionchange?.();

    expect(fake.state.closed).toBe(1);
    // 記憶も捨てているので、次は開き直して使える（閉じた DB を掴み続けない）。
    await idbPut(PENDING_STORE, { id: 'after-reopen' });
    expect(await idbGetAll(PENDING_STORE)).toHaveLength(1);
  });
});
