/**
 * 未送信打刻の再送 (`checkin-flush`) — 送信先ルーティングと 4xx 破棄の検証.
 *
 *   1. arrival / departure / no_show は `/visits/{id}/{checkin|checkout|no-show}`
 *   2. adhoc_arrival (予定外) は `/visits/adhoc-checkin` (visit_id を使わない)
 *   3. 4xx は理由付きで破棄 / ネットワーク障害・5xx は保持
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { waitFor } from '@testing-library/react';

import { ApiError } from '@/lib/api-client';
import { enqueuePending } from '@/lib/checkin-queue';

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

import { fetcher } from '@/lib/api/fetcher';
import { flushCheckinQueue } from '@/lib/checkin-flush';

const asMock = (fn: unknown) => fn as unknown as ReturnType<typeof vi.fn>;
const STAFF = 'staff-1';

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  asMock(fetcher).mockResolvedValue({});
});

describe('flushCheckinQueue — 送信先ルーティング', () => {
  it('通常の打刻は /visits/{id}/... へ再送する', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-1',
      kind: 'departure',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
    });
    const { remaining } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(asMock(fetcher).mock.calls[0][0]).toBe('/api/v1/visits/visit-1/checkout');
    expect(remaining).toBe(0);
  });

  it('予定外の到着は /visits/adhoc-checkin へ qr_token 付きで再送する', async () => {
    enqueuePending(STAFF, {
      visit_id: '',
      kind: 'adhoc_arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK', lat: 35.1, lng: 140.1 },
    });
    const { remaining, dropped } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(asMock(fetcher).mock.calls[0][0]).toBe('/api/v1/visits/adhoc-checkin');
    const body = JSON.parse((asMock(fetcher).mock.calls[0][1] as { body: string }).body) as Record<
      string,
      unknown
    >;
    expect(body).toMatchObject({ qr_token: 'TOK', lat: 35.1, lng: 140.1 });
    expect(remaining).toBe(0);
    expect(dropped).toHaveLength(0);
  });

  it('予定外の 410 (失効QR) は理由付きで破棄する', async () => {
    enqueuePending(STAFF, {
      visit_id: '',
      kind: 'adhoc_arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
    });
    asMock(fetcher).mockRejectedValueOnce(
      new ApiError('gone', 410, { detail: 'QRが失効しています' }),
    );
    const { remaining, dropped } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(remaining).toBe(0);
    expect(dropped).toHaveLength(1);
    expect(dropped[0]?.reason).toBe('QRが失効しています');
  });

  it('410 (再発行済QR) も理由付きで破棄する', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-1',
      kind: 'arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
    });
    asMock(fetcher).mockRejectedValueOnce(new ApiError('gone', 410, null));
    const { remaining, dropped } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(remaining).toBe(0);
    expect(dropped[0]?.reason).toBe('QRが再発行され無効になったため');
  });

  it('圏外 (fetch 失敗) の予定外はキューに残る', async () => {
    enqueuePending(STAFF, {
      visit_id: '',
      kind: 'adhoc_arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
    });
    asMock(fetcher).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const { remaining, dropped } = await flushCheckinQueue(STAFF, 'a', 'r');
    expect(remaining).toBe(1);
    expect(dropped).toHaveLength(0);
  });
});

describe('flushCheckinQueue — 並行実行の直列化', () => {
  it('同一スタッフの同時 flush は同じ entry を二重 POST しない', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-1',
      kind: 'arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
    });
    // 送信は「遅い」— 直列化が無ければ 2 本目が同じ entry を読んでしまう。
    let resolveFirst: (() => void) | null = null;
    asMock(fetcher).mockImplementationOnce(
      () =>
        new Promise<unknown>((resolve) => {
          resolveFirst = () => resolve({});
        }),
    );

    // 一覧 / 詳細 / /q が同時にマウントされた状況を再現する。
    const a = flushCheckinQueue(STAFF, 'a', 'r');
    const b = flushCheckinQueue(STAFF, 'a', 'r');
    await waitFor(() => expect(resolveFirst).not.toBeNull());
    resolveFirst!();

    const [ra, rb] = await Promise.all([a, b]);
    // POST は 1 回だけ (2 本目は完了後にキューを読み直し、空になっている)。
    expect(asMock(fetcher)).toHaveBeenCalledTimes(1);
    expect(ra.remaining).toBe(0);
    expect(rb.remaining).toBe(0);
    // 409 の誤検知が起きないので「送信できませんでした」も出ない。
    expect(ra.dropped).toHaveLength(0);
    expect(rb.dropped).toHaveLength(0);
  });

  it('スタッフが違えば並行に走る', async () => {
    enqueuePending(STAFF, {
      visit_id: 'visit-1',
      kind: 'arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK' },
    });
    enqueuePending('staff-2', {
      visit_id: 'visit-2',
      kind: 'arrival',
      payload: { at: '2026-08-16T01:00:00Z', qr_token: 'TOK2' },
    });
    await Promise.all([flushCheckinQueue(STAFF, 'a', 'r'), flushCheckinQueue('staff-2', 'a', 'r')]);
    expect(asMock(fetcher)).toHaveBeenCalledTimes(2);
  });
});
