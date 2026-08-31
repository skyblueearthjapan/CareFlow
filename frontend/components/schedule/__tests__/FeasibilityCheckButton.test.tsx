import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { fetcherMock, toastMock } = vi.hoisted(() => ({
  fetcherMock: vi.fn(),
  toastMock: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...args: unknown[]) => fetcherMock(...args) }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { accessToken: 'at', refreshToken: 'rt' }, status: 'authenticated' }),
}));
vi.mock('sonner', () => ({ toast: toastMock }));

import { FeasibilityCheckButton } from '../FeasibilityCheckButton';

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const report = {
  iso_year: 2026,
  iso_week: 36,
  week_start: '2026-08-31',
  week_end: '2026-09-05',
  generated_at: '2026-08-31T12:00:00Z',
  visit_count: 3,
  event_count: 1,
  hard_count: 2,
  soft_count: 1,
  summary: { 重なり: 1, 移動不可: 1, バッファ不足: 1 },
  assumptions: {
    travel_speed_kmh: 20,
    visit_buffer_min: 8,
    lunch_duration_min: 60,
    lunch_window: '11:30-13:30',
    road_factor: 1.3,
    same_address_pair_min_occupancy: 90,
  },
  findings: [],
  html: '<!doctype html><html><body>report</body></html>',
};

describe('FeasibilityCheckButton', () => {
  const win = { location: { href: '' }, close: vi.fn() };
  beforeEach(() => {
    fetcherMock.mockReset();
    toastMock.success.mockReset();
    toastMock.warning.mockReset();
    toastMock.error.mockReset();
    win.close.mockReset();
    win.location.href = '';
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:report'), configurable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
  });
  afterEach(() => vi.restoreAllMocks());

  it('admin 以外には出さない', () => {
    render(wrap(<FeasibilityCheckButton isoYear={2026} isoWeek={36} canEdit={false} />));
    expect(screen.queryByTestId('feasibility-check')).toBeNull();
  });

  it('押すと read-only API を呼び、レポートを新タブに書き出し、件数バッジを出す', async () => {
    fetcherMock.mockResolvedValueOnce(report);
    render(wrap(<FeasibilityCheckButton isoYear={2026} isoWeek={36} officeId="off-1" canEdit />));
    fireEvent.click(screen.getByRole('button', { name: /実現性チェック/ }));
    await waitFor(() => expect(fetcherMock).toHaveBeenCalledTimes(1));
    const [path, opts] = fetcherMock.mock.calls[0] as [string, { accessToken: string }];
    expect(path).toBe('/api/v1/schedule/v2/feasibility-report?iso_year=2026&iso_week=36&office_id=off-1');
    expect(opts.accessToken).toBe('at');
    await waitFor(() => expect(win.location.href).toBe('blob:report'));
    // 'noopener' を features に付けると window.open が null を返す (仕様) ので付けないこと。
    expect(window.open).toHaveBeenCalledWith('', '_blank');
    expect((window.open as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).toHaveLength(2);
    expect(await screen.findByTestId('feasibility-check-badge')).toHaveTextContent('❗2');
    expect(screen.getByTestId('feasibility-check-badge')).toHaveTextContent('△1');
    expect(toastMock.warning).toHaveBeenCalledTimes(1); // ❗あり = warning 1 回 (ポップアップ警告は無し)
    expect(win.close).not.toHaveBeenCalled();
  });

  it('ポップアップがブロックされたら案内トーストを出す（結果バッジは出す）', async () => {
    (window.open as unknown as ReturnType<typeof vi.fn>).mockReturnValue(null);
    fetcherMock.mockResolvedValueOnce({ ...report, hard_count: 0, soft_count: 0, summary: {} });
    render(wrap(<FeasibilityCheckButton isoYear={2026} isoWeek={36} canEdit />));
    fireEvent.click(screen.getByRole('button', { name: /実現性チェック/ }));
    expect(await screen.findByTestId('feasibility-check-badge')).toHaveTextContent('❗0');
    expect(toastMock.success).toHaveBeenCalledTimes(1);
    expect(toastMock.warning).toHaveBeenCalledTimes(1);
    expect(String(toastMock.warning.mock.calls[0][0])).toContain('ポップアップ');
  });

  it('失敗したら空タブを閉じてエラートースト', async () => {
    fetcherMock.mockRejectedValueOnce(new Error('boom'));
    render(wrap(<FeasibilityCheckButton isoYear={2026} isoWeek={36} canEdit />));
    fireEvent.click(screen.getByRole('button', { name: /実現性チェック/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(win.close).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('feasibility-check-badge')).toBeNull();
  });
});
