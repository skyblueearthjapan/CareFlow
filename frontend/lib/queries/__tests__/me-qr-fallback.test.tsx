/**
 * useMyVisit の担当外フォールバック / useAdhocCheckin — QR打刻の開放 (Phase C)。
 *
 * カバー:
 *   1. 通常の GET が通れば `?qr_token=` は付けない (既存挙動を変えない)
 *   2. 担当外 = 404 かつ qr_token 所持 → `?qr_token=` 付きで取り直す (設計 §4-2)
 *   3. qr_token が無ければ 404 はそのままエラー (秘匿は維持)
 *   4. 404 以外 (403/500) はフォールバックしない
 *   5. useAdhocCheckin は POST /api/v1/visits/adhoc-checkin へ qr_token 付きで送る
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { fetcher } from '@/lib/api/fetcher';
import { ApiError } from '@/lib/api-client';

import { useAdhocCheckin, useMyVisit } from '../me';

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const VISIT = { id: 'visit-1', patient_name: '田中 太郎' };

beforeEach(() => {
  vi.clearAllMocks();
  (useSession as Mock).mockReturnValue({
    data: { user: { staffId: 'staff-1' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  });
});

describe('useMyVisit — 担当外フォールバック', () => {
  it('通常の GET はトークンを一切送らない', async () => {
    (fetcher as Mock).mockResolvedValueOnce(VISIT);
    const { result } = renderHook(() => useMyVisit('visit-1', 'TOK123'), { wrapper });
    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect((fetcher as Mock).mock.calls).toHaveLength(1);
    const [path, init] = (fetcher as Mock).mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe('/api/v1/visits/visit-1');
    expect(init.headers).toBeUndefined();
  });

  it('404 + qr_token 所持なら X-QR-Token ヘッダで取り直す (URL には残さない)', async () => {
    (fetcher as Mock)
      .mockRejectedValueOnce(new ApiError('not found', 404, null))
      .mockResolvedValueOnce(VISIT);
    const { result } = renderHook(() => useMyVisit('visit-1', 'TOK123'), { wrapper });
    await waitFor(() => expect(result.current.data).toBeTruthy());
    const [path, init] = (fetcher as Mock).mock.calls[1] as [
      string,
      { headers?: Record<string, string> },
    ];
    // トークンはヘッダのみ。クエリに載せるとアクセスログ/Referer/履歴に残る。
    expect(path).toBe('/api/v1/visits/visit-1');
    expect(path).not.toContain('qr_token');
    expect(init.headers).toEqual({ 'X-QR-Token': 'TOK123' });
  });

  it('qr_token が無ければ 404 のままエラー (担当外の秘匿を維持)', async () => {
    (fetcher as Mock).mockRejectedValueOnce(new ApiError('not found', 404, null));
    const { result } = renderHook(() => useMyVisit('visit-1'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((fetcher as Mock).mock.calls).toHaveLength(1);
  });

  it('404 以外 (500) はフォールバックしない', async () => {
    (fetcher as Mock).mockRejectedValueOnce(new ApiError('boom', 500, null));
    const { result } = renderHook(() => useMyVisit('visit-1', 'TOK123'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((fetcher as Mock).mock.calls).toHaveLength(1);
  });
});

describe('useAdhocCheckin', () => {
  it('POST /api/v1/visits/adhoc-checkin へ qr_token 付きで送る', async () => {
    (fetcher as Mock).mockResolvedValueOnce({ id: 'visit-adhoc-1' });
    const { result } = renderHook(() => useAdhocCheckin(), { wrapper });
    const visit = await result.current.mutateAsync({
      qr_token: 'TOK123',
      at: '2026-08-16T01:00:00Z',
      lat: 35.1,
      lng: 140.1,
    });
    expect(visit.id).toBe('visit-adhoc-1');
    const [path, init] = (fetcher as Mock).mock.calls[0] as [
      string,
      { method: string; body: string },
    ];
    expect(path).toBe('/api/v1/visits/adhoc-checkin');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toMatchObject({ qr_token: 'TOK123', lat: 35.1 });
  });
});
