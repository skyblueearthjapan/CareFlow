/**
 * BulkPoolInsertDialog テスト (W-2 FE).
 *
 * 検証:
 *   1. 開くと simulate が自動発火し、プレビューが表示される。
 *   2. 「見せる」2: 必須チェックボックス gating — 未チェックで適用ボタン disabled、
 *      チェックで enabled。
 *   3. 409 (state_token 不一致): エラー toast + 「再計算」ボタンで simulate 再実行。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    simulateAsync: vi.fn(),
    applyAsync: vi.fn(),
    simulateReset: vi.fn(),
    applyReset: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));

vi.mock('@/lib/queries/poolBulk', () => ({
  usePoolBulkSimulateMutation: () => ({
    mutateAsync: mocks.simulateAsync,
    reset: mocks.simulateReset,
    isPending: false,
  }),
  usePoolBulkApplyMutation: () => ({
    mutateAsync: mocks.applyAsync,
    reset: mocks.applyReset,
    isPending: false,
  }),
}));

vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [] }),
}));

vi.mock('@/lib/queries/fieldBoard', () => ({
  proposeWarningLabel: (code: string) => code,
}));

vi.mock('../PoolCandidateList', () => ({
  EXCLUDED_REASON_LABEL: {} as Record<string, string>,
}));

vi.mock('../ProposalWeekCalendar', () => ({
  ProposalWeekCalendar: () => <div data-testid="proposal-week-calendar" />,
}));

vi.mock('../../WeekdayScheduleCard', () => ({
  WeekdayScheduleCard: () => <div data-testid="weekday-schedule-card" />,
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    open,
    onOpenChange,
    children,
  }: {
    open: boolean;
    onOpenChange?: (o: boolean) => void;
    children: React.ReactNode;
  }) =>
    open ? (
      <div data-testid="dialog">
        {/* テスト用: backdrop クリック相当 (onOpenChange(false) を直接発火させる)。 */}
        <button
          data-testid="mock-dialog-open-change-false"
          onClick={() => onOpenChange?.(false)}
        />
        {children}
      </div>
    ) : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="dialog-content">{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: {
    children?: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} {...rest}>
      {children}
    </button>
  ),
}));

vi.mock('@/components/ui/checkbox', () => ({
  Checkbox: ({
    checked,
    onCheckedChange,
    ...rest
  }: {
    checked?: boolean;
    onCheckedChange?: (v: boolean) => void;
    [k: string]: unknown;
  }) => (
    <input
      type="checkbox"
      checked={!!checked}
      onChange={(e) => onCheckedChange?.(e.target.checked)}
      {...rest}
    />
  ),
}));

vi.mock('@/components/ui/tabs', () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children, ...rest }: { children: React.ReactNode; [k: string]: unknown }) => (
    <div {...rest}>{children}</div>
  ),
  AlertTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  CalendarRange: () => <span />,
  CheckCircle2: () => <span />,
  Layers: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  RefreshCw: () => <span />,
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

// ─── ヘルパー ────────────────────────────────────────────────────────────────

import { BulkPoolInsertDialog } from '../BulkPoolInsertDialog';
import { ApiError } from '@/lib/api-client';

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';
const OFFICE_ID_2 = '22222222-2222-4222-8222-222222222222';

const BASE_PROPS = {
  open: true,
  onClose: vi.fn(),
  isoYear: 2026,
  isoWeek: 27,
  officeId: OFFICE_ID,
  poolPatients: [
    { id: 'p-1', name: '患者 A', primary_office_id: OFFICE_ID },
    { id: 'p-2', name: '患者 B', primary_office_id: OFFICE_ID },
  ],
  offices: [
    { id: OFFICE_ID, name: '本店' },
    { id: OFFICE_ID_2, name: '都賀' },
  ],
} as const;

