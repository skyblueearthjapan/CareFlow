/**
 * useSchedulingSettings / useUpdateSchedulingSettings hook テスト — Phase G-88 Step4。
 *
 * カバー:
 *   1. GET → fetcher が /api/v1/scheduling-settings を呼び、zod parse 済の data を返す
 *   2. PUT → 部分更新 payload を method:'PUT' / body で送信し、レスポンスを反映する
 *   3. PUT (RESET_ALL) → 全項目 null の payload を送る
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

vi.mock('@/lib/api/fetcher', () => ({
  fetcher: vi.fn(),
}));

import { useSession } from 'next-auth/react';
import { fetcher } from '@/lib/api/fetcher';

import { useSchedulingSettings, useUpdateSchedulingSettings } from '../schedulingSettings';
import { SCHEDULING_SETTINGS_RESET_ALL } from '@/lib/schemas/schedulingSettings';

const RESPONSE = {
  values: {
    visit_buffer_min: 8,
    travel_speed_kmh: 20,
    lunch_duration_min: 60,
    lunch_window_start: '11:30:00',
    lunch_window_end: '13:30:00',
    business_start: '09:30:00',
    business_end: '18:00:00',
    max_patients_per_course: 6,
  },
  is_default: {
    visit_buffer_min: true,
    travel_speed_kmh: true,
    lunch_duration_min: true,
    lunch_window_start: true,
    lunch_window_end: true,
    business_start: true,
    business_end: true,
    max_patients_per_course: true,
  },
};

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  (useSession as Mock).mockReturnValue({
    data: { accessToken: 'tok', refreshToken: 'ref', user: { role: 'admin' } },
    status: 'authenticated',
  });
});

describe('useSchedulingSettings (GET)', () => {
  it('1. /api/v1/scheduling-settings を呼び、parse 済の data を返す', async () => {
    (fetcher as Mock).mockResolvedValueOnce(RESPONSE);

    const { result } = renderHook(() => useSchedulingSettings(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/scheduling-settings',
      expect.objectContaining({ accessToken: 'tok', refreshToken: 'ref' }),
    );
    expect(result.current.data?.values.visit_buffer_min).toBe(8);
    expect(result.current.data?.is_default.business_start).toBe(true);
  });
});

describe('useUpdateSchedulingSettings (PUT)', () => {
  it('2. 部分更新 payload を method:PUT / body で送信する', async () => {
    (fetcher as Mock).mockResolvedValueOnce({
      ...RESPONSE,
      values: { ...RESPONSE.values, visit_buffer_min: 12 },
      is_default: { ...RESPONSE.is_default, visit_buffer_min: false },
    });

    const { result } = renderHook(() => useUpdateSchedulingSettings(), { wrapper });

    let returned: unknown;
    await act(async () => {
      returned = await result.current.mutateAsync({ visit_buffer_min: 12 });
    });

    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/scheduling-settings',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ visit_buffer_min: 12 }),
      }),
    );
    expect((returned as typeof RESPONSE).values.visit_buffer_min).toBe(12);
  });

  it('3. RESET_ALL は全項目 null の payload を送る', async () => {
    (fetcher as Mock).mockResolvedValueOnce(RESPONSE);

    const { result } = renderHook(() => useUpdateSchedulingSettings(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync(SCHEDULING_SETTINGS_RESET_ALL);
    });

    const call = (fetcher as Mock).mock.calls[0];
    expect(call[1].method).toBe('PUT');
    const sentBody = JSON.parse(call[1].body);
    expect(Object.values(sentBody).every((x) => x === null)).toBe(true);
    expect(Object.keys(sentBody)).toHaveLength(8);
  });
});
