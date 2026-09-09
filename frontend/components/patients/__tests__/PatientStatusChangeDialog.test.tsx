/**
 * PatientStatusChangeDialog のユニットテスト
 * (`docs/plans/patient-status-schedule-design-2026-09-09.md` §7-6 FE-1)。
 *
 * 固定したいこと:
 *   1. 影響件数 (取消件数・週別・特別訪問週間・カイポケ週数) が出る。
 *   2. 特別訪問週間の選択は **既定が「残す」**（PO 決定 Q12・勝手に終了しない）。
 *   3.「明日から」を選ぶと `from_date` が翌日になる (JST)。
 *   4. **閉じても API が飛ばない**（教訓: 閉じたら API が飛ばない）。
 *   5. 確定で status-change に正しいボディが飛ぶ。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ─── Mock query hooks (QueryClientProvider を張らずに描画する) ────────────────
vi.mock('@/lib/queries/patients', () => ({
  usePatientStatusImpact: vi.fn(),
  useChangePatientStatus: vi.fn(),
}));

import { usePatientStatusImpact, useChangePatientStatus } from '@/lib/queries/patients';
import { statusImpactSchema, statusChangeResultSchema } from '@/lib/schemas/patientStatus';

import { PatientStatusChangeDialog } from '../PatientStatusChangeDialog';

const PID = '00000000-0000-0000-0000-000000000001';
const SPID = '00000000-0000-0000-0000-0000000000a1';

const impactDeactivate = statusImpactSchema.parse({
  patient_id: PID,
  current_status: 'active',
  to_status: 'admitted',
  from_date: '2026-09-09',
  direction: 'deactivate',
  visits: {
    total: 9,
    by_week: [
      { iso_year: 2026, iso_week: 38, count: 3, label: '9/14週' },
      { iso_year: 2026, iso_week: 39, count: 6, label: '9/21週' },
    ],
    by_source: { auto: 7, manual_week: 2 },
    pair_groups: 0,
    excluded: { checked_in: 1 },
  },
  special_period: {
    id: SPID,
    start_date: '2026-09-03',
    end_date: '2026-12-02',
    pool_marks: 25,
    placed_marks: 5,
    placed_future_visits: 2,
  },
  fixed_visit_rows: 3,
  pending_requests: 1,
  kaipoke_weeks: 3,
  regenerate: null,
});

const impactReactivate = statusImpactSchema.parse({
  patient_id: PID,
  current_status: 'admitted',
  to_status: 'active',
  from_date: '2026-09-09',
  direction: 'reactivate',
  visits: { total: 0, by_week: [], by_source: {}, pair_groups: 0, excluded: {} },
  special_period: null,
  fixed_visit_rows: 2,
  pending_requests: 0,
  kaipoke_weeks: 2,
  regenerate: {
    weeks: [
      { iso_year: 2026, iso_week: 39, count: 2, label: '9/21週' },
      { iso_year: 2026, iso_week: 40, count: 6, label: '9/28週' },
    ],
    total: 8,
  },
});

const patientFixture = {
  id: PID,
  code: 'P001',
  name: '小湊',
  status: 'admitted',
  created_at: '2026-09-01T00:00:00',
  updated_at: '2026-09-09T00:00:00',
};

const changeResult = statusChangeResultSchema.parse({
  patient: patientFixture,
  direction: 'deactivate',
  cancelled_visit_ids: [],
  cancelled_count: 9,
  special_period: { id: SPID, action: 'keep', cancelled_pool_marks: 0 },
  rejected_requests: 1,
  op_groups: [],
  regenerated: null,
  notification_count: 1,
});

/**
 * 特別訪問週間を「終了する」にしたときの BE 応答。
 * `visits.total` は ⭐ 配置分 (placed_future_visits=2) を **含んだ** 11 件で返る。
 * FE 側で足し算をしていないこと (二重計上しないこと) を固定する。
 */