/** 1 枠投入できる最小 simulate レスポンス */
function makeSimulateResult(overrides: Record<string, unknown> = {}) {
  return {
    placements: [
      {
        seq: 1,
        patient_id: 'p-1',
        patient_name: '患者 A',
        weekday: 0,
        course_code: 'B',
        office_id: OFFICE_ID,
        start_time: '10:15:00',
        service_minutes: 35,
        delta_minutes: 4,
        warnings: [],
      },
    ],
    partial: [],
    unplaced: [],
    week_before_after: [],
    kpi: {
      placed_patients: 1,
      placed_slots: 1,
      travel_minutes_before: 100,
      travel_minutes_after: 110,
      travel_km_before: 10,
      travel_km_after: 11,
    },
    state_token: 'token-abc',
    ...overrides,
  };
}

/** capacity_full で投入不能 + 定員+1候補あり の unplaced エントリ */
function unplacedEntry(overcapCount: number) {
  return {
    patient_id: 'p-2',
    patient_name: '患者 B',
    reason: 'capacity_full',
    overcapacity_available_count: overcapCount,
  };
}

/** W-14: 時間起因 (no_gap 等) で投入不能の unplaced エントリ (超過候補なし)。 */
function unplacedTimeEntry(reason = 'no_gap') {
  return {
    patient_id: 'p-2',
    patient_name: '患者 B',
    reason,
    overcapacity_available_count: 0,
  };
}

// ─── テスト ──────────────────────────────────────────────────────────────────

