/**
 * KaipokeConsole — PO確定レイアウト (2026-07-09) の描画契約を固める。
 *
 * ① 既定で「送る」タブのカレンダー（この週の予定）が見える
 * ② 「取り込みプレビュー」タブに切替でプレビュー領域（空状態）が見える
 * ③ 稼働状況カード（現在の状態/直近ジョブ）がモニター下に出る
 *
 * integrations の各 use*Query/Mutation はモックする（IntegrationSettingsMenu.test の流儀を踏襲）。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const idleQuery = { data: undefined, isLoading: false, isError: false, isSuccess: true };
const idleMutation = { mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false, isError: false };

vi.mock('@/lib/queries/integrations', () => ({
  useExpandStatus: () => ({ ...idleQuery, data: { expanded: false }, refetch: vi.fn() }),
  useStartExpand: () => ({ ...idleMutation }),
  useStartDiffLocal: () => ({ ...idleMutation, error: null }),
  useStartApply: () => ({ ...idleMutation }),
  useWeekSchedule: () => ({ ...idleQuery, data: { rows: [] } }),
  useCorrectionItems: () => ({ ...idleQuery, data: { items: [] } }),
  useInboundEligibility: () => ({ ...idleQuery, data: { eligible: true } }),
  useInboundSnapshots: () => ({ ...idleQuery, data: { snapshots: [] } }),
  useKaipokeJobs: () => ({ ...idleQuery, data: { items: [], total: 0, limit: 5, offset: 0 } }),
  useRestoreInboundSnapshot: () => ({ ...idleMutation }),
  useSmartInboundPreview: () => ({ ...idleMutation, error: null }),
  useApplySmartInbound: () => ({ ...idleMutation, error: null }),
  useEventsInboundPreview: () => ({ ...idleMutation, error: null }),
  useApplyEventsInbound: () => ({ ...idleMutation, error: null }),
  useStopJob: () => ({ ...idleMutation }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

// LiveMonitorCard は noVNC 探針 (Image/URL) を持つため、ここではスタブして副作用を避ける。
vi.mock('../_components/LiveMonitorCard', () => ({
  LiveMonitorCard: () => <div data-testid="live-monitor-stub" />,
}));

import { KaipokeConsole } from '../_components/KaipokeConsole';

const baseProps = {
  live: {
    running: false,
    reachable: true,
    logs: [],
    latestJob: { id: 1, job_type: 'diff', status: 'completed' },
  } as never,
  running: false,
  reachable: true,
  latestJob: { id: 1, job_type: 'diff', status: 'completed' } as never,
  credentialsConfigured: true,
  stop: { ...idleMutation } as never,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('KaipokeConsole', () => {
  it('① 既定で「送る」タブのカレンダー（この週の予定）が見える', () => {
    render(<KaipokeConsole {...baseProps} />);
    expect(screen.getByText('この週の予定（コース別）')).toBeInTheDocument();
  });

  it('② 「取り込みプレビュー」タブに切替でプレビュー領域（空状態）が見える', async () => {
    const user = userEvent.setup();
    render(<KaipokeConsole {...baseProps} />);
    await user.click(screen.getByTestId('kaipoke-tab-inbound'));
    expect(
      await screen.findByText('❶ でカイポケの現況を取得すると、ここに取り込みプレビューが出ます。'),
    ).toBeInTheDocument();
  });

  it('③ 稼働状況カード（現在の状態/直近ジョブ）がモニター下に出る', () => {
    render(<KaipokeConsole {...baseProps} />);
    expect(screen.getByRole('heading', { name: '稼働状況' })).toBeInTheDocument();
    expect(screen.getByText('現在の状態')).toBeInTheDocument();
    expect(screen.getByText('直近ジョブ')).toBeInTheDocument();
    expect(screen.getByText('diff / completed')).toBeInTheDocument();
  });
});