const impactDeactivateEnd = statusImpactSchema.parse({
  ...impactDeactivate,
  visits: {
    ...impactDeactivate.visits,
    total: 11,
    by_week: [
      { iso_year: 2026, iso_week: 38, count: 5, label: '9/14週' },
      { iso_year: 2026, iso_week: 39, count: 6, label: '9/21週' },
    ],
  },
});

let mutateAsync: Mock;
let refetch: Mock;

function mockImpact(data: unknown, over: Record<string, unknown> = {}) {
  (usePatientStatusImpact as unknown as Mock).mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch,
    ...over,
  });
}

function renderDialog(over: Partial<React.ComponentProps<typeof PatientStatusChangeDialog>> = {}) {
  const props = {
    open: true,
    patientId: PID,
    patientName: '小湊',
    fromStatus: 'active' as const,
    toStatus: 'admitted' as const,
    onCancel: vi.fn(),
    onDone: vi.fn(),
    ...over,
  };
  render(<PatientStatusChangeDialog {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  // JST 2026-09-09 12:00 に固定 (今日=9/9・明日=9/10)。
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date('2026-09-09T03:00:00Z'));
  mutateAsync = vi.fn().mockResolvedValue(changeResult);
  refetch = vi.fn();
  (useChangePatientStatus as unknown as Mock).mockReturnValue({
    mutateAsync,
    isPending: false,
  });
  mockImpact(impactDeactivate);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('PatientStatusChangeDialog — 非稼働化 (deactivate)', () => {
  it('見出しと影響件数を表示する', () => {
    renderDialog();
    expect(screen.getByTestId('patient-status-headline')).toHaveTextContent(
      '小湊様を入院中にします',
    );
    expect(screen.getByTestId('patient-status-cancel-total')).toHaveTextContent(
      '取消する予定 9 件',
    );
    expect(screen.getByTestId('patient-status-impact')).toHaveTextContent('9/14週 3 件');
    expect(screen.getByTestId('patient-status-impact')).toHaveTextContent('9/21週 6 件');
    expect(screen.getByTestId('patient-status-excluded')).toHaveTextContent('打刻済み 1 件');
    expect(screen.getByTestId('patient-status-pending-requests')).toHaveTextContent(
      '未処理の申請 1 件',
    );
    expect(screen.getByTestId('patient-status-kaipoke')).toHaveTextContent('カイポケ送信対象 3 週');
  });

  it('特別訪問週間のブロックが出て、既定は「残す」', () => {
    renderDialog();
    const block = screen.getByTestId('patient-status-special-period');
    expect(block).toHaveTextContent('9/3〜12/2');
    expect(block).toHaveTextContent('○ 25 枚');
    expect(block).toHaveTextContent('配置済み 5 件');
    expect(screen.getByTestId('patient-status-special-keep')).toBeChecked();
    expect(screen.getByTestId('patient-status-special-end')).not.toBeChecked();
  });

  it('確定で status-change に from_date=今日 / special_period_action=keep が飛ぶ', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const props = renderDialog();

    await user.click(screen.getByTestId('patient-status-confirm'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      status: 'admitted',
      from_date: '2026-09-09',
      special_period_action: 'keep',
    });
    await waitFor(() => expect(props.onDone).toHaveBeenCalledWith(changeResult));
  });

  it('「明日から」を選ぶと from_date が翌日になる', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderDialog();

    await user.click(screen.getByTestId('patient-status-when-tomorrow'));
    await user.click(screen.getByTestId('patient-status-confirm'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0][0]).toMatchObject({ from_date: '2026-09-10' });
  });

  it('op-log の「戻る」では戻せないことを明記する', () => {
    renderDialog();
    expect(screen.getByTestId('patient-status-undo-note')).toHaveTextContent(
      '元に戻すには患者様のステータスを稼働中に戻してください',
    );
  });

  it('影響 GET には special_period_action が乗り、ラジオ切替で数え直す', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    // 選択に応じて BE が数え直した応答を返す。
    (usePatientStatusImpact as unknown as Mock).mockImplementation(
      (_id: unknown, _to: unknown, _from: unknown, opts?: { specialPeriodAction?: string }) => ({
        data: opts?.specialPeriodAction === 'end' ? impactDeactivateEnd : impactDeactivate,
        isLoading: false,
        isError: false,
        error: null,
        isFetching: false,
        refetch,
      }),
    );
    renderDialog();

    expect(usePatientStatusImpact).toHaveBeenLastCalledWith(PID, 'admitted', '2026-09-09', {
      enabled: true,
      specialPeriodAction: 'keep',
    });
    expect(screen.getByTestId('patient-status-cancel-total')).toHaveTextContent(
      '取消する予定 9 件',
    );

    await user.click(screen.getByTestId('patient-status-special-end'));

    expect(usePatientStatusImpact).toHaveBeenLastCalledWith(PID, 'admitted', '2026-09-09', {
      enabled: true,
      specialPeriodAction: 'end',
    });
    // BE の total をそのまま出す (FE で placed_future_visits を足さない)。
    expect(screen.getByTestId('patient-status-cancel-total')).toHaveTextContent(
      '取消する予定 11 件',
    );
    // 「終了する」のラベルには配置済みの今後の件数を出したままにする。
    expect(screen.getByTestId('patient-status-special-period')).toHaveTextContent(
      '配置済みの今後 2 件も取消',
    );
  });

  it('影響取得に失敗したら「再試行」で refetch する', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    mockImpact(undefined, { isError: true, error: new Error('boom') });
    renderDialog();

    expect(screen.getByTestId('patient-status-impact-error')).toHaveTextContent('boom');
    await user.click(screen.getByTestId('patient-status-impact-retry'));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('「終了する」を選ぶと special_period_action=end が飛ぶ', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderDialog();

    await user.click(screen.getByTestId('patient-status-special-end'));
    await user.click(screen.getByTestId('patient-status-confirm'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0][0]).toMatchObject({ special_period_action: 'end' });
  });

  it('閉じても API は飛ばない (やめる / Esc)', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const props = renderDialog();

    await user.click(screen.getByTestId('patient-status-cancel'));
    expect(props.onCancel).toHaveBeenCalled();
    expect(mutateAsync).not.toHaveBeenCalled();

    await user.keyboard('{Escape}');
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it('影響を取得中は確定ボタンを押せない', () => {
    mockImpact(undefined, { isLoading: true });
    renderDialog();
    expect(screen.getByTestId('patient-status-impact-loading')).toBeInTheDocument();
    expect(screen.getByTestId('patient-status-confirm')).toBeDisabled();
  });
});