describe('BulkPoolInsertDialog (W-2)', () => {
  beforeEach(() => {
    mocks.simulateAsync.mockReset();
    mocks.applyAsync.mockReset();
    mocks.simulateReset.mockReset();
    mocks.applyReset.mockReset();
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
    mockToast.info.mockReset();
  });

  it('開くと simulate が自動発火し、プレビューと適用ボタンが表示される', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(mocks.simulateAsync).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('bulk-pool-insert-apply-button')).toBeInTheDocument();
  });

  it('チェックボックス gating: 未チェックで適用ボタン disabled、チェックで enabled', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    const applyButton = screen.getByTestId('bulk-pool-insert-apply-button');
    // 未チェック: disabled
    expect(applyButton).toBeDisabled();

    // チェック → enabled
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    expect(applyButton).not.toBeDisabled();
  });

  it('409: エラー toast + 「再計算」ボタンで simulate を再実行する', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    mocks.applyAsync.mockRejectedValue(new ApiError(409, 'conflict'));
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(mocks.simulateAsync).toHaveBeenCalledTimes(1);

    // チェックして適用
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));

    // 409 → エラー toast + previewing に戻る
    await waitFor(() =>
      expect(mockToast.error).toHaveBeenCalledWith(
        'シミュレーション後にスケジュールが変更されました。再計算してください',
      ),
    );

    // 「再計算」ボタンが存在し、押すと simulate 再実行
    const recompute = screen.getByTestId('bulk-pool-insert-recompute-button');
    fireEvent.click(recompute);
    await waitFor(() => expect(mocks.simulateAsync).toHaveBeenCalledTimes(2));
  });

  it('A 案: overcapacity_available_count>=1 の投入不能行に「定員+1」バッジを表示する', async () => {
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(2)] }),
    );
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    // Badge モックは props を落とすため文言で存在を検証。
    expect(screen.getByText('定員+1名なら入る候補あり')).toBeInTheDocument();
  });

  it('A 案: overcapacity_available_count=0 ではバッジを表示しない', async () => {
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(0)] }),
    );
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(screen.queryByText('定員+1名なら入る候補あり')).not.toBeInTheDocument();
  });

  it('A 案: done 画面の患者名クリックで onOpenPatientDetail が呼ばれダイアログが閉じる', async () => {
    const onOpenPatientDetail = vi.fn();
    const onClose = vi.fn();
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(1)] }),
    );
    mocks.applyAsync.mockResolvedValue({
      applied_patients: 1,
      applied_slots: 1,
      warnings: [],
    });
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        onClose={onClose}
        onOpenPatientDetail={onOpenPatientDetail}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    // チェックして適用 → done 画面へ
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-done')).toBeInTheDocument(),
    );

    // done 画面の患者名クリック → ダイアログを閉じてから個別導線を呼ぶ (opts は未設定)。
    const patientButton = screen.getByTestId('bulk-pool-insert-done-patient-button');
    fireEvent.click(patientButton);
    expect(onClose).toHaveBeenCalled();
    // opts は undefined (患者名クリック = 通常導線; バッジクリックのみ autoOvercapacity を渡す)。
    expect(onOpenPatientDetail).toHaveBeenCalledWith('p-2', undefined);
  });

  // ── W-6: 拠点グループ化 ──────────────────────────────────────────────────

  it('混入修正: 単一拠点選択時、他拠点の患者は simulate に送られない', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        officeId={OFFICE_ID}
        poolPatients={[
          { id: 'p-1', name: '患者 A', primary_office_id: OFFICE_ID },
          { id: 'p-3', name: '患者 C', primary_office_id: OFFICE_ID_2 },
        ]}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    // 他拠点 (p-3) は patient_ids に含まれない。
    expect(mocks.simulateAsync).toHaveBeenCalledTimes(1);
    expect(mocks.simulateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ office_id: OFFICE_ID, patient_ids: ['p-1'] }),
    );
    // 「他拠点の患者 1名は対象外」の明示。
    expect(screen.getByTestId('bulk-pool-insert-other-office-note')).toHaveTextContent(
      '他拠点の患者 1名は対象外',
    );
  });

  it('拠点未設定の患者は simulate に送らず「拠点未設定」セクションへ分離する', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        officeId={OFFICE_ID}
        poolPatients={[
          { id: 'p-1', name: '患者 A', primary_office_id: OFFICE_ID },
          { id: 'p-9', name: '拠点なし 花子', primary_office_id: null },
        ]}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    // 拠点未設定セクションに患者名が出る。
    const noOffice = screen.getByTestId('bulk-pool-insert-no-office');
    expect(noOffice).toHaveTextContent('拠点未設定 1名');
    expect(noOffice).toHaveTextContent('拠点なし 花子');
    // simulate には拠点設定済みの p-1 のみ (p-9 は送らない)。
    expect(mocks.simulateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ patient_ids: ['p-1'] }),
    );
  });

  it('applying 中は onOpenChange(false) を受けても onClose が呼ばれない (busy ガード)', async () => {
    const onClose = vi.fn();
    // apply は永遠に pending のまま (applying 状態を保持させる)。
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    mocks.applyAsync.mockReturnValue(new Promise(() => {}));
    render(<BulkPoolInsertDialog {...BASE_PROPS} onClose={onClose} />);

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    // チェックして適用 → applying 状態へ。
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));

    // applying 中に Dialog の onOpenChange(false) を発火させる。
    fireEvent.click(screen.getByTestId('mock-dialog-open-change-false'));

    // busy ガードにより onClose は呼ばれないこと。
    expect(onClose).not.toHaveBeenCalled();
  });

  it('全拠点表示 (officeId=null): 拠点タブが出て、アクティブなタブのみ自動 simulate する', async () => {
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult());
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        officeId={null}
        poolPatients={[
          { id: 'p-1', name: '患者 A', primary_office_id: OFFICE_ID },
          { id: 'p-3', name: '患者 C', primary_office_id: OFFICE_ID_2 },
        ]}
      />,
    );

    // 両拠点のタブが表示される。
    expect(screen.getByTestId(`bulk-pool-insert-office-tab-${OFFICE_ID}`)).toBeInTheDocument();
    expect(screen.getByTestId(`bulk-pool-insert-office-tab-${OFFICE_ID_2}`)).toBeInTheDocument();

    // 先頭タブ (本店) のみ自動 simulate (全タブ一斉には走らせない)。
    await waitFor(() => expect(mocks.simulateAsync).toHaveBeenCalledTimes(1));
    expect(mocks.simulateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ office_id: OFFICE_ID, patient_ids: ['p-1'] }),
    );

    // 2 つ目のタブを開くと、そのタブの拠点で simulate が走る。
    fireEvent.click(screen.getByTestId(`bulk-pool-insert-office-tab-${OFFICE_ID_2}`));
    await waitFor(() => expect(mocks.simulateAsync).toHaveBeenCalledTimes(2));
    expect(mocks.simulateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ office_id: OFFICE_ID_2, patient_ids: ['p-3'] }),
    );
  });

  // ── W-5b: done 画面の OvercapacityBadge クリック + プレビューヒント ──────────

  it('done 画面: overcapCount>=1 の患者の OvercapacityBadge がクリック可能で autoOvercapacity:true が渡る', async () => {
    const onOpenPatientDetail = vi.fn();
    const onClose = vi.fn();
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(2)] }),
    );
    mocks.applyAsync.mockResolvedValue({
      applied_patients: 1,
      applied_slots: 1,
      warnings: [],
    });
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        onClose={onClose}
        onOpenPatientDetail={onOpenPatientDetail}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    // チェックして適用 → done 画面へ。
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-done')).toBeInTheDocument(),
    );

    // OvercapacityBadge ボタンをクリック → autoOvercapacity:true で呼ばれる。
    const badgeButton = screen.getByTestId('bulk-pool-insert-done-overcap-button');
    fireEvent.click(badgeButton);
    expect(onClose).toHaveBeenCalled();
    expect(onOpenPatientDetail).toHaveBeenCalledWith('p-2', { autoOvercapacity: true });
  });

  it('done 画面: overcapCount=0 の患者には OvercapacityBadge ボタンを表示しない', async () => {
    const onOpenPatientDetail = vi.fn();
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(0)] }),
    );
    mocks.applyAsync.mockResolvedValue({
      applied_patients: 1,
      applied_slots: 1,
      warnings: [],
    });
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        onOpenPatientDetail={onOpenPatientDetail}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-done')).toBeInTheDocument(),
    );

    expect(screen.queryByTestId('bulk-pool-insert-done-overcap-button')).not.toBeInTheDocument();
  });

  it('プレビュー: overcap エントリありでヒント行が表示される / なしで非表示', async () => {
    // overcap あり。
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(2)] }),
    );
    const { unmount } = render(<BulkPoolInsertDialog {...BASE_PROPS} />);
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('bulk-pool-insert-overcap-hint')).toBeInTheDocument();
    unmount();

    // overcap なし。
    mocks.simulateAsync.mockResolvedValue(makeSimulateResult({ unplaced: [unplacedEntry(0)] }));
    render(<BulkPoolInsertDialog {...BASE_PROPS} />);
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('bulk-pool-insert-overcap-hint')).not.toBeInTheDocument();
  });

  // ── W-14: 詰まり解消の橋渡し (autoUnblock) ────────────────────────────────

  it('W-14: done 画面の時間起因患者に「ずらせば入る手」ボタンが出て {autoUnblock:true} で呼ばれる', async () => {
    const onOpenPatientDetail = vi.fn();
    const onClose = vi.fn();
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedTimeEntry('no_gap')] }),
    );
    mocks.applyAsync.mockResolvedValue({
      applied_patients: 1,
      applied_slots: 1,
      warnings: [],
    });
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        onClose={onClose}
        onOpenPatientDetail={onOpenPatientDetail}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    // チェックして適用 → done 画面へ。
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-done')).toBeInTheDocument(),
    );

    // 「ずらせば入る手」ボタンをクリック → autoUnblock:true で呼ばれる。
    const unblockButton = screen.getByTestId('bulk-pool-insert-done-unblock-button');
    fireEvent.click(unblockButton);
    expect(onClose).toHaveBeenCalled();
    expect(onOpenPatientDetail).toHaveBeenCalledWith('p-2', { autoUnblock: true });
  });

  it('W-14: done 画面の定員起因のみ患者には「ずらせば入る手」ボタンを出さない', async () => {
    const onOpenPatientDetail = vi.fn();
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedEntry(2)] }),
    );
    mocks.applyAsync.mockResolvedValue({
      applied_patients: 1,
      applied_slots: 1,
      warnings: [],
    });
    render(
      <BulkPoolInsertDialog {...BASE_PROPS} onOpenPatientDetail={onOpenPatientDetail} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-done')).toBeInTheDocument(),
    );

    // 定員超過ボタンは出るが、詰まり解消ボタンは出ない。
    expect(screen.getByTestId('bulk-pool-insert-done-overcap-button')).toBeInTheDocument();
    expect(
      screen.queryByTestId('bulk-pool-insert-done-unblock-button'),
    ).not.toBeInTheDocument();
  });

  it('W-14: 両方該当患者 (no_gap + overcap) はプレビューで複合ヒントが出て、done 画面で overcap/unblock 両ボタンが揃う', async () => {
    const onOpenPatientDetail = vi.fn();
    const onClose = vi.fn();
    // reason=no_gap かつ overcapacity_available_count=2 → 時間起因でも超過候補も持つ
    const bothEntry = {
      patient_id: 'p-2',
      patient_name: '患者 B',
      reason: 'no_gap',
      overcapacity_available_count: 2,
    };
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [bothEntry] }),
    );
    mocks.applyAsync.mockResolvedValue({
      applied_patients: 1,
      applied_slots: 1,
      warnings: [],
    });
    render(
      <BulkPoolInsertDialog
        {...BASE_PROPS}
        onClose={onClose}
        onOpenPatientDetail={onOpenPatientDetail}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    // プレビュー段階: ヒント行が overcap+time の複合文言を表示する
    // (InsertListColumn の hasOvercapEntry && hasTimeBlockerEntry 分岐)。
    expect(screen.getByTestId('bulk-pool-insert-overcap-hint')).toHaveTextContent(
      '定員+1名の個別相談・ずらして入る手は、適用後にこの画面の患者名から探せます',
    );

    // 適用 → done 画面へ。
    fireEvent.click(screen.getByTestId('bulk-pool-insert-confirm-checkbox'));
    fireEvent.click(screen.getByTestId('bulk-pool-insert-apply-button'));
    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-done')).toBeInTheDocument(),
    );

    // done 画面: 両ボタンが揃う。
    expect(screen.getByTestId('bulk-pool-insert-done-overcap-button')).toBeInTheDocument();
    expect(screen.getByTestId('bulk-pool-insert-done-unblock-button')).toBeInTheDocument();
  });

  it('W-14: プレビューでは時間起因は情報バッジのみ (押せる done ボタンは出ない)', async () => {
    mocks.simulateAsync.mockResolvedValue(
      makeSimulateResult({ unplaced: [unplacedTimeEntry('no_gap')] }),
    );
    render(
      <BulkPoolInsertDialog {...BASE_PROPS} onOpenPatientDetail={vi.fn()} />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('bulk-pool-insert-preview')).toBeInTheDocument(),
    );

    // 情報バッジ (文言) はプレビューに出る。
    expect(screen.getByText('ずらせば入る手を探せます')).toBeInTheDocument();
    // 押せる done 用ボタンはプレビューには存在しない。
    expect(
      screen.queryByTestId('bulk-pool-insert-done-unblock-button'),
    ).not.toBeInTheDocument();
    // ヒント行に「ずらして入る手」の趣旨が統合されている。
    expect(screen.getByTestId('bulk-pool-insert-overcap-hint')).toHaveTextContent(
      'ずらして入る手',
    );
  });
});
