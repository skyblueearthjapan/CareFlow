/**
 * 受け入れ枠 (厳密版) の読み込み — 「全て」は office_id を付けない・拠点は付ける。
 */
import * as React from 'react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

import { useSession } from 'next-auth/react';
import { fetcher } from '@/lib/api/fetcher';
import { makePayload } from '@/lib/__tests__/fixtures/acceptanceStrictPayload';

import { useStrictAcceptance } from '../acceptance_strict';

let qc: QueryClient;

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  (useSession as Mock).mockReturnValue({
    data: { accessToken: 'a', refreshToken: 'r' },
    status: 'authenticated',
  });
  (fetcher as Mock).mockResolvedValue({ ...makePayload(), office_id: null });
});

describe('useStrictAcceptance', () => {
  it('「全て」は office_id を付けない', async () => {
    const { result } = renderHook(() => useStrictAcceptance(null, { allOffices: true }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect((fetcher as Mock).mock.calls[0]?.[0]).toBe('/api/v1/acceptance-matrix/strict?');
    expect(result.current.data?.office_id).toBeNull();
  });

  it('拠点を選ぶと office_id を付ける・未定なら読まない', async () => {
    renderHook(() => useStrictAcceptance(null), { wrapper });
    const { result } = renderHook(() => useStrictAcceptance('office-1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect((fetcher as Mock).mock.calls).toHaveLength(1);
    expect((fetcher as Mock).mock.calls[0]?.[0]).toBe(
      '/api/v1/acceptance-matrix/strict?office_id=office-1',
    );
  });
});
