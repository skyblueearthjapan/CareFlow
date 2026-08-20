/**
 * 取り込む対象モード「イベントのみ」(kaipoke-event-two-way-design.md §3-③) のテスト。
 *
 * 担保する約束:
 *   - 既定は「訪問＋イベント」で、❶実行時に smart(訪問) と events の両方が飛ぶ
 *   - 「イベントのみ」に切り替えると ❶ は events だけを飛ばし smart は呼ばない
 *   - モード切替で取得済みプランは破棄される (混成状態で❸できない)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const smartMutateAsync = vi.fn();
const eventsMutateAsync = vi.fn();
const idleQuery = { data: undefined, isLoading: false, isError: false, isSuccess: true };
const idleMutation = { mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false, isError: false };

vi.mock('@/lib/queries/integrations', () => ({
  useInboundEligibility: () => ({ ...idleQuery, data: { eligible: true } }),
  useInboundSnapshots: () => ({ ...idleQuery, data: { snapshots: [] } }),
  useKaipokeJobs: () => ({ ...idleQuery, data: { items: [], total: 0, limit: 50, offset: 0 } }),
  useRestoreInboundSnapshot: () => ({ ...idleMutation }),
  useSmartInboundPreview: () => ({
    ...idleMutation,
    mutateAsync: smartMutateAsync,
    error: null,
  }),
  useApplySmartInbound: () => ({ ...idleMutation, error: null }),
  useEventsInboundPreview: () => ({
    ...idleMutation,
    mutateAsync: eventsMutateAsync,
    error: null,
  }),
  useApplyEventsInbound: () => ({ ...idleMutation, error: null }),
  useCorrectionItems: () => ({ ...idleQuery, data: { items: [] } }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

import { useInbound } from '../_components/useInbound';
import { InboundControls } from '../_components/InboundControls';

function fmt(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

function thisMonday(): string {
  const x = new Date();
  const day = x.getDay();
  x.setDate(x.getDate() + (day === 0 ? -6 : 1 - day));
  return fmt(x);
}

const SMART_PLAN = {
  weekStart: thisMonday(),
  weekEnd: thisMonday(),
  protectedDays: [],
  replaceDays: [],
  sheetId: null,
  diffSummary: {},
  replace: null,
};

const EVENTS_PLAN = {
  weekStart: thisMonday(),
  weekEnd: thisMonday(),
  fetchedTotal: 0,
  sundaySkipped: 0,
  memoCount: 0,
  adds: 0,
  updates: 0,
  deletes: 0,
  changes: [],
  unmatched: [],
};

function Harness() {
  const vm = useInbound({ busy: false, credentialsConfigured: true });
  return <InboundControls vm={vm} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  smartMutateAsync.mockResolvedValue(SMART_PLAN);
  eventsMutateAsync.mockResolvedValue(EVENTS_PLAN);
});

describe('InboundControls — 取り込む対象モード', () => {
  it('既定 (訪問＋イベント) の❶は smart と events の両方を呼ぶ', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(
      screen.getByRole('button', { name: /❶ カイポケの現況を取得して差分を見る（訪問＋イベント）/ }),
    );
    await waitFor(() => expect(eventsMutateAsync).toHaveBeenCalledTimes(1));
    expect(smartMutateAsync).toHaveBeenCalledTimes(1);
  });

  it('「イベントのみ」に切り替えると❶は events だけを呼ぶ', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByTestId('inbound-target-events-only'));
    expect(screen.getByTestId('inbound-events-only-note')).toBeInTheDocument();

    await user.click(
      screen.getByRole('button', { name: /❶ カイポケの現況を取得して差分を見る（イベントのみ）/ }),
    );
    await waitFor(() => expect(eventsMutateAsync).toHaveBeenCalledTimes(1));
    expect(smartMutateAsync).not.toHaveBeenCalled();
  });

  it('モード切替で取得済みプランは破棄される', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    // 両方モードで取得 → ❸ボタンが出る
    await user.click(
      screen.getByRole('button', { name: /（訪問＋イベント）/ }),
    );
    await waitFor(() => expect(screen.getByTestId('smart-apply-button')).toBeInTheDocument());

    // イベントのみへ切替 → プラン破棄で❸が消える
    await user.click(screen.getByTestId('inbound-target-events-only'));
    expect(screen.queryByTestId('smart-apply-button')).not.toBeInTheDocument();
  });
});
