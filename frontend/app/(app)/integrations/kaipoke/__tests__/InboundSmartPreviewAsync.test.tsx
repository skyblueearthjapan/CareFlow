/**
 * 訪問の読み込み (統合プレビュー) のバックグラウンド化 — 連携画面の vitest
 * (契約 = docs/plans/smart-preview-async-2026-10-01.md)。
 *
 * 縛る挙動:
 *   - 読み込み中は「カイポケから予定を読み込んでいます（1〜2 分かかることがあります）」を出す
 *   - 画面へ戻ったとき、この週で動いているジョブがあれば待ち受けを再開し、結果を今までどおり出す
 *     (訪問が揃ったら、❶と同じ順でイベントも取得する)
 *   - 離れている間に完了していた結果は「HH:MM に読み込んだ結果です」と「読み込み直す」を添えて出す
 *   - 同じジョブを二重に待ち受けない
 *   - 失敗したら理由 (BE の detail) を出し、「もう一度読み込む」で訪問だけ読み込み直せる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
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
    await waitFor(() => expect(screen.getByTestId('smart-apply-button')).toBeInTheDocument());
    // 訪問が揃ったら、❶と同じ順でイベントも取得する (訪問だけで❸へ進ませない)。
    await waitFor(() =>
      expect(eventsPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: thisMonday() }),
    );
    expect(smartPreviewMutateAsync.mock.invocationCallOrder[0]!).toBeLessThan(
      eventsPreviewMutateAsync.mock.invocationCallOrder[0]!,
    );
    // 実行中の再開なので「読み込んだ結果です」の注記は出さない。
    expect(screen.queryByTestId('smart-restored-note')).not.toBeInTheDocument();

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

  it('離れている間に完了していた結果を、時刻と「読み込み直す」を添えて出す', async () => {
    const completedAt = '2026-10-01T01:05:00Z';
    const d = new Date(completedAt);
    const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    state.active = {
      jobId: JOB_ID,
      weekStart: thisMonday(),
      status: 'completed',
      preview: SMART_PLAN,
      completedAt,
    };
    const user = userEvent.setup();
    render(<Harness />);

    await waitFor(() => expect(screen.getByTestId('smart-apply-button')).toBeInTheDocument());
    expect(screen.getByTestId('smart-restored-note').textContent).toContain(
      `${hhmm} に読み込んだ結果です`,
    );
    // 完了済みの結果は待ち受けない (訪問の読み込みは撃たない)。イベントは取得する。
    expect(smartPreviewMutateAsync).not.toHaveBeenCalled();
    await waitFor(() => expect(eventsPreviewMutateAsync).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole('button', { name: '読み込み直す' }));
    await waitFor(() =>
      expect(smartPreviewMutateAsync).toHaveBeenCalledWith({ weekStart: thisMonday() }),
    );
    // 新しく読み込んだら注記は消える。
    await waitFor(() =>
      expect(screen.queryByTestId('smart-restored-note')).not.toBeInTheDocument(),
    );
  });

  it('再開中は vm.resumed が立ち、「前回の読み込みを再開しました」を出す', async () => {
    let resolve: (v: unknown) => void = () => {};
    smartPreviewMutateAsync.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    state.active = { jobId: JOB_ID, weekStart: thisMonday(), status: 'running' };
    const { result } = renderHook(() => useInbound({ busy: false, credentialsConfigured: true }));
    await waitFor(() => expect(result.current.resumed).toBe(true));

    // 読み込み中の表示 (mutation の isPending は実物のフックが立てる)。
    const vm = {
      ...result.current,
      fetching: true,
      smartPreview: { ...result.current.smartPreview, isPending: true },
    } as typeof result.current;
    render(<InboundControls vm={vm} />);
    expect(screen.getByRole('status').textContent).toContain('前回の読み込みを再開しました。');

    await act(async () => {
      resolve(SMART_PLAN);
    });
    await waitFor(() => expect(result.current.resumed).toBe(false));
    expect(result.current.smartPlan).toEqual(SMART_PLAN);
  });

  it('待ち受け中にイベントのみへ切り替えたら、訪問の結果は出さずイベントも勝手に取らない', async () => {
    let resolve: (v: unknown) => void = () => {};
    smartPreviewMutateAsync.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    state.active = { jobId: JOB_ID, weekStart: thisMonday(), status: 'running' };
    const { result } = renderHook(() => useInbound({ busy: false, credentialsConfigured: true }));
    await waitFor(() => expect(result.current.resumed).toBe(true));

    act(() => {
      result.current.setEventsOnly(true);
    });
    await act(async () => {
      resolve(SMART_PLAN);
    });
    await waitFor(() => expect(result.current.resumed).toBe(false));
    expect(result.current.smartPlan).toBeNull();
    expect(eventsPreviewMutateAsync).not.toHaveBeenCalled();
  });
});
