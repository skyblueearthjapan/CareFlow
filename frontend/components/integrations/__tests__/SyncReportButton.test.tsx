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

import { ApiError } from '@/lib/api-client';

import { SyncReportButton } from '../SyncReportButton';

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const report = {
  job: { id: 'job-1', status: 'completed', params: { op: 'apply' } },
  summary: { total: 24, success: 24, failed: 0 },
  detailLevel: 'full',
  generatedAt: '2026-09-03T12:00:00Z',
  html: '<!doctype html><html><body>sync report</body></html>',
};

describe('SyncReportButton', () => {
  const win: { location: { href: string }; close: ReturnType<typeof vi.fn>; opener: unknown } = {
    location: { href: '' },
    close: vi.fn(),
    opener: {},
  };
  beforeEach(() => {
    fetcherMock.mockReset();
    toastMock.warning.mockReset();
    toastMock.error.mockReset();
    win.close.mockReset();
    win.location.href = '';
    win.opener = {};
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:sync-report'),
      configurable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('押すと report API を呼び、新タブを Blob URL へ遷移させる', async () => {
    fetcherMock.mockResolvedValueOnce(report);
    render(wrap(<SyncReportButton jobId="job-1" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    await waitFor(() => expect(fetcherMock).toHaveBeenCalledTimes(1));
    const [path, opts] = fetcherMock.mock.calls[0] as [string, { accessToken: string }];
    expect(path).toBe('/api/v1/integrations/kaipoke/jobs/job-1/report?format=json');
    expect(opts.accessToken).toBe('at');
    await waitFor(() => expect(win.location.href).toBe('blob:sync-report'));
    // 'noopener' を features に付けると window.open が null を返す (仕様) ので付けないこと。
    expect(window.open).toHaveBeenCalledWith('', '_blank');
    expect(win.opener).toBeNull();
    expect(win.close).not.toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalled();
    // Blob は UTF-8 の HTML として渡す (文字化け防止)
    const blob = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0]![0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('text/html;charset=utf-8');
    expect(blob.size).toBe(new Blob([report.html]).size);
  });

  it('Blob URL は 60 秒後に revoke する', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetcherMock.mockResolvedValueOnce(report);
    render(wrap(<SyncReportButton jobId="job-1" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    await waitFor(() => expect(win.location.href).toBe('blob:sync-report'));
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:sync-report');
  });

  it('422 は「報告書の対象外」トーストを出し、空タブを閉じる', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 422', 422, null));
    render(wrap(<SyncReportButton jobId="job-2" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('このジョブは報告書の対象外です');
    expect(win.close).toHaveBeenCalledTimes(1);
  });

  it('404 は「ジョブが見つかりません」トーストを出す', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 404', 404, null));
    render(wrap(<SyncReportButton jobId="job-3" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('ジョブが見つかりません');
    expect(win.close).toHaveBeenCalledTimes(1);
  });

  it('403 は「管理者のみ開けます」トーストを出す', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 403', 403, null));
    render(wrap(<SyncReportButton jobId="job-3b" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('管理者のみ開けます');
    expect(win.close).toHaveBeenCalledTimes(1);
  });

  it('ポップアップがブロックされたら案内トーストを出し、API は叩かない', async () => {
    (window.open as unknown as ReturnType<typeof vi.fn>).mockReturnValue(null);
    fetcherMock.mockResolvedValueOnce(report);
    render(wrap(<SyncReportButton jobId="job-4" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledTimes(1));
    expect(String(toastMock.warning.mock.calls[0][0])).toContain('ポップアップ');
    // 開く先が無いのに BE でレポートを組み立てさせない
    expect(fetcherMock).not.toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it('取得中は busy 表示になり二度押しできない', async () => {
    let resolve: ((v: unknown) => void) | undefined;
    fetcherMock.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    render(wrap(<SyncReportButton jobId="job-5" label="📄 レポート" />));
    fireEvent.click(screen.getByRole('button', { name: /レポート/ }));
    const busy = await screen.findByRole('button', { name: '作成中…' });
    expect(busy).toBeDisabled();
    resolve?.(report);
    await waitFor(() => expect(win.location.href).toBe('blob:sync-report'));
  });
});
