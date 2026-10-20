/**
 * 「カイポケとの差分」(LeaveComparePanel) と「カイポケへ送る」(LeaveOutboundDialog)。
 * 設計 §7-4-2〜§7-4-4。名前は架空 (職員A・職員B)。
 *
 * 担保する約束:
 *   - 差分の action ごとのボタン・種類を選ぶ・action が無い差分は案内だけ・合わせ済み
 *   - 同じ職員の「らく助に入れる」はまとめて押せる
 *   - 合わせたら recompare_needed で「もう一度比べて」・409 の detail をそのまま出す
 *   - 「実績を記録する」は未確認の日・差分などがあれば押せず理由を出す
 *   - 進み具合 (取り直し中・比べている・終わった)・夕方の注意・開始の 409
 *   - 送る前の一覧 (送れない理由)・照合がまだの月は送れない・送信中・結果
 */
import * as React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import type * as ReactQueryModule from '@tanstack/react-query';
import type * as LeaveQueries from '@/lib/queries/leave';
import { ApiError } from '@/lib/api-client';
import type {
  CompareJobStatus,
  CompareLatest,
  LeaveCompareRunView,
  LeaveDiff,
  OutboundJobStatus,
  OutboundPreview,
} from '@/lib/schemas/leaveKaipoke';

const qcStub = { invalidateQueries: vi.fn(() => Promise.resolve()) };
vi.mock('@tanstack/react-query', async (importOriginal) => {
  const actual = await importOriginal<typeof ReactQueryModule>();
  return { ...actual, useQueryClient: () => qcStub };
});

vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const invalidate = vi.fn(() => Promise.resolve());
vi.mock('@/lib/queries/leave', async (importOriginal) => {
  const actual = await importOriginal<typeof LeaveQueries>();
  return { leaveErrorMessage: actual.leaveErrorMessage, invalidateAfterLeave: () => invalidate() };
});

let latest: CompareLatest | undefined;
let latestError: Error | null = null;
let jobError: Error | null = null;
let outJobError: Error | null = null;
let applyPending = false;
let jobStatus: CompareJobStatus | undefined;
let preview: OutboundPreview | undefined;
let outJob: OutboundJobStatus | undefined;
const jobCalls: (string | null)[] = [];
const outJobCalls: (string | null)[] = [];
const startCompare = vi.fn();
const applyCompare = vi.fn();
const startOutbound = vi.fn();
vi.mock('@/lib/queries/leaveKaipoke', () => ({
  useCompareLatest: () => ({
    data: latestError ? undefined : latest,
    isLoading: false,
    isError: !!latestError,
    error: latestError,
  }),
  useCompareJob: (id: string | null) => {
    jobCalls.push(id);
    return {
      data: id && !jobError ? jobStatus : undefined,
      isError: !!id && !!jobError,
      error: jobError,
    };
  },
  useStartCompare: () => ({ mutateAsync: startCompare, isPending: false }),
  useApplyCompare: () => ({ mutateAsync: applyCompare, isPending: applyPending }),
  useOutboundPreview: (_m: string, enabled: boolean) => ({
    data: enabled ? preview : undefined,
    isError: false,
  }),
  useStartOutbound: () => ({ mutateAsync: startOutbound, isPending: false }),
  useOutboundJob: (id: string | null) => {
    outJobCalls.push(id);
    return {
      data: id && !outJobError ? outJob : undefined,
      isError: !!id && !!outJobError,
      error: outJobError,
    };
  },
}));

import { toast } from '@/components/ui/sonner';
import { LeaveComparePanel, recordActualBlockers } from '../LeaveComparePanel';

const S1 = '11111111-1111-4111-8111-111111111111';
const S2 = '22222222-2222-4222-8222-222222222222';

function diff(over: Partial<LeaveDiff>): LeaveDiff {
  return {
    kind: 'kaipoke_only',
    case: 2,
    staff_id: S1,
    staff_name: '職員A',
    date: '2026-11-10',
    rakusuke: null,
    sent: null,
    kaipoke: 'off',
    baseline_pending: false,
    action: 'add_to_rakusuke',
    message: 'カイポケだけ休み（公休）',
    detail: {},
    resolved: null,
    ...over,
  };
}

