/**
 * 未送信打刻の再送 (`checkin-flush`) — 送信先ルーティングと 4xx 破棄の検証.
 *
 *   1. arrival / departure / no_show は `/visits/{id}/{checkin|checkout|no-show}`
 *   2. adhoc_arrival (予定外) は `/visits/adhoc-checkin` (visit_id を使わない)
 *   3. 4xx は理由付きで破棄 / ネットワーク障害・5xx は保持
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

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
