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

/** 予定外訪問の到着 (visit 未生成・qr_token が患者特定の唯一の鍵)。 */
function enqueueAdhoc() {
  return enqueuePending(STAFF, {
    visit_id: '',
    kind: 'adhoc_arrival',
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

  it('予定外の到着 (adhoc_arrival) は visit_id 無しで積める', () => {
    const e = enqueueAdhoc();
    expect(e?.kind).toBe('adhoc_arrival');
    expect(e?.visit_id).toBe('');
    // 再読込 (localStorage 経由) でも型検証を通って残る。
    const listed = listPending(STAFF);
    expect(listed).toHaveLength(1);
    expect(listed[0]?.kind).toBe('adhoc_arrival');
    expect(listed[0]?.payload.qr_token).toBe('TOK');
  });

  it('qr_token 欠けの adhoc_arrival は読み込み時に捨てる (再送不能なため)', () => {
    window.localStorage.setItem(
      'checkin-pending:staff-1',
      JSON.stringify([
        { id: 'x', visit_id: '', kind: 'adhoc_arrival', payload: { at: 'now' }, queued_at: 1 },
      ]),
    );
    expect(listPending(STAFF)).toHaveLength(0);
  });

  it('flushPending は adhoc_arrival も通常 entry と同じく送信・除去する', async () => {
    enqueueAdhoc();
    enqueueArrival();
    const post = vi.fn(async () => ({}));
    const { remaining, dropped } = await flushPending(STAFF, post);
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls.map((c) => (c[0] as PendingEntry).kind)).toEqual([
      'adhoc_arrival',
      'arrival',
    ]);
    expect(remaining).toBe(0);
    expect(dropped).toHaveLength(0);
  });

  it('保留は TTL で勝手に消えない (24h 相当の経過でも保持)', () => {
    enqueueArrival();
    // checkin-storage と違い失効ロジックを持たないため、再読込でも残る。
    expect(countPending(STAFF)).toBe(1);
    expect(listPending(STAFF)[0]?.payload.qr_token).toBe('TOK');
  });
});