function run(over: Partial<LeaveCompareRunView> = {}): LeaveCompareRunView {
  const diffs = over.diffs ?? [];
  return {
    id: 'run-1',
    month: '2026-11-01',
    kaipoke_job_id: 'job-0',
    fetched_at: {},
    diffs,
    auto_updates: [],
    diff_count: diffs.length,
    planned_send_count: 0,
    unconfirmed_days: 0,
    created_at: '2026-10-21T01:00:00Z',
    ...over,
  };
}

function setLatest(r: LeaveCompareRunView | null, over: Partial<CompareLatest> = {}) {
  latest = {
    month: '2026-11',
    run: r,
    job: null,
    last_compared_at: r ? '2026-10-21T01:00:00Z' : null,
    last_leave_changed_at: null,
    changed_since_compare: false,
    evening_warning: null,
    ...over,
  };
}

function job(status: string, over: Partial<CompareJobStatus> = {}): CompareJobStatus {
  return {
    job: {
      id: 'job-1',
      op: 'leave-compare',
      status,
      month: '2026-11',
      done: 2,
      total: 6,
      results: [],
      failed: [],
      error: null,
      stale: false,
    },
    state: 'fetching',
    run: null,
    compare_error: null,
    ...over,
  };
}

const ALL_DIFFS: LeaveDiff[] = [
  diff({}),
  diff({ date: '2026-11-11', message: 'カイポケだけ休み（午後休）', kaipoke: 'pm_off' }),
  diff({
    kind: 'type_mismatch',
    case: 8,
    staff_id: S2,
    staff_name: '職員B',
    date: '2026-11-12',
    rakusuke: 'off',
    kaipoke: 'paid_off',
    action: 'match_kaipoke_kind',
    message: 'らく助は公休・カイポケは有給',
  }),
  diff({
    kind: 'unknown_kind',
    case: null,
    staff_id: S2,
    staff_name: '職員B',
    date: '2026-11-13',
    kaipoke: null,
    action: 'choose_kind',
    message: '「休み」の種類を選んでください',
    detail: { subject: '休み', start: '10:00', end: '11:00', content_hash: 'h-1' },
  }),
  diff({
    kind: 'actual_conflict',
    case: null,
    staff_id: S2,
    staff_name: '職員B',
    date: '2026-11-16',
    action: null,
    message: 'カイポケで要対応：休みと実績が食い違っています',
    detail: { visits: [{ patient: '利用者X', start: '09:00', end: '10:00' }] },
  }),
  diff({
    kind: 'staff_not_found',
    case: null,
    staff_id: null,
    staff_name: '',
    date: null,
    action: null,
    message: 'カイポケの実績の職員「職員Z」がらく助に見つかりません',
    detail: {
      name: '職員Z',
      dates: ['2026-11-17'],
      guide: 'らく助の職員の名前をそろえて比べ直してください',
    },
  }),
  diff({
    kind: 'unconfirmed',
    case: null,
    staff_id: null,
    staff_name: '',
    date: '2026-11-30',
    action: 'refetch_week',
    message: '未確認：この週のカイポケの予定を取れていません',
    detail: { reason: 'no_snapshot', week_start: '2026-11-30' },
  }),
  diff({
    staff_id: S2,
    staff_name: '職員B',
    date: '2026-11-18',
    message: 'カイポケだけ休み（公休）',
    resolved: { action: 'add_to_rakusuke', by: 'u', at: '2026-10-21T02:00:00Z' },
  }),
];

