/**
 * RecordReportButton（訪問記録の A4 出力・設計 §11-3）の vitest。
 *
 * 縛る挙動（SyncReportButton と同じ型）:
 *   1. click ハンドラ内で同期的に `window.open('', '_blank')`（'noopener' は付けない）
 *   2. 取得した HTML を UTF-8 の Blob URL にして新タブへ流し込み、60 秒後に revoke
 *   3. 失敗は toast ＋ 空タブを閉じる（403 / 404 / 422 は現場向けの文言）
 *   4. ポップアップがブロックされたら BE を叩かない
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { fetcherMock, toastMock } = vi.hoisted(() => ({
  fetcherMock: vi.fn(),
  toastMock: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...args: unknown[]) => fetcherMock(...args) }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { accessToken: 'at', refreshToken: 'rt' }, status: 'authenticated' }),
}));
vi.mock('sonner', () => ({ toast: toastMock }));
vi.mock('@/components/ui/sonner', () => ({ toast: toastMock }));

import { ApiError } from '@/lib/api-client';

import { RecordReportButton } from '../RecordReportButton';

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

const HTML = '<!doctype html><html><body>訪問記録</body></html>';
/** BE 契約（§11-3）: `format=json` は `{recording, html, generated_at}` を返す。 */
const REPORT = {
  recording: { id: 'rec-1', patient_name: '山田 太郎' },
  html: HTML,
  generated_at: '2026-09-18T03:00:00Z',
};

describe('RecordReportButton', () => {
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
      value: vi.fn(() => 'blob:record-report'),
      configurable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('押すと report API を呼び、新タブを Blob URL へ遷移させる', async () => {
    fetcherMock.mockResolvedValueOnce(REPORT);
    render(wrap(<RecordReportButton recordingId="rec-1" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));

    await waitFor(() => expect(fetcherMock).toHaveBeenCalledTimes(1));
    const [path, opts] = fetcherMock.mock.calls[0] as [string, { accessToken: string }];
    // JSON で受けて html を取り出す（useSyncReport と同方式・非 JSON 依存をしない）。
    expect(path).toBe('/api/v1/visit-recordings/rec-1/report?format=json');
    expect(opts.accessToken).toBe('at');

    await waitFor(() => expect(win.location.href).toBe('blob:record-report'));
    // 'noopener' を features に付けると window.open が null を返す (仕様) ので付けないこと。
    expect(window.open).toHaveBeenCalledWith('', '_blank');
    expect(win.opener).toBeNull();
    expect(win.close).not.toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalled();

    const blob = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0]![0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('text/html;charset=utf-8');
    expect(blob.size).toBe(new Blob([HTML]).size);
  });

  it('Blob URL は 60 秒後に revoke する', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetcherMock.mockResolvedValueOnce(REPORT);
    render(wrap(<RecordReportButton recordingId="rec-1" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(win.location.href).toBe('blob:record-report'));
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60_000);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:record-report');
  });

  it('403 は「管理者と本人のみ」トーストを出し、空タブを閉じる', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 403', 403, null));
    render(wrap(<RecordReportButton recordingId="rec-2" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('管理者と本人のみ出力できます');
    expect(win.close).toHaveBeenCalledTimes(1);
  });

  it('404 / 422 もそれぞれの文言を出す', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 404', 404, null));
    const { unmount } = render(wrap(<RecordReportButton recordingId="rec-3" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('記録が見つかりません');
    unmount();

    fetcherMock.mockRejectedValueOnce(new ApiError('API 422', 422, null));
    render(wrap(<RecordReportButton recordingId="rec-4" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(2));
    expect(String(toastMock.error.mock.calls[1][0])).toContain('まだ出力できません');
  });

  it('空の本文・html 欠落は失敗として扱う（BE 契約違反）', async () => {
    fetcherMock.mockResolvedValueOnce({ ...REPORT, html: '' });
    const { unmount } = render(wrap(<RecordReportButton recordingId="rec-5" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(win.close).toHaveBeenCalledTimes(1);
    unmount();

    // html を持たない応答は zod で弾く（生 HTML をそのまま返された場合も同じ）。
    fetcherMock.mockResolvedValueOnce({ recording: { id: 'rec-5b' } });
    render(wrap(<RecordReportButton recordingId="rec-5b" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(2));
    expect(win.close).toHaveBeenCalledTimes(2);
  });

  it('ポップアップがブロックされたら案内トーストを出し、API は叩かない', async () => {
    (window.open as unknown as ReturnType<typeof vi.fn>).mockReturnValue(null);
    render(wrap(<RecordReportButton recordingId="rec-6" />));
    fireEvent.click(screen.getByRole('button', { name: /A4 で出力/ }));
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledTimes(1));
    expect(String(toastMock.warning.mock.calls[0][0])).toContain('ポップアップ');
    expect(fetcherMock).not.toHaveBeenCalled();
  });

  it('disabled のときは押せず理由を title に出す（権限は呼び出し側が判定）', () => {
    render(
      wrap(
        <RecordReportButton recordingId="rec-7" disabled title="A4 出力は管理者と本人だけです" />,
      ),
    );
    const btn = screen.getByTestId('record-report-button');
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', 'A4 出力は管理者と本人だけです');
  });
});
