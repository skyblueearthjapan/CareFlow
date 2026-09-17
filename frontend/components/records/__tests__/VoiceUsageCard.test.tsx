/**
 * VoiceUsageCard（音声記録の利用状況・設計 §11-3 の費用ダッシュボード）の vitest。
 *
 * 縛る挙動:
 *   1. 当月を既定で問い合わせ、件数・合計分・トークン・費用（USD ＋ 固定レートの円）を出す
 *   2. 「前の月 / 次の月」で month が動き、その月を BE に問い合わせ直す（未来は押せない）
 *   3. 一般ロールは問い合わせず「管理者のみ表示できます」（カードは隠さない = PO 決定）
 *   4. 0 件の月は空状態（らく助のひとこと）
 *   5. 失敗件数が 0 でなければ warning トーンで出す
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { fetcherMock, sessionRef } = vi.hoisted(() => ({
  fetcherMock: vi.fn(),
  sessionRef: { value: { role: 'admin' as string } },
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...args: unknown[]) => fetcherMock(...args) }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'at', refreshToken: 'rt', user: { role: sessionRef.value.role } },
    status: 'authenticated',
  }),
}));

import { ApiError } from '@/lib/api-client';

import { VoiceUsageCard, formatUsageMonthLabel, voiceUsageMonthOptions } from '../VoiceUsageCard';

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

/** 2026-09-18 12:00 JST に固定（JST の当月 = 2026-09）。 */
const NOW = new Date('2026-09-18T03:00:00Z');
const THIS_MONTH = '2026-09';

const USAGE = {
  month: THIS_MONTH,
  recordings: 42,
  minutes_total: 618,
  tokens_in: 1234567,
  tokens_out: 89012,
  // BE は Decimal を文字列で返すことがある（契約どおり許容する）。
  cost_usd: '1.2345',
  by_staff: [
    { staff_id: 'st-1', staff_name: '熊澤', recordings: 20, minutes: 300, cost_usd: 0.6 },
    { staff_id: 'st-2', staff_name: '小西', recordings: 22, minutes: 318, cost_usd: 0.6345 },
  ],
  by_status: { summarized: 40, failed: 2 },
  failed: 2,
};

