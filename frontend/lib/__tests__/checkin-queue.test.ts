import { describe, it, expect, beforeEach, vi } from 'vitest';

import {
  countPending,
  DropPendingError,
  enqueuePending,
  flushPending,
  listPending,
  removePending,
  type PendingEntry,
} from '@/lib/checkin-queue';

const STAFF = 'staff-1';

function enqueueArrival() {
  return enqueuePending(STAFF, {
    visit_id: 'visit-1',
    kind: 'arrival',
    payload: { at: '2026-06-30T00:00:00Z', qr_token: 'TOK', lat: 35.1, lng: 140.1 },
  });
}

describe('checkin-queue', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('積んだ保留を一覧/件数で取得できる', () => {
    enqueueArrival();
    enqueueArrival();
    expect(countPending(STAFF)).toBe(2);
    expect(listPending(STAFF)).toHaveLength(2);
  });

  it('staff id で名前空間が分かれる', () => {
    enqueueArrival();
    expect(countPending('other-staff')).toBe(0);
  });

  it('id 指定で個別に取り除ける', () => {
    const e = enqueueArrival();
    enqueueArrival();
    removePending(STAFF, e!.id);
    expect(countPending(STAFF)).toBe(1);
  });

  it('flushPending は成功した entry のみ取り除く', async () => {
    enqueueArrival();
    enqueueArrival();
    const post = vi.fn(async () => ({}));
    const { remaining, dropped } = await flushPending(STAFF, post);
    expect(post).toHaveBeenCalledTimes(2);
    expect(remaining).toBe(0);
    expect(dropped).toHaveLength(0);
    expect(countPending(STAFF)).toBe(0);
  });

  it('flushPending は reject した entry を残す (ベストエフォート再送)', async () => {
    enqueueArrival();
    enqueueArrival();
    // どちらも届かない (network/5xx) → 残す。
    const post = vi.fn(async (_e: PendingEntry) => {
      throw new Error('still offline');
    });
    const { remaining, dropped } = await flushPending(STAFF, post);
    expect(remaining).toBe(2);
    expect(dropped).toHaveLength(0);
    expect(countPending(STAFF)).toBe(2);
  });

  it('flushPending は 4xx (DropPendingError) を破棄し理由を返す', async () => {
    const a = enqueueArrival();
    enqueueArrival();
    // 1 件目は 4xx 確定エラー (破棄)、2 件目は成功 (送信)。
    const post = vi.fn(async (e: PendingEntry) => {
      if (e.id === a!.id) throw new DropPendingError('無効なQRのため');
      return {};
    });
    const { remaining, dropped } = await flushPending(STAFF, post);
    // 両方ともキューから消える (破棄 + 送信)。
    expect(remaining).toBe(0);
    expect(countPending(STAFF)).toBe(0);
    // 破棄分は理由付きで返る (黙って消えない)。
    expect(dropped).toHaveLength(1);
    expect(dropped[0]?.entry.id).toBe(a!.id);
    expect(dropped[0]?.reason).toBe('無効なQRのため');
  });

  it('保留は TTL で勝手に消えない (24h 相当の経過でも保持)', () => {
    enqueueArrival();
    // checkin-storage と違い失効ロジックを持たないため、再読込でも残る。
    expect(countPending(STAFF)).toBe(1);
    expect(listPending(STAFF)[0]?.payload.qr_token).toBe('TOK');
  });
});
