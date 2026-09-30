/**
 * 実績の時刻を合わせる API (設計 2026-09-30 §6-1) — リクエストの形を縛る。
 *
 * バックエンドと突き合わせる契約:
 *   PUT    /api/v1/visits/{id}/actual-time          body { kind, time }
 *          (reason_code / reason_text は API の受け口として任意のまま。画面からは送らない)
 *   DELETE /api/v1/visits/{id}/actual-time?kind=…   (読取時刻に戻す)
 *   どちらもヘッダ `X-Client-Surface: mobile`・応答は VisitRead・成功後は ['me'] を無効化。
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

import { useAdjustActualTime, useResetActualTime } from '../me';

let qc: QueryClient;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const VISIT = {
  id: 'visit-1',
  actual_arrival_at: '2026-09-30T03:56:00Z',
  actual_arrival_read_at: '2026-09-30T04:06:20Z',
  actual_arrival_adjusted: true,
};

type Init = {
  method?: string;
  body?: string;
  headers?: Record<string, string>;
  accessToken?: string | null;
  refreshToken?: string | null;
};

beforeEach(() => {
  vi.clearAllMocks();
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  (useSession as Mock).mockReturnValue({
    data: { user: { staffId: 'staff-1' }, accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  });
});

describe('useAdjustActualTime', () => {
  it('PUT /visits/{id}/actual-time に kind / time を送り (理由は無し)、X-Client-Surface: mobile を付ける', async () => {
    (fetcher as Mock).mockResolvedValueOnce(VISIT);
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useAdjustActualTime('visit-1'), { wrapper });

    const updated = await result.current.mutateAsync({ kind: 'arrival', time: '12:56' });

    expect(updated).toEqual(VISIT);
    expect((fetcher as Mock).mock.calls).toHaveLength(1);
    const [path, init] = (fetcher as Mock).mock.calls[0] as [string, Init];
    expect(path).toBe('/api/v1/visits/visit-1/actual-time');
    expect(init.method).toBe('PUT');
    expect(init.headers).toEqual({ 'X-Client-Surface': 'mobile' });
    expect(JSON.parse(init.body ?? '')).toEqual({ kind: 'arrival', time: '12:56' });
    expect(init.accessToken).toBe('a');
    expect(init.refreshToken).toBe('r');
    // 一覧・詳細を取り直す (既存の打刻と同じ)。
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['me'] }));
  });

  it('失敗 (422 など) はそのまま投げ、無効化しない', async () => {
    (fetcher as Mock).mockRejectedValueOnce(new Error('422'));
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useAdjustActualTime('visit-1'), { wrapper });
    await expect(result.current.mutateAsync({ kind: 'departure', time: '13:41' })).rejects.toThrow(
      '422',
    );
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe('useResetActualTime', () => {
  it('DELETE /visits/{id}/actual-time?kind= で読取時刻に戻す (body なし)', async () => {
    (fetcher as Mock).mockResolvedValueOnce({ ...VISIT, actual_arrival_adjusted: false });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const { result } = renderHook(() => useResetActualTime('visit-1'), { wrapper });

    await result.current.mutateAsync('arrival');

    const [path, init] = (fetcher as Mock).mock.calls[0] as [string, Init];
    expect(path).toBe('/api/v1/visits/visit-1/actual-time?kind=arrival');
    expect(init.method).toBe('DELETE');
    expect(init.headers).toEqual({ 'X-Client-Surface': 'mobile' });
    expect(init.body).toBeUndefined();
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['me'] }));
  });

  it('退出も同じ形', async () => {
    (fetcher as Mock).mockResolvedValueOnce(VISIT);
    const { result } = renderHook(() => useResetActualTime('visit-1'), { wrapper });
    await result.current.mutateAsync('departure');
    expect((fetcher as Mock).mock.calls[0]?.[0]).toBe(
      '/api/v1/visits/visit-1/actual-time?kind=departure',
    );
  });
});