describe('PatientStatusChangeDialog — 復帰 (reactivate)', () => {
  beforeEach(() => {
    mockImpact(impactReactivate);
  });

  it('週別の作成件数と再生成チェック (既定 ON) を出す', () => {
    renderDialog({ fromStatus: 'admitted', toStatus: 'active' });
    expect(screen.getByTestId('patient-status-headline')).toHaveTextContent(
      '小湊様を稼働中に戻します',
    );
    expect(screen.getByTestId('patient-status-regen-total')).toHaveTextContent('作る予定 8 件');
    expect(screen.getByTestId('patient-status-impact')).toHaveTextContent('9/21週 2 件');
    expect(screen.getByTestId('patient-status-regenerate')).toBeChecked();
    expect(screen.getByTestId('patient-status-special-hint')).toHaveTextContent('特別訪問週間');
    expect(screen.getByTestId('patient-status-confirm')).toHaveTextContent('稼働中に戻す');
  });

  it('確定で status=active / regenerate=true が飛ぶ', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderDialog({ fromStatus: 'admitted', toStatus: 'active' });

    await user.click(screen.getByTestId('patient-status-confirm'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync).toHaveBeenCalledWith({
      status: 'active',
      from_date: '2026-09-09',
      regenerate: true,
    });
  });

  it('チェックを外すと regenerate=false が飛ぶ', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderDialog({ fromStatus: 'admitted', toStatus: 'active' });

    await user.click(screen.getByTestId('patient-status-regenerate'));
    await user.click(screen.getByTestId('patient-status-confirm'));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(mutateAsync.mock.calls[0][0]).toMatchObject({ regenerate: false });
  });
});
