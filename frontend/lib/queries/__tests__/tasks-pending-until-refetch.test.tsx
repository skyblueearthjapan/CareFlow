/**
 * やることの変更は、読み直し (['tasks'] の無効化) が終わるまで isPending を続ける。
 *
 * 欠陥: 無効化を待たずに mutation が終わり、ボタンがすぐ押せるようになるため、
 * 完了直後の二度押しで 409 と「ほかの人が先に操作した」が出ていた。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));
const fetcherMock = vi.fn();
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...a: unknown[]) => fetcherMock(...a) }));

import { useCompleteTask, useTasks } from '../tasks';

let qc: QueryClient;
let finishRefetch: () => void;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  fetcherMock.mockReset();
  qc.invalidateQueries = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finishRefetch = resolve;
      }),
  ) as unknown as QueryClient['invalidateQueries'];
});

describe('useCompleteTask', () => {
  it('成功しても読み直しが終わるまで isPending のまま', async () => {
    fetcherMock.mockResolvedValue({ id: 't1' });
    const { result } = renderHook(() => useCompleteTask(), { wrapper });
    act(() => result.current.mutate({ taskId: 't1', visitId: 'v1' }));
    await waitFor(() => expect(qc.invalidateQueries).toHaveBeenCalled());
    expect(result.current.isPending).toBe(true);
    await act(async () => finishRefetch());
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it('失敗 (409) のときも読み直しが終わるまで isPending のまま', async () => {
    fetcherMock.mockRejectedValue(new Error('API 409'));
    const { result } = renderHook(() => useCompleteTask(), { wrapper });
    act(() => result.current.mutate({ taskId: 't1' }));
    await waitFor(() => expect(qc.invalidateQueries).toHaveBeenCalled());
    expect(result.current.isPending).toBe(true);
    await act(async () => finishRefetch());
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe('useTasks', () => {
  it('doneFrom を done_from で送る', async () => {
    fetcherMock.mockResolvedValue([]);
    renderHook(() => useTasks({ status: 'done', doneFrom: '2026-10-02' }), { wrapper });
    await waitFor(() => expect(fetcherMock).toHaveBeenCalled());
    const url = String(fetcherMock.mock.calls[0]?.[0]);
    const qs = new URLSearchParams(url.split('?')[1]);
    expect(qs.get('status')).toBe('done');
    expect(qs.get('done_from')).toBe('2026-10-02');
    expect(qs.has('date_from')).toBe(false);
  });
});
