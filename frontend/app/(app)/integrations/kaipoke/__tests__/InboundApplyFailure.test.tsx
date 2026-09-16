/**
 * ❸取り込みの部分失敗を成功と見せないこと (A-3・2026-09-16)。
 * 正典 = docs/plans/mobile-staff-schedule-design-2026-09-16.md §1 A-3。
 *
 * 担保する約束:
 *   - 訪問 apply が失敗したら **イベント apply を実行しない** (部分適用を作らない)
 *   - 成功トーストを出さない (部分失敗も含む)
 *   - 失敗内容は画面に残る Alert (smart-apply-error) に出す。BE の detail を出す
 *     (`API 422 …` という機械向け文字列では現場が対処できない)
 *   - 訪問成功 → イベント失敗の順では「訪問は適用済み」を Alert に含める
 *   - 失敗後は❸を押せない (古いプレビューでの再実行 = 二重適用を封じる)
 *   - 再プレビュー (❶) でクリアされる
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

vi.mock('@/lib/queries/integrations', () => ({
  useInboundEligibility: () => ({ ...idleQuery, data: { eligible: true } }),
  useInboundSnapshots: () => ({ ...idleQuery, data: { snapshots: [] } }),
  useKaipokeJobs: () => ({ ...idleQuery, data: { items: [], total: 0, limit: 50, offset: 0 } }),
  useRestoreInboundSnapshot: () => ({ ...idleMutation }),
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

import { ApiError } from '@/lib/api-client';

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

const BLOCKED_DETAIL =
  'らく助側で取消済みの訪問があります（今週だけ取消／ステータス連動）。⇧送信でカイポケへ反映してから置換してください（対象日: 2026-09-14）';

const EVENTS_DETAIL = 'イベントの upsert に失敗しました（カイポケ側の応答が不正です）';

/**
 * 実際に `fetcher` が投げるのと同じ形の 422。理由は `message` ではなく
 * `body.detail` に入っているので、`e.message` を出す実装だとこのテストは落ちる。
 */
function apiError(detail: string): ApiError {
  return new ApiError(
    'API 422 Unprocessable Entity (/api/v1/integrations/kaipoke/smart-apply)',
    422,
    {
      detail,
    },
  );
}

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
  smartPreviewMutateAsync.mockResolvedValue(SMART_PLAN);
  eventsPreviewMutateAsync.mockResolvedValue(EVENTS_PLAN);
  applyEventsMutateAsync.mockResolvedValue({ added: 1, updated: 0, deleted: 0, failed: 0 });
});

describe('InboundControls — ❸取り込みの失敗', () => {
  it('訪問 apply が 422 なら イベント apply を実行せず、BE の detail を Alert に残す', async () => {
    applySmartMutateAsync.mockRejectedValue(apiError(BLOCKED_DETAIL));
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('smart-apply-error')).toBeInTheDocument());
    const alert = screen.getByTestId('smart-apply-error');
    expect(alert.textContent).toContain(BLOCKED_DETAIL);
    // 機械向けの message (API 422 …) は出さない。
    expect(alert.textContent).not.toContain('API 422');
    expect(applyEventsMutateAsync).not.toHaveBeenCalled();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('失敗後は❸を押せず、❶からの取り直しを促す', async () => {
    applySmartMutateAsync.mockRejectedValue(apiError(BLOCKED_DETAIL));
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('smart-apply-error')).toBeInTheDocument());
    expect(screen.getByTestId('smart-apply-button')).toBeDisabled();
    expect(screen.getByTestId('smart-apply-error').textContent).toContain(
      '❶ プレビューを取り直してから再実行してください',
    );
  });

  it('訪問成功 → イベント失敗では 成功トーストを出さず、適用済みの内訳を Alert に含める', async () => {
    applySmartMutateAsync.mockResolvedValue({
      diff: null,
      replace: { wiped: 3, inserted: 5, skipped: [] },
    });
    applyEventsMutateAsync.mockRejectedValue(apiError(EVENTS_DETAIL));
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(screen.getByTestId('smart-apply-error')).toBeInTheDocument());
    const text = screen.getByTestId('smart-apply-error').textContent ?? '';
    expect(text).toContain('訪問は適用済み');
    expect(text).toContain('置換: 削除 3 / 挿入 5');
    expect(text).toContain(`イベントの取込に失敗: ${EVENTS_DETAIL}`);
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.getByTestId('smart-apply-button')).toBeDisabled();
  });

  it('再プレビュー (❶) で Alert はクリアされる', async () => {
    applySmartMutateAsync.mockRejectedValue(apiError(BLOCKED_DETAIL));
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);
    await waitFor(() => expect(screen.getByTestId('smart-apply-error')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /（訪問＋イベント）/ }));
    await waitFor(() => expect(screen.queryByTestId('smart-apply-error')).not.toBeInTheDocument());
  });

  it('成功時は従来どおり イベント apply も走り、完了トーストが出る', async () => {
    applySmartMutateAsync.mockResolvedValue({ diff: null, replace: null });
    const user = userEvent.setup();
    render(<Harness />);

    await fetchThenApply(user);

    await waitFor(() => expect(applyEventsMutateAsync).toHaveBeenCalledTimes(1));
    expect(toastSuccess).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('smart-apply-error')).not.toBeInTheDocument();
  });
});
