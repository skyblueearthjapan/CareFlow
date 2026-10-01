/**
 * 訪問の読み込み (統合プレビュー) のバックグラウンド化 — 連携画面の vitest
 * (契約 = docs/plans/smart-preview-async-2026-10-01.md)。
 *
 * 縛る挙動:
 *   - 読み込み中は「カイポケから予定を読み込んでいます（1〜2 分かかることがあります）」を出す
 *   - 画面へ戻ったとき、この週で動いているジョブがあれば待ち受けを再開し、結果を今までどおり出す
 *   - 同じジョブを二重に待ち受けない
 *   - 失敗したら理由 (BE の detail) を出し、「もう一度読み込む」で訪問だけ読み込み直せる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const smartPreviewMutateAsync = vi.fn();
const eventsPreviewMutateAsync = vi.fn();
const idleQuery = { data: undefined, isLoading: false, isError: false, isSuccess: true };
const idleMutation = { mutateAsync: vi.fn(), mutate: vi.fn(), isPending: false, isError: false };

/** テストごとに差し替える、訪問の読み込み (mutation) と実行中ジョブ (query) の状態。 */
const state: {
  smart: Record<string, unknown>;
  active: unknown;
} = { smart: {}, active: null };

vi.mock('@/lib/queries/integrations', () => ({
  useInboundEligibility: () => ({ ...idleQuery, data: { eligible: true } }),
  useInboundSnapshots: () => ({ ...idleQuery, data: { snapshots: [] } }),
  useKaipokeJobs: () => ({ ...idleQuery, data: { items: [], total: 0, limit: 50, offset: 0 } }),
  useRestoreInboundSnapshot: () => ({ ...idleMutation }),
  useActiveSmartInboundPreview: () => ({ ...idleQuery, data: state.active }),
  useSmartInboundPreview: () => ({
    ...idleMutation,
    mutateAsync: smartPreviewMutateAsync,
    error: null,
    ...state.smart,
  }),
  useApplySmartInbound: () => ({ ...idleMutation, error: null }),
  useEventsInboundPreview: () => ({
    ...idleMutation,
    mutateAsync: eventsPreviewMutateAsync,
    error: null,
  }),
  useApplyEventsInbound: () => ({ ...idleMutation, error: null }),
  useCorrectionItems: () => ({ ...idleQuery, data: { items: [] } }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
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

const JOB_ID = '11111111-1111-4111-8111-111111111111';

const SMART_PLAN = {
  weekStart: thisMonday(),
  weekEnd: thisMonday(),
  protectedDays: [],
  replaceDays: [thisMonday()],
  sheetId: null,
  diffSummary: {},
  replace: null,
};

function Harness() {
  const vm = useInbound({ busy: false, credentialsConfigured: true });
  return <InboundControls vm={vm} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  state.smart = {};
  state.active = null;
  smartPreviewMutateAsync.mockResolvedValue(SMART_PLAN);
  eventsPreviewMutateAsync.mockResolvedValue(null);
});

describe('InboundControls — 訪問の読み込み (バックグラウンド)', () => {
  it('読み込み中は所要時間の目安を出す', () => {
    state.smart = { isPending: true };
    render(<Harness />);
    expect(
      screen.getByText(/カイポケから予定を読み込んでいます（1〜2 分かかることがあります）/),
    ).toBeInTheDocument();
  });

  it('この週で動いているジョブがあれば待ち受けを再開し、結果を表示する', async () => {
    state.active = { jobId: JOB_ID, weekStart: thisMonday(), status: 'running' };
    const { rerender } = render(<Harness />);

    await waitFor(() =>
      expect(smartPreviewMutateAsync).toHaveBeenCalledWith({
        weekStart: thisMonday(),
        resumeJobId: JOB_ID,
      }),
    );
    // 再開は訪問だけ (イベントの取得を勝手に始めない)。
    expect(eventsPreviewMutateAsync).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('smart-apply-button')).toBeInTheDocument());

    // 再描画されても同じジョブを二重に待ち受けない。
    rerender(<Harness />);
    expect(smartPreviewMutateAsync).toHaveBeenCalledTimes(1);
  });

  it('動いているジョブが無ければ何も始めない', () => {
    render(<Harness />);
    expect(smartPreviewMutateAsync).not.toHaveBeenCalled();
  });

  it('失敗したら BE の理由を出し、「もう一度読み込む」で訪問だけ読み込み直す', async () => {
    const detail =
      '訪問の読み込みが既に実行中です（10/26 の週）。完了してからもう一度お試しください';
    state.smart = {
      isError: true,
      error: new ApiError('API 409 Conflict (/api/v1/integrations/…)', 409, { detail }),
    };
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByText('訪問の取得に失敗しました')).toBeInTheDocument();
    expect(screen.getByText(detail)).toBeInTheDocument();
    expect(screen.queryByText(/API 409/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'もう一度読み込む' }));
    expect(smartPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: thisMonday() });
    expect(eventsPreviewMutateAsync).not.toHaveBeenCalled();
  });
});