/** 夕方 (JST 17:30) にする。夕方の注意は 17〜18 時台だけ出す。 */
function atEvening() {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-21T17:30:00+09:00'));
}

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  latest = undefined;
  latestError = null;
  jobError = null;
  outJobError = null;
  applyPending = false;
  jobStatus = undefined;
  preview = undefined;
  outJob = undefined;
  jobCalls.length = 0;
  outJobCalls.length = 0;
  startCompare.mockReset();
  applyCompare.mockReset();
  startOutbound.mockReset();
  invalidate.mockClear();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  applyCompare.mockResolvedValue({
    applied: [{}],
    recompare_needed: true,
    moved_visit_count: 2,
    notified: 1,
  });
});

describe('差分の一覧', () => {
  it('まだ比べていなければそう出し、実績を記録するは押せない', () => {
    setLatest(null);
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-summary')).toHaveTextContent(
      'この月はまだカイポケと比べていません',
    );
    expect(screen.getByRole('button', { name: '実績を記録する' })).toBeDisabled();
    expect(screen.getByText('まだカイポケと比べていません')).toBeInTheDocument();
  });

  it('action ごとのボタン・種類を選ぶ・案内だけ・合わせ済み', () => {
    setLatest(run({ diffs: ALL_DIFFS, unconfirmed_days: 1 }));
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    const rows = screen.getAllByTestId('leave-diff');
    expect(rows).toHaveLength(ALL_DIFFS.length);
    const byKind = (k: string) => rows.filter((r) => r.getAttribute('data-kind') === k);
    expect(
      within(byKind('kaipoke_only')[0]!).getByRole('button', { name: 'らく助に入れる（公休）' }),
    ).toBeInTheDocument();
    expect(
      within(byKind('type_mismatch')[0]!).getByRole('button', {
        name: 'カイポケの種類に合わせる（有給）',
      }),
    ).toBeInTheDocument();
    const unknown = byKind('unknown_kind')[0]!;
    expect(unknown).toHaveTextContent('カイポケの予定：「休み」10:00〜11:00');
    expect(
      within(unknown)
        .getAllByRole('button')
        .map((b) => b.textContent),
    ).toEqual(['休みではない', '公休', '午前休', '午後休', '有給']);
    // action が無い差分は案内だけ (ボタンなし)
    const conflict = byKind('actual_conflict')[0]!;
    expect(within(conflict).queryByRole('button')).not.toBeInTheDocument();
    expect(conflict).toHaveTextContent('実績の訪問：利用者X 09:00〜10:00');
    expect(within(conflict).getByTestId('diff-guide')).toHaveTextContent(
      'カイポケで合わせてから、もう一度比べてください',
    );
    const notFound = byKind('staff_not_found')[0]!;
    expect(within(notFound).getByTestId('diff-guide')).toHaveTextContent(
      'らく助の職員の名前をそろえて比べ直してください',
    );
    expect(
      within(byKind('unconfirmed')[0]!).getByRole('button', {
        name: 'カイポケと比べ直す（月をまとめて取り直します）',
      }),
    ).toBeInTheDocument();
    // 合わせ済み
    const done = rows.find((r) => r.textContent?.includes('11/18'))!;
    expect(done).toHaveTextContent('合わせ済み');
    expect(within(done).queryByRole('button')).not.toBeInTheDocument();
    // 言葉は「合わせる」(「直す」「修正」は使わない)
    // (「取り直す」「比べ直す」は別の意味)
    expect(document.body.textContent).not.toMatch(/修正|(?<!取り|比べ)直す/);
  });

  it('1 件を合わせる・種類を選ぶ (content_hash つき)・同じ職員はまとめて', async () => {
    setLatest(run({ diffs: ALL_DIFFS }));
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'カイポケの種類に合わせる（有給）' }));
    expect(applyCompare).toHaveBeenLastCalledWith([
      { staff_id: S2, date: '2026-11-12', action: 'match_kaipoke_kind' },
    ]);
    const unknown = screen
      .getAllByTestId('leave-diff')
      .find((r) => r.dataset.kind === 'unknown_kind')!;
    fireEvent.click(within(unknown).getByRole('button', { name: '午後休' }));
    expect(applyCompare).toHaveBeenLastCalledWith([
      {
        staff_id: S2,
        date: '2026-11-13',
        action: 'choose_kind',
        content_hash: 'h-1',
        chosen_kind: 'pm_off',
      },
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'まとめて「らく助に入れる」（2 件）' }));
    expect(applyCompare).toHaveBeenLastCalledWith([
      { staff_id: S1, date: '2026-11-10', action: 'add_to_rakusuke' },
      { staff_id: S1, date: '2026-11-11', action: 'add_to_rakusuke' },
    ]);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('1 件を合わせました', {
        description:
          '訪問 2 件を「担当なし」へ移しました。もう一度「カイポケと比べる」を押して確かめてください',
      }),
    );
    // recompare_needed → 比べ直しの案内
    expect(screen.getByTestId('compare-summary')).toHaveTextContent(
      '合わせた結果を確かめるため、もう一度「カイポケと比べる」を押してください',
    );
  });

  it('合わせられなかったら detail をそのまま出す (409・422)', async () => {
    setLatest(run({ diffs: ALL_DIFFS }));
    applyCompare.mockRejectedValue(
      new ApiError('API 409', 409, {
        detail: 'この差分は最新の照合にありません。カイポケと比べ直してください',
      }),
    );
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'カイポケの種類に合わせる（有給）' }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'この差分は最新の照合にありません。カイポケと比べ直してください',
      ),
    );
  });
});

