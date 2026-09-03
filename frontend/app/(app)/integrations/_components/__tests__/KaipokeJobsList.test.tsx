import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { fetcherMock } = vi.hoisted(() => ({ fetcherMock: vi.fn() }));
vi.mock('@/lib/api/fetcher', () => ({ fetcher: (...args: unknown[]) => fetcherMock(...args) }));
vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'at', refreshToken: 'rt', user: { role: 'admin' } },
    status: 'authenticated',
  }),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() } }));

import { KaipokeJobsList } from '../KaipokeJobsList';

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

function job(over: Record<string, unknown>) {
  return {
    id: '00000000-0000-4000-8000-000000000001',
    job_type: 'push',
    status: 'completed',
    week_start: '2026-08-31',
    params: { op: 'apply' },
    result_summary: null,
    started_at: null,
    completed_at: null,
    created_by_user_id: null,
    created_at: '2026-09-03T00:00:00Z',
    updated_at: '2026-09-03T00:00:00Z',
    items: [],
    ...over,
  };
}

describe('KaipokeJobsList — 連携結果レポートボタン', () => {
  beforeEach(() => fetcherMock.mockReset());

  it('op ラベルを表示し、完了 × 対象 op の行だけ 📄 レポートを出す', async () => {
    fetcherMock.mockResolvedValue({
      items: [
        job({ id: '00000000-0000-4000-8000-00000000000a', params: { op: 'apply' } }),
        job({
          id: '00000000-0000-4000-8000-00000000000b',
          job_type: 'fetch',
          params: { op: 'smart-apply' },
          status: 'running',
        }),
        job({
          id: '00000000-0000-4000-8000-00000000000c',
          job_type: 'fetch',
          params: { op: 'smart-preview' },
        }),
        job({ id: '00000000-0000-4000-8000-00000000000d', job_type: 'fetch', params: {} }),
      ],
      total: 4,
      limit: 50,
      offset: 0,
    });

    render(wrap(<KaipokeJobsList />));

    // op ラベル (raw な job_type ではなく現場の言葉・正典の表記)
    expect(await screen.findByText('訪問をカイポケへ送信')).toBeInTheDocument();
    expect(screen.getByText('カイポケから取込（自動判別）')).toBeInTheDocument();
    expect(screen.getByText('取込プレビュー')).toBeInTheDocument();
    // op を持たないジョブは job_type にフォールバック (フィルタの選択肢と混ざらないよう行内で見る)
    const rows = screen.getAllByRole('row');
    expect(within(rows[4]!).getByText('fetch')).toBeInTheDocument();
    // 「種類」フィルタ (fetch/push) との対応は小さなヒントで残す
    expect(screen.getAllByText('（送信）')).toHaveLength(1);
    expect(screen.getAllByText('（取得）')).toHaveLength(3);
    expect(screen.getByRole('columnheader', { name: '内容' })).toBeInTheDocument();

    // レポートボタンは apply (completed) の 1 行だけ
    await waitFor(() => expect(screen.getAllByTestId('sync-report-button')).toHaveLength(1));
    // 「詳細」リンクは全行に残る
    expect(screen.getAllByRole('link', { name: '詳細' })).toHaveLength(4);
  });

  it('失敗した対象ジョブにもレポートボタンを出す', async () => {
    fetcherMock.mockResolvedValue({
      items: [job({ status: 'failed', params: { op: 'events-outbound' } })],
      total: 1,
      limit: 50,
      offset: 0,
    });
    render(wrap(<KaipokeJobsList />));
    expect(await screen.findByTestId('sync-report-button')).toBeInTheDocument();
    expect(screen.getByText('イベントをカイポケへ送信')).toBeInTheDocument();
  });
});
