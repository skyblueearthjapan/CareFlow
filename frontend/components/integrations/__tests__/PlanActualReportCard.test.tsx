import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { fetcherMock, toastMock } = vi.hoisted(() => ({
  fetcherMock: vi.fn(),
  toastMock: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...args: unknown[]) => fetcherMock(...args) }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'at', refreshToken: 'rt', user: { role: 'admin' } },
    status: 'authenticated',
  }),
}));
vi.mock('sonner', () => ({ toast: toastMock }));

import { ApiError } from '@/lib/api-client';

import {
  PlanActualReportCard,
  formatCountsLine,
  formatMonthLabel,
  planActualDefaultMonth,
  planActualMonthOptions,
} from '../PlanActualReportCard';

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

/** 2026-09-10 12:00 JST に固定 (JST の当月 = 2026-09 / 既定の対象月 = 前月 2026-08)。 */
const NOW = new Date('2026-09-10T03:00:00Z');
const DEFAULT_MONTH = '2026-08';

function planActualJob(over: Record<string, unknown> = {}) {
  return {
    id: 'job-pa-1',
    job_type: 'fetch',
    status: 'completed',
    week_start: '2026-08-31',
    params: { op: 'plan-actual-compare', month: DEFAULT_MONTH },
    result_summary: {
      month: DEFAULT_MONTH,
      plan_rows: 500,
      actual_rows: 498,
      counts: { 一致: 480, 時刻ズレ: 12, 担当違い: 0, 相違: 0, 予定のみ: 0, 実績のみ: 3, 重複: 2 },
      by_staff: [{ staff: '熊澤', counts: { 一致: 40 }, duplicates: 0, total: 40 }],
    },
    created_at: '2026-09-01T01:00:00Z',
    completed_at: '2026-09-01T01:02:00Z',
    items: [],
    ...over,
  };
}

interface Ctl {
  jobs: unknown[];
  live: Record<string, unknown>;
  /** POST /plan-actual-compare の応答 (既定は 202 相当)。 */
  start: () => Promise<unknown>;
  /** レポート取得の応答。 */
  report: () => Promise<unknown>;
}

function setup(over: Partial<Ctl> = {}): Ctl {
  const ctl: Ctl = {
    jobs: [],
    live: { reachable: true, running: false, logs: [] },
    start: () =>
      Promise.resolve({ jobId: '11111111-1111-1111-1111-111111111111', status: 'running' }),
    report: () => Promise.resolve('<!doctype html><html><body>予実</body></html>'),
    ...over,
  };
  fetcherMock.mockImplementation((path: string) => {
    if (path.startsWith('/api/v1/integrations/live')) return Promise.resolve(ctl.live);
    if (path.startsWith('/api/v1/integrations/kaipoke/jobs')) {
      return Promise.resolve({ items: ctl.jobs, total: ctl.jobs.length, limit: 20, offset: 0 });
    }
    if (path === '/api/v1/integrations/plan-actual-compare') return ctl.start();
    if (path.startsWith('/api/v1/integrations/plan-actual-report')) return ctl.report();
    return Promise.reject(new Error(`unexpected path: ${path}`));
  });
  return ctl;
}

/** 偽タイマーを進めて react-query のポーリング/再取得を走らせる。 */
async function tick(ms: number) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
  });
}