describe('実績を記録する', () => {
  it('未確認の日が 1 日でもあれば押せず、理由を出す', () => {
    setLatest(run({ diffs: [], unconfirmed_days: 1 }));
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByRole('button', { name: '実績を記録する' })).toBeDisabled();
    expect(
      screen.getByText('未確認の日が 1 日あります（その週を取り直してください）'),
    ).toBeInTheDocument();
  });

  it('押せない理由の決まり', () => {
    expect(
      recordActualBlockers({ run: null, changedSinceCompare: false, baselinePendingCount: 0 }),
    ).toEqual(['まだカイポケと比べていません']);
    expect(
      recordActualBlockers({
        run: run({ diffs: [diff({})], planned_send_count: 2, unconfirmed_days: 0 }),
        changedSinceCompare: true,
        baselinePendingCount: 3,
      }),
    ).toEqual([
      'カイポケとの差分が 1 件あります',
      'カイポケへ送る予定の休みが 2 件あります',
      '照合前の休みが 3 件あります',
      '比べた後に休みを変えました（もう一度比べてください）',
    ]);
    // 合わせ済みの差分は数えないが、比べ直しは要る
    expect(
      recordActualBlockers({
        run: run({ diffs: [diff({ resolved: { action: 'add_to_rakusuke' } })] }),
        changedSinceCompare: false,
        baselinePendingCount: 0,
      }),
    ).toEqual(['合わせた差分があります（もう一度比べて確かめてください）']);
    expect(
      recordActualBlockers({ run: run(), changedSinceCompare: false, baselinePendingCount: 0 }),
    ).toEqual([]);
  });

  it('条件がそろっていても、記録の API ができるまでは押せないと書く', () => {
    setLatest(run());
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByRole('button', { name: '実績を記録する' })).toBeDisabled();
    expect(screen.getByText(/実績の記録はこのあと使えるようになります/)).toBeInTheDocument();
    expect(screen.getByText('カイポケとの差分はありません。')).toBeInTheDocument();
  });

  it('比べた後に休みを変えたら案内する', () => {
    setLatest(run(), { changed_since_compare: true });
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-summary')).toHaveTextContent(
      '比べた後に休みを変えました。もう一度比べてください',
    );
  });
});