describe('VoiceUsageCard', () => {
  beforeEach(() => {
    fetcherMock.mockReset();
    sessionRef.value.role = 'admin';
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('月の選択肢は当月から過去 11 か月＝12 個（新しい順）', () => {
    const opts = voiceUsageMonthOptions(THIS_MONTH);
    expect(opts).toHaveLength(12);
    expect(opts[0]).toBe('2026-09');
    expect(opts[1]).toBe('2026-08');
    expect(opts[11]).toBe('2025-10');
    expect(formatUsageMonthLabel('2026-09')).toBe('2026年9月');
  });

  it('当月を既定で読み、件数・分・トークン・費用（USD と円）を出す', async () => {
    fetcherMock.mockResolvedValueOnce(USAGE);
    render(wrap(<VoiceUsageCard />));

    await waitFor(() => expect(fetcherMock).toHaveBeenCalledTimes(1));
    expect((fetcherMock.mock.calls[0] as [string])[0]).toBe(
      `/api/v1/admin/visit-recordings/usage?month=${THIS_MONTH}`,
    );

    expect(await screen.findByTestId('voice-usage-recordings')).toHaveTextContent('42 件');
    expect(screen.getByTestId('voice-usage-minutes')).toHaveTextContent('618 分');
    expect(screen.getByTestId('voice-usage-minutes')).toHaveTextContent('10.3 時間');
    expect(screen.getByTestId('voice-usage-tokens')).toHaveTextContent('1,234,567');
    expect(screen.getByTestId('voice-usage-tokens')).toHaveTextContent('89,012');
    // 月合計は通貨として 2 桁、円は固定レート 150 の概算（1.2345 × 150 = 185.175 → 185）。
    expect(screen.getByTestId('voice-usage-cost')).toHaveTextContent('$1.23');
    expect(screen.getByTestId('voice-usage-cost')).toHaveTextContent('約 185 円');
    expect(screen.getByTestId('voice-usage-rate-note')).toHaveTextContent('概算');

    // スタッフ別の小表。明細は 4 桁（$0.00 に潰れると比較できない）。
    const table = screen.getByTestId('voice-usage-by-staff');
    expect(table).toHaveTextContent('熊澤');
    expect(table).toHaveTextContent('小西');
    expect(table).toHaveTextContent('$0.6345');

    // 失敗は 0 でなければ出す
    expect(screen.getByTestId('voice-usage-failed')).toHaveTextContent('失敗 2 件');
  });

  it('「前の月」で月が動き、その月を問い合わせ直す（次の月は当月で止まる）', async () => {
    fetcherMock.mockResolvedValue({ ...USAGE, recordings: 0, failed: 0, by_staff: [] });
    render(wrap(<VoiceUsageCard />));
    await waitFor(() => expect(fetcherMock).toHaveBeenCalledTimes(1));

    // 当月なので「次の月」は押せない
    expect(screen.getByTestId('voice-usage-next')).toBeDisabled();

    fireEvent.click(screen.getByTestId('voice-usage-prev'));
    await waitFor(() => expect(fetcherMock).toHaveBeenCalledTimes(2));
    expect((fetcherMock.mock.calls[1] as [string])[0]).toBe(
      '/api/v1/admin/visit-recordings/usage?month=2026-08',
    );
    expect(screen.getByTestId('voice-usage-month-select')).toHaveValue('2026-08');

    // 前月に戻ったので「次の月」が押せる
    expect(screen.getByTestId('voice-usage-next')).not.toBeDisabled();
    fireEvent.click(screen.getByTestId('voice-usage-next'));
    await waitFor(() =>
      expect(screen.getByTestId('voice-usage-month-select')).toHaveValue(THIS_MONTH),
    );
  });

  it('0 件の月は空状態（らく助のひとこと）', async () => {
    fetcherMock.mockResolvedValueOnce({
      month: THIS_MONTH,
      recordings: 0,
      minutes_total: 0,
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      by_staff: [],
      by_status: {},
      failed: 0,
    });
    render(wrap(<VoiceUsageCard />));
    expect(await screen.findByText('この月はまだ記録がありません')).toBeInTheDocument();
    expect(screen.queryByTestId('voice-usage-recordings')).not.toBeInTheDocument();
  });

  it('一般ロールは問い合わせず「管理者のみ表示できます」を出す（カードは隠さない）', async () => {
    sessionRef.value.role = 'staff';
    render(wrap(<VoiceUsageCard />));
    expect(screen.getByTestId('voice-usage-card')).toBeInTheDocument();
    expect(screen.getByTestId('voice-usage-admin-only')).toHaveTextContent(
      '管理者のみ表示できます',
    );
    expect(screen.getByTestId('voice-usage-month-select')).toBeDisabled();
    // 403 を積まないため BE は叩かない
    await waitFor(() => expect(fetcherMock).not.toHaveBeenCalled());
  });

  it('BE が 403 を返しても同じ案内にする（エラー文言を生で出さない）', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 403', 403, null));
    render(wrap(<VoiceUsageCard />));
    expect(await screen.findByTestId('voice-usage-admin-only')).toHaveTextContent(
      '管理者のみ表示できます',
    );
    expect(screen.queryByTestId('voice-usage-error')).not.toBeInTheDocument();
  });

  it('403 以外の失敗は理由を出す', async () => {
    fetcherMock.mockRejectedValueOnce(new ApiError('API 500', 500, { detail: 'サーバーエラー' }));
    render(wrap(<VoiceUsageCard />));
    expect(await screen.findByTestId('voice-usage-error')).toHaveTextContent('サーバーエラー');
  });
});