describe('planActualMonthOptions / 表示ヘルパ', () => {
  it('前3か月・当月・翌月を古い順で返す', () => {
    expect(planActualMonthOptions('2026-09')).toEqual([
      '2026-06',
      '2026-07',
      '2026-08',
      '2026-09',
      '2026-10',
    ]);
  });

  it('年をまたぐ (1月・12月)', () => {
    expect(planActualMonthOptions('2026-01')).toEqual([
      '2025-10',
      '2025-11',
      '2025-12',
      '2026-01',
      '2026-02',
    ]);
    expect(planActualMonthOptions('2026-12')).toEqual([
      '2026-09',
      '2026-10',
      '2026-11',
      '2026-12',
      '2027-01',
    ]);
  });

  it('既定の対象月は前月 (締め後に前月を見る運用)', () => {
    expect(planActualDefaultMonth('2026-09')).toBe('2026-08');
    expect(planActualDefaultMonth('2026-01')).toBe('2025-12');
  });

  it('ラベルは「2026年8月」', () => {
    expect(formatMonthLabel('2026-08')).toBe('2026年8月');
    expect(formatMonthLabel('2026-12')).toBe('2026年12月');
  });

  it('件数行は 0 件の区分を畳む (一致だけは 0 でも出す)', () => {
    expect(formatCountsLine({ 一致: 480, 時刻ズレ: 12, 担当違い: 0, 実績のみ: 3, 重複: 2 })).toBe(
      '一致 480・時刻ズレ 12・実績のみ 3・重複 2',
    );
    expect(formatCountsLine({ 一致: 0, 相違: 5 })).toBe('一致 0・相違 5');
    expect(formatCountsLine({})).toBe('');
  });

  it('内数のタグ (職種未設定・同日複数) は区分の後ろに足す (0 件なら出さない)', () => {
    expect(formatCountsLine({ 一致: 100, 実績のみ: 1, 職種未設定: 1, 同日複数: 2 })).toBe(
      '一致 100・実績のみ 1・職種未設定 1・同日複数 2',
    );
    expect(formatCountsLine({ 一致: 100, 職種未設定: 0, 同日複数: 0 })).toBe('一致 100');
  });

  it('イベント除外は区分に混ぜず末尾に添える (0 件なら出さない)', () => {
    expect(formatCountsLine({ 一致: 10, events_skipped: 4 })).toBe('一致 10（イベント除外 4）');
    expect(formatCountsLine({ 一致: 10, events_skipped: 0 })).toBe('一致 10');
  });
});