describe('カイポケと比べる', () => {
  it('始めると進み具合を読み (取り直し中→比べている→終わった)、終わったら 1 回だけ読み直す', async () => {
    atEvening();
    setLatest(null);
    startCompare.mockResolvedValue({
      job_id: 'job-1',
      status: 'pending',
      month: '2026-11',
      total: 6,
      evening_warning: '夕方は送信・取り込みと重なりやすい時間です。',
    });
    jobStatus = job('running');
    const { rerender } = render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'カイポケと比べる' }));
    expect(startCompare).toHaveBeenCalledWith('2026-11');
    await waitFor(() => expect(jobCalls[jobCalls.length - 1]).toBe('job-1'));
    expect(screen.getByTestId('compare-progress')).toHaveTextContent(
      'カイポケから取り直し中（2/6 週）',
    );
    expect(screen.getByRole('note')).toHaveTextContent(
      '夕方は送信・取り込みと重なりやすい時間です。',
    );
    // 動いている間は押せない
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).toBeDisabled();

    jobStatus = job('completed', { state: 'comparing' });
    rerender(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-progress')).toHaveTextContent('らく助の休みと比べています');
    expect(invalidate).not.toHaveBeenCalled();

    jobStatus = job('completed', { state: 'done', run: run({ diffs: [diff({})] }) });
    rerender(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    await waitFor(() =>
      expect(screen.getByTestId('compare-progress')).toHaveTextContent('比べ終わりました'),
    );
    expect(invalidate).toHaveBeenCalledTimes(1);
    rerender(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(invalidate).toHaveBeenCalledTimes(1);
    // 最新がまだ読めていなくても、ジョブの結果の差分を出す
    expect(screen.getAllByTestId('leave-diff')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).not.toBeDisabled();
  });

  it('開いたときに動いているジョブがあれば、その進み具合を読む', () => {
    setLatest(null, { job: job('running').job });
    jobStatus = job('running');
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(jobCalls).toContain('job-1');
    expect(screen.getByTestId('compare-progress')).toHaveTextContent('取り直し中（2/6 週）');
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).toBeDisabled();
  });

  it('止まったジョブは終わったものとして扱い、「カイポケと比べる」を押せる', () => {
    setLatest(null, { job: job('running').job });
    jobStatus = job('running', { job: { ...job('running').job, stale: true } });
    const { unmount } = render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-progress')).toHaveTextContent('止まっているようです');
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).not.toBeDisabled();
    expect(invalidate).toHaveBeenCalledTimes(1);
    unmount();
    // 最新の照合のジョブが止まっていれば、そもそも読みに行かない
    jobCalls.length = 0;
    setLatest(null, { job: { ...job('running').job, stale: true } });
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(jobCalls.every((id) => id === null)).toBe(true);
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).not.toBeDisabled();
  });

  it('進み具合を読めなかったらエラーを出し、押せるように戻す', () => {
    setLatest(null, { job: job('running').job });
    jobError = new Error('通信に失敗しました');
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-progress')).toHaveTextContent('進み具合を読めませんでした');
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).not.toBeDisabled();
  });

  it('照合の結果を読めなかったら「読めませんでした」と出す', () => {
    latestError = new Error('通信に失敗しました');
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-summary')).toHaveTextContent(
      '照合の結果を読めませんでした：通信に失敗しました',
    );
  });

  it('夕方の注意は 17〜18 時台だけ出す', () => {
    setLatest(null, { evening_warning: '夕方の注意' });
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-21T19:05:00+09:00'));
    const { unmount } = render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
    unmount();
    atEvening();
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByRole('note')).toHaveTextContent('夕方の注意');
  });

  it('合わせている途中 (読み直しが終わるまで) はボタンを押せない', () => {
    setLatest(run({ diffs: ALL_DIFFS }));
    applyPending = true;
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByRole('button', { name: 'カイポケの種類に合わせる（有給）' })).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'まとめて「らく助に入れる」（2 件）' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'カイポケと比べる' })).toBeDisabled();
  });

  it('比べられなかった・取り直せなかったら理由を出す', () => {
    setLatest(null, { job: { ...job('running').job } });
    jobStatus = job('completed', { state: 'compare_failed', compare_error: 'ValueError' });
    const { unmount } = render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-progress')).toHaveTextContent(
      '比べられませんでした：ValueError',
    );
    unmount();
    setLatest(null, { job: { ...job('running').job } });
    jobStatus = job('failed', {
      state: 'failed',
      job: { ...job('failed').job, error: 'カイポケにログインできませんでした' },
    });
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    expect(screen.getByTestId('compare-progress')).toHaveTextContent(
      'カイポケから取り直せませんでした：カイポケにログインできませんでした',
    );
  });

  it('ほかのジョブが動いていたら 409 の文言をそのまま出す・未確認の日の「比べ直す」でも始める', async () => {
    setLatest(run({ diffs: ALL_DIFFS }));
    startCompare.mockRejectedValue(
      new ApiError('API 409', 409, {
        detail: 'カイポケの処理が動いています。終わってから押してください',
      }),
    );
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    fireEvent.click(
      screen.getByRole('button', { name: 'カイポケと比べ直す（月をまとめて取り直します）' }),
    );
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'カイポケの処理が動いています。終わってから押してください',
      ),
    );
    expect(startCompare).toHaveBeenCalledWith('2026-11');
  });
});

