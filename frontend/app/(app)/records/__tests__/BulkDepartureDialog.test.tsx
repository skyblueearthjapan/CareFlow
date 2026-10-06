/**
 * 打刻履歴の「まとめて退出を入れる」（設計 `pc-actual-time-edit-design-2026-10-06.md` D3・Q1）。
 *
 * * 決め方: 到着＋予定の長さ（既定）／到着＋○分（編集できる）／予定の終わり。確認の一覧は
 *   選んだ決め方に合わせてその場で変わる。
 * * 実行は既存の PUT を 1 件ずつ順番に呼び、結果「N 件入れました ・ 失敗 M」と失敗の理由を残す。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { mockAdjust, mockToast } = vi.hoisted(() => ({
  mockAdjust: vi.fn(),
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('@/components/ui/sonner', () => ({ toast: mockToast }));
vi.mock('@/lib/queries/visit-history', () => ({
  useAdjustVisitActualTime: () => ({ mutateAsync: mockAdjust, isPending: false }),
}));

import { ApiError } from '@/lib/api-client';
import type { VisitHistoryRow } from '@/lib/queries/visit-history';

import { BulkDepartureDialog } from '../_components/BulkDepartureDialog';

function row(over: Partial<VisitHistoryRow>): VisitHistoryRow {
  return {
    visit_id: 'v-1',
    visit_date: '2026-10-03',
    patient_name: '鈴木 和子',
    planned_staff_name: '看護師A',
    start_time: '13:00:00',
    end_time: '13:35:00',
    // 04:13Z = JST 13:13
    arrival_at: '2026-10-03T04:13:00Z',
    departure_at: null,
    state: 'no_departure',
    adjust_allowed: true,
    ...over,
  } as VisitHistoryRow;
}

const ROWS = [
  row({}),
  // 05:05Z = JST 14:05・予定 30 分
  row({
    visit_id: 'v-2',
    patient_name: '中村 光子',
    start_time: '14:00:00',
    end_time: '14:30:00',
    arrival_at: '2026-10-03T05:05:00Z',
  }),
  // 01:06Z = JST 10:06・予定 45 分
  row({
    visit_id: 'v-3',
    patient_name: '森 義雄',
    start_time: '10:00:00',
    end_time: '10:45:00',
    arrival_at: '2026-10-03T01:06:00Z',
  }),
];

const time = (id: string) => screen.getByTestId(`history-bulk-time-${id}`).textContent;

beforeEach(() => {
  vi.clearAllMocks();
  mockAdjust.mockResolvedValue({});
});

describe('BulkDepartureDialog', () => {
  it('既定は到着＋予定の長さ。決め方を変えると確認の一覧がその場で変わる', () => {
    render(<BulkDepartureDialog rows={ROWS} onClose={vi.fn()} />);
    expect(screen.getByTestId('history-bulk-rule-len')).toBeChecked();
    expect([time('v-1'), time('v-2'), time('v-3')]).toEqual(['13:48', '14:35', '10:51']);

    // 到着＋○分（既定 35 分・編集できる）。
    fireEvent.change(screen.getByTestId('history-bulk-minutes'), { target: { value: '40' } });
    expect(screen.getByTestId('history-bulk-rule-min')).toBeChecked();
    expect([time('v-1'), time('v-2'), time('v-3')]).toEqual(['13:53', '14:45', '10:46']);

    fireEvent.click(screen.getByTestId('history-bulk-rule-end'));
    expect([time('v-1'), time('v-2'), time('v-3')]).toEqual(['13:35', '14:30', '10:45']);
    expect(screen.getByTestId('history-bulk-run')).toHaveTextContent('3 件に退出を入れる（実行）');
  });

  it('実行は 1 件ずつ順番に PUT し、失敗は理由つきで残す', async () => {
    let inFlight = 0;
    mockAdjust.mockImplementation(async ({ visitId }: { visitId: string }) => {
      inFlight += 1;
      expect(inFlight).toBe(1);
      await new Promise((r) => setTimeout(r, 1));
      inFlight -= 1;
      if (visitId === 'v-2') {
        throw new ApiError('API 422', 422, {
          detail: '退出は到着（14:05）より後の時刻にしてください',
        });
      }
      return {};
    });
    const onDone = vi.fn();
    render(<BulkDepartureDialog rows={ROWS} onClose={vi.fn()} onDone={onDone} />);
    fireEvent.click(screen.getByTestId('history-bulk-run'));

    await waitFor(() =>
      expect(screen.getByTestId('history-bulk-summary')).toHaveTextContent(
        '2 件入れました ・ 失敗 1',
      ),
    );
    expect(mockAdjust.mock.calls.map((c) => c[0])).toEqual([
      { visitId: 'v-1', kind: 'departure', time: '13:48' },
      { visitId: 'v-2', kind: 'departure', time: '14:35' },
      { visitId: 'v-3', kind: 'departure', time: '10:51' },
    ]);
    expect(screen.getByTestId('history-bulk-failure-v-2')).toHaveTextContent(
      '中村 光子（退出 14:35）: 退出は到着（14:05）より後の時刻にしてください',
    );
    expect(onDone).toHaveBeenCalledWith(['v-1', 'v-3']);
    expect(screen.getByRole('button', { name: '閉じる' })).toBeInTheDocument();
  });

  it('予定の無い訪問は送らずに理由を残す', async () => {
    render(
      <BulkDepartureDialog
        rows={[row({ visit_id: 'v-u', start_time: null, end_time: null, patient_name: '予定外' })]}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('history-bulk-run'));
    await waitFor(() =>
      expect(screen.getByTestId('history-bulk-summary')).toHaveTextContent(
        '0 件入れました ・ 失敗 1',
      ),
    );
    expect(mockAdjust).not.toHaveBeenCalled();
    expect(screen.getByTestId('history-bulk-failure-v-u')).toHaveTextContent(
      '予定が無いため決められません',
    );
  });
});
