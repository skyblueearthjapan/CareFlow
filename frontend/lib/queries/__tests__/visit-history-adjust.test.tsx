/**
 * 実績の時刻を合わせる mutation（PC）の vitest
 * （契約 = `docs/plans/actual-time-adjust-design-2026-09-30.md` §6-1・§8-3）。
 *
 * 縛る挙動:
 *   1. PUT / DELETE の URL・メソッド・`X-Client-Surface: pc`・body の項目名
 *   2. 成功後に打刻履歴・訪問モニター・訪問・スマホの自分の訪問を失効する
 *   3. 失敗したら失効しない
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { mockFetcher } = vi.hoisted(() => ({ mockFetcher: vi.fn() }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...a: unknown[]) => mockFetcher(...a) }));

import {
  useAdjustVisitActualTime,
  useResetVisitActualTime,
  useVisitHistory,
} from '../visit-history';

const VISIT_ID = '11111111-1111-4111-8111-111111111111';

let qc: QueryClient;
let invalidateSpy: ReturnType<typeof vi.fn>;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function invalidatedKeys(): unknown[][] {
  return invalidateSpy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey);
}

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  invalidateSpy = vi.fn();
  qc.invalidateQueries = invalidateSpy as unknown as QueryClient['invalidateQueries'];
  mockFetcher.mockReset();
  mockFetcher.mockResolvedValue({});
});

describe('useAdjustVisitActualTime', () => {
  it('PUT /visits/{id}/actual-time を X-Client-Surface: pc と契約の項目名で呼ぶ', async () => {
    const { result } = renderHook(() => useAdjustVisitActualTime(), { wrapper });
    result.current.mutate({
      visitId: VISIT_ID,
      kind: 'arrival',
      time: '12:56',
      reasonCode: 'intercom_wait',
      reasonText: '  ',
    });
    await waitFor(() => expect(mockFetcher).toHaveBeenCalled());

    const [path, init] = mockFetcher.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe(`/api/v1/visits/${VISIT_ID}/actual-time`);
    expect(init.method).toBe('PUT');
    expect(init.headers).toEqual({ 'X-Client-Surface': 'pc' });
    expect(init.accessToken).toBe('tok');
    expect(JSON.parse(init.body as string)).toEqual({
      kind: 'arrival',
      time: '12:56',
      reason_code: 'intercom_wait',
      reason_text: null,
    });
  });

  it('自由記述は前後の空白を落として送る', async () => {
    const { result } = renderHook(() => useAdjustVisitActualTime(), { wrapper });
    result.current.mutate({
      visitId: VISIT_ID,
      kind: 'departure',
      time: '13:35',
      reasonCode: 'other',
      reasonText: ' 家族と話していた ',
    });
    await waitFor(() => expect(mockFetcher).toHaveBeenCalled());
    const init = mockFetcher.mock.calls[0]?.[1] as { body: string };
    expect(JSON.parse(init.body)).toEqual({
      kind: 'departure',
      time: '13:35',
      reason_code: 'other',
      reason_text: '家族と話していた',
    });
  });

  it('成功後に打刻履歴・モニター・訪問・自分の訪問を失効する', async () => {
    const { result } = renderHook(() => useAdjustVisitActualTime(), { wrapper });
    result.current.mutate({ visitId: VISIT_ID, kind: 'arrival', time: '12:56' });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
    expect(invalidatedKeys()).toEqual([['visit-history'], ['monitor'], ['visits'], ['me']]);
  });

  it('失敗したら失効しない', async () => {
    mockFetcher.mockRejectedValue(new Error('422'));
    const { result } = renderHook(() => useAdjustVisitActualTime(), { wrapper });
    result.current.mutate({ visitId: VISIT_ID, kind: 'arrival', time: '12:56' });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});

describe('useResetVisitActualTime', () => {
  it('DELETE /visits/{id}/actual-time?kind= を X-Client-Surface: pc で呼び、同じ対象を失効する', async () => {
    const { result } = renderHook(() => useResetVisitActualTime(), { wrapper });
    result.current.mutate({ visitId: VISIT_ID, kind: 'departure' });
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());

    const [path, init] = mockFetcher.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe(`/api/v1/visits/${VISIT_ID}/actual-time?kind=departure`);
    expect(init.method).toBe('DELETE');
    expect(init.headers).toEqual({ 'X-Client-Surface': 'pc' });
    expect(init.body).toBeUndefined();
    expect(invalidatedKeys()).toEqual([['visit-history'], ['monitor'], ['visits'], ['me']]);
  });
});

/**
 * レビュー L-12: ページ送り・絞り込みのたびにクエリキーが変わる。前の結果を持たないと
 * `data` が一瞬 undefined になり、集計帯・件数・ページ送りが消えて画面が跳ねる。
 */
describe('useVisitHistory — 条件が変わっても前の結果を保つ', () => {
  const RANGE = { from: '2026-09-01', to: '2026-09-30' };

  it('次の結果が届くまで前の結果を出し続け、届いたら差し替える', async () => {
    mockFetcher.mockResolvedValueOnce({ items: [], total: 120, summary: { visits: 120 } });
    const { result, rerender } = renderHook(
      ({ offset }: { offset: number }) => useVisitHistory({ ...RANGE, offset }),
      { wrapper, initialProps: { offset: 0 } },
    );
    await waitFor(() => expect(result.current.data?.total).toBe(120));
    expect(result.current.isPlaceholderData).toBe(false);

    // 2 ページ目へ。応答はまだ来ない。
    let deliver!: (value: unknown) => void;
    mockFetcher.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    );
    rerender({ offset: 50 });
    expect(result.current.data?.total).toBe(120);
    expect(result.current.data?.summary.visits).toBe(120);
    expect(result.current.isPlaceholderData).toBe(true);
    expect(result.current.isLoading).toBe(false);

    deliver({ items: [], total: 121, summary: { visits: 121 } });
    await waitFor(() => expect(result.current.isPlaceholderData).toBe(false));
    expect(result.current.data?.total).toBe(121);
  });
});
