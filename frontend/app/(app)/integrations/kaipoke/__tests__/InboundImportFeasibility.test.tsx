/**
 * 取り込み後の確認 (設計 docs/plans/schedule-feasibility-alerts-design-2026-11-01.md §3-4)。
 *
 *   - 取り込みが成功しても、新しく出た無理・注意の帯は画面に残す (計画は消える)
 *   - 成功トーストにも件数を足す
 *   - 次の読み込み (❶) で消える
 *   - 判定できなかったときはその文を出す
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const smartPreviewMutateAsync = vi.fn();
const eventsPreviewMutateAsync = vi.fn();
const applySmartMutateAsync = vi.fn();
const applyEventsMutateAsync = vi.fn();
const idleQuery = { data: undefined, isLoading: false, isError: false, isSuccess: true };
const idleMutation = { mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false, isError: false };
let eventsPreviewPending = false;

vi.mock('@/lib/queries/integrations', () => ({
  useInboundEligibility: () => ({ ...idleQuery, data: { eligible: true } }),
  useInboundSnapshots: () => ({ ...idleQuery, data: { snapshots: [] } }),
  useKaipokeJobs: () => ({ ...idleQuery, data: { items: [], total: 0, limit: 50, offset: 0 } }),
  useRestoreInboundSnapshot: () => ({ ...idleMutation }),
  useActiveSmartInboundPreview: () => ({ ...idleQuery, data: null }),
  useSmartInboundPreview: () => ({
    ...idleMutation,
    mutateAsync: smartPreviewMutateAsync,
    error: null,
  }),
  useApplySmartInbound: () => ({
    ...idleMutation,
    mutateAsync: applySmartMutateAsync,
    error: null,
  }),
  useEventsInboundPreview: () => ({
    ...idleMutation,
    mutateAsync: eventsPreviewMutateAsync,
    isPending: eventsPreviewPending,
    error: null,
  }),
  useApplyEventsInbound: () => ({
    ...idleMutation,
    mutateAsync: applyEventsMutateAsync,
    error: null,
  }),
  useCorrectionItems: () => ({ ...idleQuery, data: { items: [] } }),
}));

const toastSuccess = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: vi.fn(),
    warning: vi.fn(),
  },
}));

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
  replaceDays: [thisMonday()],
  sheetId: null,
  diffSummary: {},
  replace: null,
};

const EVENTS_PLAN = {
  weekStart: thisMonday(),
  weekEnd: thisMonday(),
  fetchedTotal: 1,
  sundaySkipped: 0,
  memoCount: 0,
  adds: 1,
  updates: 0,
  deletes: 0,
  changes: [
    {
      action: 'add',
      staffName: '田中　看護師',
      staffId: 's-1',
      date: thisMonday(),
      title: '朝会',
      startsAt: `${thisMonday()}T08:30:00+09:00`,
      endsAt: `${thisMonday()}T09:00:00+09:00`,
      externalId: 'ext-1',
    },
  ],
  unmatched: [],
};

function Harness() {
  const vm = useInbound({ busy: false, credentialsConfigured: true });
  return <InboundControls vm={vm} />;
}

async function fetchThenApply(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /（訪問＋イベント）/ }));
  await waitFor(() => expect(screen.getByTestId('smart-apply-button')).toBeInTheDocument());
  await user.click(screen.getByTestId('smart-apply-button'));
  await user.click(screen.getByRole('button', { name: '取り込む' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  eventsPreviewPending = false;
  smartPreviewMutateAsync.mockResolvedValue(SMART_PLAN);
  eventsPreviewMutateAsync.mockResolvedValue(EVENTS_PLAN);
  applyEventsMutateAsync.mockResolvedValue({ added: 1, updated: 0, deleted: 0, failed: 0 });
});

const ITEM = {
  tier: 'hard',
  kind: '重なり',
  day: thisMonday(),
  at: '10:20',
  staff: '田中 看護師',
  patientName: '乙川 二郎',
  reason: '前の予定（甲野 一郎様 10:00〜10:35）と 15 分重なっています。',
  visitIds: ['v-1'],
};

describe('InboundControls — 取り込み後の確認', () => {
  it('成功後も新しく出た無理・注意の帯が残り、トーストに件数を足す', async () => {
    applySmartMutateAsync.mockResolvedValue({
      diff: null,
      replace: { wiped: 1, inserted: 2, skipped: [] },
      feasibility: {
        ok: true,
        hardCount: 1,
        softCount: 0,
        unassignedCount: 0,
        items: [ITEM],
        message: null,
      },
    });
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('import-feasibility')).toBeInTheDocument());
    expect(screen.getByTestId('import-feasibility')).toHaveAttribute('data-state', 'found');
    expect(screen.getByTestId('import-feasibility-hard')).toHaveTextContent('1');
    expect(screen.getByTestId('import-feasibility-item-0')).toHaveTextContent('乙川 二郎様');
    expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining('新しく出た 無理 1・注意 0'));
    // 計画は消えている (❸は押せない) が、帯は残る
    expect(screen.queryByTestId('smart-apply-button')).toBeNull();

    // 次の読み込みで消える
    await user.click(screen.getByRole('button', { name: /（訪問＋イベント）/ }));
    await waitFor(() => expect(screen.queryByTestId('import-feasibility')).toBeNull());
  });

  it('判定できなかったときは、取り込みは終わっている旨の文を出す', async () => {
    applySmartMutateAsync.mockResolvedValue({
      diff: null,
      replace: { wiped: 1, inserted: 2, skipped: [] },
      feasibility: {
        ok: false,
        hardCount: 0,
        softCount: 0,
        unassignedCount: 0,
        items: [],
        message: '取り込み後の確認ができませんでした（取り込みは終わっています）',
      },
    });
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('import-feasibility')).toBeInTheDocument());
    expect(screen.getByTestId('import-feasibility')).toHaveAttribute('data-state', 'failed');
    expect(toastSuccess).toHaveBeenCalledWith(expect.not.stringContaining('新しく出た'));
  });
});

describe('InboundControls — イベントの取り込み後の確認', () => {
  it('訪問とイベントで新しく出た分を合わせて出す', async () => {
    applySmartMutateAsync.mockResolvedValue({
      diff: null,
      replace: { wiped: 1, inserted: 2, skipped: [] },
      feasibility: {
        ok: true,
        hardCount: 1,
        softCount: 0,
        unassignedCount: 0,
        items: [ITEM],
        message: null,
      },
    });
    applyEventsMutateAsync.mockResolvedValue({
      added: 1,
      updated: 0,
      deleted: 0,
      failed: 0,
      feasibility: {
        ok: true,
        hardCount: 0,
        softCount: 1,
        unassignedCount: 0,
        items: [{ ...ITEM, tier: 'soft', kind: 'バッファ不足', patientName: '丙山 三郎' }],
        message: null,
      },
    });
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('import-feasibility-soft')).toBeInTheDocument());
    expect(screen.getByTestId('import-feasibility-hard')).toHaveTextContent('1');
    expect(screen.getByTestId('import-feasibility-soft')).toHaveTextContent('1');
    expect(screen.getByTestId('import-feasibility-item-1')).toHaveTextContent('丙山 三郎様');
    expect(toastSuccess).toHaveBeenCalledWith(expect.stringContaining('新しく出た 無理 1・注意 1'));
  });

  it('イベントの確認だけできなかったときは、訪問の分を出してその旨を添える', async () => {
    applySmartMutateAsync.mockResolvedValue({
      diff: null,
      replace: { wiped: 1, inserted: 2, skipped: [] },
      feasibility: {
        ok: true,
        hardCount: 1,
        softCount: 0,
        unassignedCount: 0,
        items: [ITEM],
        message: null,
      },
    });
    applyEventsMutateAsync.mockResolvedValue({
      added: 1,
      updated: 0,
      deleted: 0,
      failed: 0,
      feasibility: {
        ok: false,
        hardCount: 0,
        softCount: 0,
        unassignedCount: 0,
        items: [],
        message: '取り込み後の確認ができませんでした（取り込みは終わっています）',
      },
    });
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('import-feasibility')).toBeInTheDocument());
    expect(screen.getByTestId('import-feasibility')).toHaveAttribute('data-state', 'found');
    expect(screen.getByTestId('import-feasibility-hard')).toHaveTextContent('1');
    expect(screen.getByTestId('import-feasibility-partial')).toHaveTextContent('できませんでした');
  });
});
