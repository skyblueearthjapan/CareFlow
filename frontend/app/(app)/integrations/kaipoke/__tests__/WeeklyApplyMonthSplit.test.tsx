import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { CorrectionItem } from '@/lib/schemas/integration';

vi.mock('@/components/integrations/ReconcileReportButton', () => ({
  ReconcileReportButton: () => null,
}));

import { WeeklyApplyControls } from '../_components/WeeklyApplyControls';
import {
  buildMonthGroups,
  resolveItemDate,
  type WeeklyApplyVm,
} from '../_components/useWeeklyApply';

// 2026-11-30 (月) 〜 2026-12-06 (日): 11 月と 12 月にまたがる週。
const CROSS_WEEK = new Date(2026, 10, 30);
// 2026-12-07 (月) 〜 2026-12-13 (日): 12 月だけ。
const DEC_WEEK = new Date(2026, 11, 7);

let seq = 0;
function item(
  action: string,
  before: Record<string, unknown> | null,
  after: Record<string, unknown> | null,
  include = true,
): CorrectionItem {
  seq += 1;
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, '0')}`,
    sheet_id: '00000000-0000-4000-8000-999999999999',
    action,
    before,
    after,
    include,
    created_at: '2026-11-18T00:00:00Z',
    updated_at: '2026-11-18T00:00:00Z',
    patient_status: null,
    inactive_patient: false,
  };
}

describe('resolveItemDate', () => {
  it('delete/edit/date_change は before、add は after の日を週内の日付にする', () => {
    expect(resolveItemDate(item('delete', { date: '30' }, null), CROSS_WEEK)).toEqual(
      new Date(2026, 10, 30),
    );
    expect(resolveItemDate(item('add', null, { date: '2' }), CROSS_WEEK)).toEqual(
      new Date(2026, 11, 2),
    );
    expect(
      resolveItemDate(item('date_change', { date: '30' }, { date: '1' }), CROSS_WEEK, 'after'),
    ).toEqual(new Date(2026, 11, 1));
  });

  it('週内に無い日・数字でない日は null', () => {
    expect(resolveItemDate(item('add', null, { date: '15' }), CROSS_WEEK)).toBeNull();
    expect(resolveItemDate(item('add', null, { date: '' }), CROSS_WEEK)).toBeNull();
  });
});

describe('buildMonthGroups', () => {
  it('月をまたぐ週は月ごとに分けて、未送信・送信済み・境をまたぐ移動を数える', () => {
    const items = [
      item('edit', { date: '30' }, { date: '30' }),
      item('date_change', { date: '30' }, { date: '1' }),
      item('add', null, { date: '1' }),
      item('delete', { date: '3' }, null),
      item('edit', { date: '5' }, { date: '5' }, false),
    ];
    const groups = buildMonthGroups(items, CROSS_WEEK, new Date(2026, 10, 18));
    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({
      month: '2026-11',
      label: '11 月分（11/30）',
      pending: 1,
      blocked: 1,
      sent: 0,
      boundaryMoves: 1,
    });
    expect(groups[1]).toMatchObject({
      month: '2026-12',
      label: '12 月分（12/1〜12/6）',
      pending: 2,
      sent: 1,
      boundaryMoves: 0,
    });
  });

  it('今日以前の行は送れない行に数える', () => {
    const groups = buildMonthGroups(
      [item('add', null, { date: '30' }), item('add', null, { date: '1' })],
      CROSS_WEEK,
      new Date(2026, 10, 30),
    );
    expect(groups[0]).toMatchObject({ pending: 0, blocked: 1 });
    expect(groups[1]).toMatchObject({ pending: 1, blocked: 0 });
  });

  it('月をまたがない週は 1 つだけ', () => {
    const groups = buildMonthGroups(
      [item('add', null, { date: '8' })],
      DEC_WEEK,
      new Date(2026, 10, 18),
    );
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ month: '2026-12', pending: 1 });
  });
});

function vmStub(overrides: Partial<WeeklyApplyVm>): WeeklyApplyVm {
  const mutation = { isPending: false };
  return {
    busy: false,
    credentialsConfigured: true,
    weekStart: CROSS_WEEK,
    weekEnd: new Date(2026, 11, 6),
    month: '2026-11',
    label: '11/30（月）〜 12/6（日）',
    sheetId: 'sheet-1',
    summary: { total: 4 },
    confirm: null,
    setConfirm: vi.fn(),
    showDiffDetail: false,
    setShowDiffDetail: vi.fn(),
    confirmMonth: null,
    setConfirmMonth: vi.fn(),
    spansMonths: true,
    monthGroups: [],
    monthExpanded: () => true,
    setWeekStart: vi.fn(),
    expandStatus: { data: { expanded: true }, isLoading: false },
    expand: mutation,
    diffLocal: mutation,
    apply: mutation,
    schedule: { data: { rows: [] }, isLoading: false },
    itemsQuery: { isLoading: false },
    scheduleRows: [],
    items: [],
    total: 4,
    unresolved: 0,
    isExpanded: true,
    looksUnexpanded: false,
    resetDiff: vi.fn(),
    shiftWeek: vi.fn(),
    runExpand: vi.fn(),
    runDiff: vi.fn(),
    runApply: vi.fn(),
    ...overrides,
  } as unknown as WeeklyApplyVm;
}

describe('WeeklyApplyControls の ④', () => {
  it('月をまたぐ週は月ごとのボタンを出し、送り終えた月は押せない', () => {
    render(
      <WeeklyApplyControls
        vm={vmStub({
          monthGroups: [
            {
              month: '2026-11',
              label: '11 月分（11/30）',
              pending: 0,
              blocked: 0,
              sent: 24,
              boundaryMoves: 0,
            },
            {
              month: '2026-12',
              label: '12 月分（12/1〜12/6）',
              pending: 142,
              blocked: 0,
              sent: 0,
              boundaryMoves: 0,
            },
          ],
          monthExpanded: (m: string) => m !== '2026-12',
        })}
      />,
    );
    expect(screen.getByText('11 月分を本番反映')).toBeDisabled();
    expect(screen.getByText('12 月分を本番反映')).toBeEnabled();
    expect(screen.getByText('送信済み 24 件')).toBeInTheDocument();
    expect(screen.getByText('142 件')).toBeInTheDocument();
    expect(screen.getByText('12 月の展開がまだです')).toBeInTheDocument();
    expect(screen.queryByText('この週で本番反映')).not.toBeInTheDocument();
  });

  it('月をまたがない週は今までどおり 1 つのボタン', () => {
    render(<WeeklyApplyControls vm={vmStub({ spansMonths: false, weekStart: DEC_WEEK })} />);
    expect(screen.getByText('この週で本番反映')).toBeInTheDocument();
    expect(screen.queryByText(/月分を本番反映/)).not.toBeInTheDocument();
  });
});
