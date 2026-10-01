/**
 * MakeWeekDialog — 「週を作る」ダイアログ (copy-week-design-2026-09-30.md / copy-week-mock.html)。
 *
 * 検証:
 *   1. 「固定訪問から生成」を選ぶと onChooseFixed だけが呼ばれる (コピー API は呼ばない)
 *   2. 前の週をコピー: 直近の週が初期選択・祝日の週は警告
 *   3. 確認画面: 写す先の週の明示・件数・内訳・既定値 (固定訪問に無い=写す / 補う=オフ /
 *      自動割当=オン)・1 件ずつ外すと preview を取り直す
 *   4. 実行ボタンで confirm 付きの実行 → onCopied
 *   5. 今週より前の週ではコピーを選べない
 *   6. 打刻のある週 (足すだけ) の注意書き
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

import type { CopyWeekPreview, CopyWeekSources, CopyWeekOptions } from '@/lib/queries/copy_week';

const state = vi.hoisted(() => ({
  sources: undefined as unknown,
  preview: undefined as unknown,
  previewCalls: [] as Array<unknown>,
  previewError: false,
  refetch: vi.fn(),
  mutateAsync: vi.fn(),
  reset: vi.fn(),
}));

vi.mock('@/lib/queries/copy_week', () => ({
  useCopyWeekSources: (_target: string, enabled: boolean) => ({
    data: enabled ? state.sources : undefined,
    isLoading: false,
  }),
  useCopyWeekPreview: (opts: unknown) => {
    state.previewCalls.push(opts);
    return {
      data: opts && !state.previewError ? state.preview : undefined,
      isFetching: false,
      isError: Boolean(opts) && state.previewError,
      error: state.previewError ? new Error('通信エラー') : null,
      refetch: state.refetch,
    };
  },
  useCopyWeek: () => ({
    mutateAsync: state.mutateAsync,
    reset: state.reset,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children, ...rest }: { children: React.ReactNode; [k: string]: unknown }) => (
    <div {...rest}>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}));

vi.mock('lucide-react', () => ({ Loader2: () => <span /> }));

import { MakeWeekDialog } from '../MakeWeekDialog';

const SOURCES: CopyWeekSources = {
  target_week_start: '2026-10-05',
  items: [
    {
      week_start: '2026-09-28',
      visits: 147,
      patients: 71,
      cancelled: 5,
      unplanned: 1,
      qr_arrivals: 43,
      holidays: [],
    },
    {
      week_start: '2026-09-21',
      visits: 128,
      patients: 69,
      cancelled: 35,
      unplanned: 6,
      qr_arrivals: 41,
      holidays: [
        { date: '2026-09-21', name: '敬老の日' },
        { date: '2026-09-23', name: '秋分の日' },
      ],
    },
  ],
};

function preview(overrides: Partial<CopyWeekPreview> = {}): CopyWeekPreview {
  return {
    source_week_start: '2026-09-28',
    target_week_start: '2026-10-05',
    mode: 'replace',
    copy_count: 147,
    fill_count: 0,
    patients: 71,
    by_weekday: [
      { weekday: 0, date: '2026-10-05', count: 27 },
      { weekday: 1, date: '2026-10-06', count: 26 },
    ],
    skipped: {
      cancelled: 5,
      unplanned: 1,
      special_extra: 0,
      inactive_patient: 0,
      user_excluded: 0,
      kept_conflict: 0,
      occupied_day: 0,
      past_day: 0,
      kept_same_day: 0,
      pair_partner: 0,
    },
    temp_course_count: 23,
    not_in_fixed: [
      {
        visit_ids: ['v-extra-1'],
        patient_id: 'p1',
        patient_name: '青木 一郎',
        weekday: 0,
        target_date: '2026-10-05',
        start_time: '11:30:00',
        end_time: '12:05:00',
        excluded: false,
      },
      {
        visit_ids: ['v-pair-a', 'v-pair-b'],
        patient_id: 'p2',
        patient_name: '井上 和子',
        weekday: 1,
        target_date: '2026-10-06',
        start_time: '16:40:00',
        end_time: '17:15:00',
        excluded: false,
      },
    ],
    missing_fixed: [
      {
        patient_id: 'p3',
        patient_name: '大野 正',
        weekday: 2,
        target_date: '2026-10-07',
        start_time: '09:00:00',
        end_time: '10:00:00',
        visits: 1,
      },
    ],
    missing_fixed_count: 30,
    missing_patients_without_visits: 10,
    existing: {
      total: 0,
      replace: 0,
      keep_checked_in: 0,
      keep_import: 0,
      keep_pinned: 0,
      keep_cancelled: 0,
      keep_past: 0,
      keep_other: 0,
    },
    needs_manual_staff: [],
    source_holidays: [],
    target_holidays: [],
    ...overrides,
  };
}

function renderDialog(props: Partial<React.ComponentProps<typeof MakeWeekDialog>> = {}) {
  const onChooseFixed = vi.fn();
  const onCopied = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <MakeWeekDialog
      open
      onOpenChange={onOpenChange}
      targetWeekStart="2026-10-05"
      isPastWeek={false}
      onChooseFixed={onChooseFixed}
      onCopied={onCopied}
      {...props}
    />,
  );
  return { onChooseFixed, onCopied, onOpenChange };
}

function lastPreviewOpts(): CopyWeekOptions | null {
  return (state.previewCalls[state.previewCalls.length - 1] ?? null) as CopyWeekOptions | null;
}

describe('MakeWeekDialog', () => {
  beforeEach(() => {
    state.sources = SOURCES;
    state.preview = preview();
    state.previewCalls = [];
    state.mutateAsync = vi.fn().mockResolvedValue({ created: 147 });
    state.previewError = false;
    state.refetch = vi.fn();
    state.reset = vi.fn();
  });

  it('固定訪問から生成 → onChooseFixed だけ (コピー API は呼ばない)', () => {
    const { onChooseFixed, onOpenChange } = renderDialog();
    expect(screen.getByText('10/5 の週を作る')).toBeInTheDocument();
    expect(screen.getByText('10/5(月)〜10/11(日)')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('make-week-how-fixed'));
    fireEvent.click(screen.getByText('固定訪問から生成する'));
    expect(onChooseFixed).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(state.mutateAsync).not.toHaveBeenCalled();
  });

  it('前の週をコピー: 直近の週が初期選択・祝日の週を選ぶと警告', () => {
    renderDialog();
    fireEvent.click(screen.getByTestId('make-week-next')); // 既定 = 前の週をコピー
    const rows = screen.getByTestId('make-week-sources');
    expect(rows).toHaveTextContent('9/28 の週');
    expect(rows).toHaveTextContent('直近');
    const radios = screen.getAllByRole('radio') as HTMLInputElement[];
    expect(radios[0]?.checked).toBe(true);
    expect(screen.queryByTestId('make-week-source-holiday-warning')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('make-week-source-2026-09-21'));
    expect(screen.getByTestId('make-week-source-holiday-warning')).toHaveTextContent(
      '9/21 敬老の日・9/23 秋分の日',
    );
  });

  it('確認画面: 写す先を明示・既定値・1 件ずつ外すと preview を取り直す → 実行', async () => {
    const { onCopied } = renderDialog();
    fireEvent.click(screen.getByTestId('make-week-next'));
    fireEvent.click(screen.getByTestId('make-week-next'));

    expect(lastPreviewOpts()).toEqual({
      sourceWeekStart: '2026-09-28',
      targetWeekStart: '2026-10-05',
      excludeVisitIds: [],
      fillFromFixed: false,
    });
    expect(screen.getByTestId('make-week-total')).toHaveTextContent('147');
    expect(screen.getByTestId('make-week-skipped')).toHaveTextContent('取消 5・予定外 1');
    expect(screen.getByTestId('make-week-by-weekday')).toHaveTextContent('10/5(月) 27 件');
    expect(screen.getByText(/臨時コースの訪問 23/)).toBeInTheDocument();
    // 既定値: 固定訪問に無い訪問 = 写す / 補う = オフ / 自動割当 = オン
    expect((screen.getByTestId('make-week-extras-all') as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId('make-week-fill') as HTMLInputElement).checked).toBe(false);
    expect((screen.getByTestId('make-week-assign') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/うち 10 名は訪問が 1 件もありません/)).toBeInTheDocument();

    // 2 名体制の行を外すと組ごと除外して取り直す
    fireEvent.click(screen.getByTestId('make-week-extra-v-pair-a'));
    expect(lastPreviewOpts()?.excludeVisitIds).toEqual(['v-pair-a', 'v-pair-b']);
    // 固定訪問から補う
    fireEvent.click(screen.getByTestId('make-week-fill'));
    expect(lastPreviewOpts()?.fillFromFixed).toBe(true);

    const run = screen.getByTestId('make-week-run');
    expect(run).toHaveTextContent('147 件を 10/5(月)〜10/11(日) の週へ写す');
    fireEvent.click(run);
    await waitFor(() => expect(state.mutateAsync).toHaveBeenCalledTimes(1));
    expect(state.mutateAsync).toHaveBeenCalledWith({
      sourceWeekStart: '2026-09-28',
      targetWeekStart: '2026-10-05',
      excludeVisitIds: ['v-pair-a', 'v-pair-b'],
      fillFromFixed: true,
      assignStaff: true,
      // 確認画面で見せた件数を一緒に送る (実行時と違えば結果に印が付く)
      expectedCounts: {
        copy_count: 147,
        fill_count: 0,
        replace_count: 0,
        needs_manual_count: 0,
      },
    });
    await waitFor(() => expect(onCopied).toHaveBeenCalledWith({ created: 147 }));
  });

  it('今週より前の週ではコピーを選べない', () => {
    renderDialog({ isPastWeek: true });
    expect(screen.getByTestId('make-week-how-copy')).toBeDisabled();
    expect(screen.getByText('固定訪問から生成する')).toBeInTheDocument();
  });

  it('打刻のある週は足すだけ・コピー前に戻せないことを出す', () => {
    state.preview = preview({
      mode: 'add_only',
      existing: { ...preview().existing, total: 12, keep_checked_in: 3, keep_other: 9 },
    });
    renderDialog();
    fireEvent.click(screen.getByTestId('make-week-next'));
    fireEvent.click(screen.getByTestId('make-week-next'));
    expect(screen.getByTestId('make-week-add-only')).toHaveTextContent(
      '打刻のある週は「コピー前に戻す」を使えません',
    );
    expect(screen.getByTestId('make-week-existing')).toHaveTextContent('すべて残します');
  });

  it('開くたびに前回の実行状態を消す (copyMut.reset)', () => {
    renderDialog();
    expect(state.reset).toHaveBeenCalled();
  });

  it('担当を手で付ける必要がある訪問を理由つきで出す', () => {
    state.preview = preview({
      needs_manual_staff: [
        {
          origin: 'copy',
          patient_id: 'p9',
          patient_name: '野口 フミ',
          target_date: '2026-10-05',
          start_time: '09:00:00',
          end_time: '09:45:00',
          reason: '前の週と同じ担当（佐々木）のコースになるため',
        },
      ],
      skipped: { ...preview().skipped, kept_same_day: 2, pair_partner: 1 },
    });
    renderDialog();
    fireEvent.click(screen.getByTestId('make-week-next'));
    fireEvent.click(screen.getByTestId('make-week-next'));
    const box = screen.getByTestId('make-week-needs-manual');
    expect(box).toHaveTextContent('担当を手で付ける必要がある訪問 1 件');
    expect(box).toHaveTextContent('野口 フミ');
    expect(box).toHaveTextContent('前の週と同じ担当（佐々木）のコースになるため');
    expect(screen.getByTestId('make-week-skipped')).toHaveTextContent(
      '同じ日に残す訪問があるため写さない 2',
    );
    expect(screen.getByTestId('make-week-skipped')).toHaveTextContent(
      '2 名体制の相方を写さないため 1',
    );
  });

  it('確認画面の読み込みに失敗したら止めて「もう一度読み込む」', () => {
    state.previewError = true;
    renderDialog();
    fireEvent.click(screen.getByTestId('make-week-next'));
    fireEvent.click(screen.getByTestId('make-week-next'));
    expect(screen.getByTestId('make-week-preview-error')).toHaveTextContent('通信エラー');
    fireEvent.click(screen.getByText('もう一度読み込む'));
    expect(state.refetch).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('make-week-run')).toBeDisabled();
  });
});