describe('カイポケへ送る', () => {
  const PREVIEW: OutboundPreview = {
    month: '2026-11',
    last_compared_at: '2026-10-21T01:00:00Z',
    blocked_reason: null,
    items: [
      {
        staff_id: S1,
        staff_name: '職員A',
        date: '2026-11-10',
        leave_type: 'off',
        title: '休み',
        start: '09:00',
        end: '18:00',
        sendable: true,
        reason: null,
        record_id: null,
      },
      {
        staff_id: S2,
        staff_name: '職員B',
        date: '2026-11-15',
        leave_type: 'off',
        title: '休み',
        start: '09:30',
        end: '18:00',
        sendable: false,
        reason: '日曜はカイポケへ送れません',
        record_id: null,
      },
    ],
    sendable_count: 1,
    needs_action: [
      {
        staff_id: S2,
        staff_name: '職員B',
        date: '2026-11-20',
        rakusuke: null,
        sent: 'off',
        message: 'カイポケの公休を手で消してください',
      },
    ],
    baseline_count: 2,
    sent_count: 3,
    evening_warning: null,
  };

  async function openSend() {
    setLatest(run());
    render(<LeaveComparePanel month="2026-11" baselinePendingCount={0} />);
    fireEvent.click(screen.getByRole('button', { name: 'カイポケへ送る…' }));
    return screen.findByRole('dialog', { name: '2026年11月の休みをカイポケへ送る' });
  }

  it('送る前の一覧: 送れない行は理由・要対応・件数', async () => {
    preview = PREVIEW;
    const dlg = await openSend();
    const rows = within(dlg).getAllByTestId('outbound-item');
    expect(rows[0]).toHaveTextContent('職員A');
    expect(rows[0]).toHaveTextContent('休み 09:00〜18:00');
    expect(rows[0]).toHaveTextContent('送る');
    expect(rows[1]).toHaveTextContent('日曜はカイポケへ送れません');
    expect(dlg).toHaveTextContent('職員B 11/20（金）：カイポケの公休を手で消してください');
    expect(within(dlg).getByTestId('outbound-counts')).toHaveTextContent(
      '送れる休み 1 件・照合前 2 件・送信済み 3 件',
    );
  });

  it('照合がまだの月は送れない (理由を出す)', async () => {
    preview = { ...PREVIEW, blocked_reason: 'まだカイポケと比べていないので、休みは送りません' };
    const dlg = await openSend();
    expect(within(dlg).getByRole('alert')).toHaveTextContent(
      'まだカイポケと比べていないので、休みは送りません',
    );
    expect(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' })).toBeDisabled();
  });

  it('送ると送信中を出し、終わったら結果と送れなかった理由を出す', async () => {
    atEvening();
    preview = PREVIEW;
    startOutbound.mockResolvedValue({
      job_id: 'out-1',
      status: 'pending',
      month: '2026-11',
      count: 1,
      total: 1,
      needs_action: [],
      evening_warning: '夕方の注意',
    });
    outJob = {
      job: { ...job('running').job, id: 'out-1', op: 'leave-outbound', done: 0, total: 1 },
      count: 1,
      items: [],
      sent_count: 0,
      failed_count: 0,
      skipped_count: 0,
      needs_action: [],
    };
    const dlg = await openSend();
    await act(async () => {
      fireEvent.click(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' }));
    });
    expect(startOutbound).toHaveBeenCalledWith('2026-11');
    await waitFor(() =>
      expect(within(dlg).getByTestId('outbound-progress')).toHaveTextContent(
        'カイポケへ送信中（0/1 週）',
      ),
    );
    expect(dlg).toHaveTextContent('夕方の注意');
    expect(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' })).toBeDisabled();
  });

  it('送信の結果', async () => {
    preview = PREVIEW;
    startOutbound.mockResolvedValue({
      job_id: 'out-1',
      status: 'pending',
      month: '2026-11',
      count: 1,
      total: 1,
      needs_action: [],
      evening_warning: null,
    });
    outJob = {
      job: { ...job('completed').job, id: 'out-1', op: 'leave-outbound', done: 1, total: 1 },
      count: 2,
      items: [
        {
          staff_id: S1,
          staff_name: '職員A',
          date: '2026-11-10',
          leave_type: 'off',
          title: '休み',
          start: '09:00',
          end: '18:00',
          outcome: 'sent',
          external_key: 'k',
          reason: null,
        },
        {
          staff_id: S2,
          staff_name: '職員B',
          date: '2026-11-12',
          leave_type: 'off',
          title: '休み',
          start: '09:00',
          end: '18:00',
          outcome: 'failed',
          external_key: null,
          reason: 'カイポケの職員 ID が分かりません',
        },
      ],
      sent_count: 1,
      failed_count: 1,
      skipped_count: 0,
      needs_action: [],
    };
    const dlg = await openSend();
    await act(async () => {
      fireEvent.click(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' }));
    });
    const progress = await within(dlg).findByTestId('outbound-progress');
    expect(progress).toHaveTextContent('送れた 1 件・送れなかった 1 件・飛ばした 0 件');
    expect(progress).toHaveTextContent(
      '職員B 11/12（木）：送れませんでした（カイポケの職員 ID が分かりません）',
    );
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(1));
  });

  it('送り終わったら「閉じる」で閉じて開き直し、もう一度送れる', async () => {
    preview = PREVIEW;
    startOutbound.mockResolvedValue({
      job_id: 'out-1',
      status: 'pending',
      month: '2026-11',
      count: 1,
      total: 1,
      needs_action: [],
      evening_warning: null,
    });
    outJob = {
      job: { ...job('completed').job, id: 'out-1', op: 'leave-outbound', done: 1, total: 1 },
      count: 1,
      items: [],
      sent_count: 1,
      failed_count: 0,
      skipped_count: 0,
      needs_action: [],
    };
    let dlg = await openSend();
    await act(async () => {
      fireEvent.click(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' }));
    });
    await within(dlg).findByText(/送れた 1 件/);
    expect(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' })).toBeDisabled();
    fireEvent.click(within(dlg).getByRole('button', { name: '閉じる' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'カイポケへ送る…' }));
    dlg = await screen.findByRole('dialog', { name: '2026年11月の休みをカイポケへ送る' });
    expect(within(dlg).queryByText(/送れた 1 件/)).not.toBeInTheDocument();
    expect(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' })).not.toBeDisabled();
  });

  it('止まった送信・読めなかった送信は送信中にしない', async () => {
    preview = PREVIEW;
    startOutbound.mockResolvedValue({
      job_id: 'out-1',
      status: 'pending',
      month: '2026-11',
      count: 1,
      total: 1,
      needs_action: [],
      evening_warning: null,
    });
    outJobError = new Error('通信に失敗しました');
    const dlg = await openSend();
    await act(async () => {
      fireEvent.click(within(dlg).getByRole('button', { name: 'カイポケへ送る（1 件）' }));
    });
    expect(await within(dlg).findByRole('alert')).toHaveTextContent(
      '送信の進み具合を読めませんでした',
    );
    expect(dlg).not.toHaveTextContent('カイポケへ送信中');
  });
});
