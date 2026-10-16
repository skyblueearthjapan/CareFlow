/**
 * 登録の位置のずれのお知らせ — 本物の QueryClient で [元に戻す] を確かめる。
 *
 * 合わせた後はモニターの再取得でずれが無くなり、お知らせは消える。toast の [元に戻す] は
 * その後に押されるので、消えた後でも POST /location/revert が飛び、モニターを
 * 再取得させることを確かめる (location-drift-align-2026-10-17 §5)。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'token', refreshToken: null },
    status: 'authenticated',
  }),
  signOut: vi.fn(),
}));

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { toast } from '@/components/ui/sonner';

import { LocationDriftNotice, UNDO_TOAST_MS } from '../LocationDriftNotice';

const DRIFT = { distance_m: 441, suggested_lat: 35.604, suggested_lng: 140.1, scans: 6, days: 3 };
const REGISTERED = { lat: 35.6, lng: 140.1 };

describe('LocationDriftNotice (本物の QueryClient)', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockImplementation(async (url: string) => {
      const body = String(url).endsWith('/location/align')
        ? { lat: 35.604, lng: 140.1 }
        : { lat: 35.6, lng: 140.1 };
      return new Response(JSON.stringify(body), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('お知らせが消えた後でも [元に戻す] で revert を POST し、モニターを再取得させる', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const user = userEvent.setup();
    const ui = (drift: typeof DRIFT | null) => (
      <QueryClientProvider client={qc}>
        <LocationDriftNotice patientId="p-1" drift={drift} registered={REGISTERED} />
      </QueryClientProvider>
    );
    const { rerender } = render(ui(DRIFT));

    await user.click(screen.getByTestId('location-drift-align'));
    await user.click(await screen.findByTestId('location-drift-confirm-align'));
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/api\/v1\/patients\/p-1\/location\/align$/);
    expect((init as RequestInit).method).toBe('POST');
    const options = vi.mocked(toast.success).mock.calls[0]![1] as {
      duration: number;
      action: { onClick: () => void };
    };
    expect(options.duration).toBe(UNDO_TOAST_MS);
    expect(UNDO_TOAST_MS).toBe(10_000);

    // 再取得でずれが無くなった → お知らせは消える。
    rerender(ui(null));
    expect(screen.queryByTestId('location-drift-notice')).toBeNull();
    invalidate.mockClear();

    options.action.onClick();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [revertUrl, revertInit] = fetchMock.mock.calls[1]!;
    expect(String(revertUrl)).toMatch(/\/api\/v1\/patients\/p-1\/location\/revert$/);
    expect((revertInit as RequestInit).method).toBe('POST');
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['monitor'] }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('登録の位置を元に戻しました'));
  });
});