describe('PlanActualReportCard', () => {
  const win: { location: { href: string }; close: ReturnType<typeof vi.fn>; opener: unknown } = {
    location: { href: '' },
    close: vi.fn(),
    opener: {},
  };

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
    fetcherMock.mockReset();
    toastMock.success.mockReset();
    toastMock.warning.mockReset();
    toastMock.error.mockReset();
    win.close.mockReset();
    win.location.href = '';
    win.opener = {};
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window);
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:plan-actual'),
      configurable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('月セレクトは 5 択 (前3か月〜翌月)・既定は前月', () => {
    setup();
    render(wrap(<PlanActualReportCard />));
    const select = screen.getByTestId('plan-actual-month-select') as HTMLSelectElement;
    expect(select.value).toBe(DEFAULT_MONTH);
    expect([...select.options].map((o) => o.value)).toEqual([
      '2026-06',
      '2026-07',
      '2026-08',
      '2026-09',
      '2026-10',
    ]);
    expect([...select.options].map((o) => o.textContent)).toEqual([
      '2026年6月',
      '2026年7月',
      '2026年8月',
      '2026年9月',
      '2026年10月',
    ]);
  });

  it('「実績を取得して比較」は選択中の月で開始 API を呼ぶ', async () => {
    setup();
    render(wrap(<PlanActualReportCard />));
    fireEvent.change(screen.getByTestId('plan-actual-month-select'), {
      target: { value: '2026-07' },
    });
    fireEvent.click(screen.getByTestId('plan-actual-start-button'));

    await waitFor(() =>
      expect(
        fetcherMock.mock.calls.some(
          (c) => (c[0] as string) === '/api/v1/integrations/plan-actual-compare',
        ),
      ).toBe(true),
    );
    const call = fetcherMock.mock.calls.find(
      (c) => (c[0] as string) === '/api/v1/integrations/plan-actual-compare',
    )!;
    const opts = call[1] as { method: string; body: string };
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({ month: '2026-07' });
    await waitFor(() => expect(toastMock.success).toHaveBeenCalledTimes(1));
    expect(String(toastMock.success.mock.calls[0][0])).toContain('2026年7月');
    expect(String(toastMock.success.mock.calls[0][0])).toContain('約2分');
  });

  it('開始後は短時間ロックして二度押しを防ぎ、15 秒で解ける', async () => {
    setup();
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-start-button'));
    await waitFor(() => expect(screen.getByTestId('plan-actual-start-button')).toBeDisabled());
    await tick(15_000);
    await waitFor(() => expect(screen.getByTestId('plan-actual-start-button')).toBeEnabled());
  });

  it('接続設定が未完了なら開始できない', () => {
    setup();
    render(wrap(<PlanActualReportCard credentialsConfigured={false} />));
    expect(screen.getByTestId('plan-actual-start-button')).toBeDisabled();
  });

  it('別の月の予実比較が動いていれば開始できない', async () => {
    setup({
      jobs: [
        planActualJob({
          id: 'other',
          status: 'running',
          params: { op: 'plan-actual-compare', month: '2026-07' },
        }),
      ],
    });
    render(wrap(<PlanActualReportCard />));
    await waitFor(() => expect(screen.getByTestId('plan-actual-start-button')).toBeDisabled());
    expect(screen.getByTestId('plan-actual-running').textContent).toContain(
      '2026年7月の実績を取得中',
    );
  });

  it('対象月のジョブが running なら「取得中…」を出す (ジョブ行から)', async () => {
    setup({ jobs: [planActualJob({ status: 'running', result_summary: null })] });
    render(wrap(<PlanActualReportCard />));
    await waitFor(() =>
      expect(screen.getByTestId('plan-actual-running').textContent).toContain(
        '2026年8月の実績を取得中…（約2分）',
      ),
    );
    expect(screen.queryByTestId('plan-actual-summary')).toBeNull();
  });

  it('他オペで塞がっているときも押せず、その旨を出す', async () => {
    setup({
      live: {
        reachable: true,
        running: true,
        logs: [],
        latestJob: { status: 'running', params: { op: 'apply' } },
      },
    });
    render(wrap(<PlanActualReportCard />));
    await waitFor(() => expect(screen.getByTestId('plan-actual-start-button')).toBeDisabled());
    expect(screen.getByTestId('plan-actual-running').textContent).toContain('他の処理が実行中です');
  });

  it('running → completed に変わるとリロードなしで件数が出る (ポーリング)', async () => {
    const ctl = setup({ jobs: [planActualJob({ status: 'running', result_summary: null })] });
    render(wrap(<PlanActualReportCard />));
    await waitFor(() => expect(screen.getByTestId('plan-actual-running')).toBeInTheDocument());
    // BE 側でジョブが完了 → 5 秒間隔のポーリングが拾う
    ctl.jobs = [planActualJob()];
    await tick(5_000);
    const line = await screen.findByTestId('plan-actual-summary');
    expect(line.textContent).toContain('一致 480・時刻ズレ 12・実績のみ 3・重複 2');
    expect(screen.queryByTestId('plan-actual-running')).toBeNull();
  });

  it('ライブが running→idle に落ちたらジョブ一覧を取り直す', async () => {
    const ctl = setup({
      live: {
        reachable: true,
        running: true,
        logs: [],
        latestJob: { status: 'running', params: { op: 'apply' } },
      },
    });
    render(wrap(<PlanActualReportCard />));
    await waitFor(() => expect(screen.getByTestId('plan-actual-running')).toBeInTheDocument());
    // 実行中は 2 秒間隔でライブを見ている。終わった瞬間にジョブ一覧を invalidate する。
    ctl.jobs = [planActualJob()];
    ctl.live = { reachable: true, running: false, logs: [] };
    await tick(2_000);
    const line = await screen.findByTestId('plan-actual-summary');
    expect(line.textContent).toContain('一致 480');
  });

  it('直近が失敗なら理由をエラー表示する', async () => {
    setup({
      jobs: [
        planActualJob({
          status: 'failed',
          result_summary: { month: DEFAULT_MONTH, error: '実績CSVの取得に失敗しました' },
        }),
      ],
    });
    render(wrap(<PlanActualReportCard />));
    const err = await screen.findByTestId('plan-actual-error');
    expect(err.textContent).toContain('実績CSVの取得に失敗しました');
    expect(screen.queryByTestId('plan-actual-summary')).toBeNull();
  });

  it('開始が 409 なら「別の処理が実行中です」', async () => {
    setup({ start: () => Promise.reject(new ApiError('API 409', 409, { detail: '実行中です' })) });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-start-button'));
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledTimes(1));
    expect(String(toastMock.warning.mock.calls[0][0])).toBe('別の処理が実行中です');
    expect(toastMock.error).not.toHaveBeenCalled();
  });

  it('開始が 4xx なら BE の日本語 detail を出す', async () => {
    setup({
      start: () => Promise.reject(new ApiError('API 422', 422, { detail: '対象月が不正です' })),
    });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-start-button'));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toBe('対象月が不正です');
  });

  it('開始が 5xx / 524 なら「取得は続いている可能性」を案内し、例外文言は出さない', async () => {
    setup({ start: () => Promise.reject(new ApiError('API 524 Gateway Timeout', 524, null)) });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-start-button'));
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledTimes(1));
    expect(String(toastMock.warning.mock.calls[0][0])).toBe(
      '応答が返りませんでしたが、取得は続いている可能性があります。しばらく待って「最新のレポートを開く」でご確認ください。',
    );
    expect(toastMock.error).not.toHaveBeenCalled();
    // 二度押し防止のロックもかける
    expect(screen.getByTestId('plan-actual-start-button')).toBeDisabled();
  });

  it('通信断 (ApiError でない例外) も同じ案内にする', async () => {
    setup({ start: () => Promise.reject(new TypeError('Failed to fetch')) });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-start-button'));
    await waitFor(() => expect(toastMock.warning).toHaveBeenCalledTimes(1));
    expect(String(toastMock.warning.mock.calls[0][0])).toContain('取得は続いている可能性');
    expect(String(toastMock.warning.mock.calls[0][0])).not.toContain('Failed to fetch');
  });

  it('完了ジョブがあれば件数の 1 行サマリを出す (0 件の区分は畳む)', async () => {
    setup({ jobs: [planActualJob()] });
    render(wrap(<PlanActualReportCard />));
    const line = await screen.findByTestId('plan-actual-summary');
    expect(line.textContent).toContain('一致 480・時刻ズレ 12・実績のみ 3・重複 2');
    expect(line.textContent).not.toContain('担当違い');
    // 職種未設定（未）の注意書きは常設
    expect(screen.getByTestId('plan-actual-note').textContent).toContain('職種未設定');
  });

  it('選択月に完了ジョブが無ければサマリは出さない', async () => {
    setup({ jobs: [planActualJob()] });
    render(wrap(<PlanActualReportCard />));
    await screen.findByTestId('plan-actual-summary');
    fireEvent.change(screen.getByTestId('plan-actual-month-select'), {
      target: { value: '2026-07' },
    });
    await waitFor(() => expect(screen.queryByTestId('plan-actual-summary')).toBeNull());
  });

  it('「最新のレポートを開く」は HTML を新しいタブへ開く', async () => {
    setup();
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-report-button'));
    await waitFor(() => expect(win.location.href).toBe('blob:plan-actual'));
    const path = fetcherMock.mock.calls.find((c) =>
      (c[0] as string).startsWith('/api/v1/integrations/plan-actual-report'),
    )![0] as string;
    expect(path).toBe(`/api/v1/integrations/plan-actual-report?month=${DEFAULT_MONTH}&format=html`);
    expect(window.open).toHaveBeenCalledWith('', '_blank');
    expect(win.opener).toBeNull();
    const blob = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock
      .calls[0]![0] as Blob;
    expect(blob.type).toBe('text/html;charset=utf-8');
  });

  it('レポートの 404 は BE の日本語 detail を優先する', async () => {
    setup({
      report: () =>
        Promise.reject(
          new ApiError('API 404', 404, { detail: '予定CSVがまだ取り込まれていません' }),
        ),
    });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-report-button'));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toBe('予定CSVがまだ取り込まれていません');
    expect(win.close).toHaveBeenCalledTimes(1);
  });

  it('detail の無い 404 は「先に実績を取得して比較」を案内する', async () => {
    setup({ report: () => Promise.reject(new ApiError('API 404', 404, null)) });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-report-button'));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toBe(
      '8月の実績データがまだありません。先に「実績を取得して比較」を実行してください',
    );
  });

  it('空の HTML では真っ白なタブを開かない', async () => {
    setup({ report: () => Promise.resolve('') });
    render(wrap(<PlanActualReportCard />));
    fireEvent.click(screen.getByTestId('plan-actual-report-button'));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledTimes(1));
    expect(String(toastMock.error.mock.calls[0][0])).toContain('レポートを取得できませんでした');
    expect(win.close).toHaveBeenCalledTimes(1);
    expect(win.location.href).toBe('');
  });
});
