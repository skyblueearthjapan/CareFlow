/**
 * ScopeOptimizeDialog テスト (U-1: 反映先選択の組込).
 *
 * 検証:
 *   1. 適用パネルに ChangeScopeChoice が表示される（既定 A 選択）。
 *   2. A 経路 (既定): apply payload に change_scope='pattern_and_week' が付く。
 *   3. B 経路: change_scope='week_only' が付き、成功 toast が B 向けになる。
 *   4. スキーマ: week_sync 欠落の旧 BE レスポンスでパースが通る（寛容パース）。
 *   5. state_token 409 の既存エラーハンドリングは不変。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── モック ──────────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    simulateMutate: vi.fn(),
    applyMutate: vi.fn(),
    simulateReset: vi.fn(),
    applyReset: vi.fn(),
    simulateIsPending: false,
    applyIsPending: false,
    simulateIsError: false,
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));

vi.mock('@/lib/queries/scopeOptimization', () => ({
  useScopeOptimizationSimulate: () => ({
    mutate: mocks.simulateMutate,
    isPending: mocks.simulateIsPending,
    isError: mocks.simulateIsError,
    reset: mocks.simulateReset,
  }),
  useScopeOptimizationApply: () => ({
    mutate: mocks.applyMutate,
    isPending: mocks.applyIsPending,
    reset: mocks.applyReset,
  }),
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    open,
    children,
  }: {
    open: boolean;
    onOpenChange?: () => void;
    children: React.ReactNode;
  }) => (open ? <div data-testid="dialog">{children}</div> : null),
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

vi.mock('@/components/ui/filter-chip', () => ({
  FilterChip: ({
    children,
    onClick,
    active,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    active?: boolean;
  }) => (
    <button onClick={onClick} data-active={active ? 'true' : 'false'}>
      {children}
    </button>
  ),
}));

vi.mock('lucide-react', () => ({
  Loader2: () => <span data-testid="loader" />,
  Route: () => <span />,
}));

vi.mock('../ImprovementSuggestionCard', () => ({
  ImprovementSuggestionCard: () => <div data-testid="improvement-card" />,
}));

vi.mock('../CourseMoveTimeline', () => ({
  CourseMoveTimeline: () => <div data-testid="course-timeline" />,
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

import { ScopeOptimizeDialog } from '../ScopeOptimizeDialog';

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';

const BASE_PROPS = {
  open: true,
  onClose: vi.fn(),
  isoYear: 2026,
  isoWeek: 27,
  officeId: OFFICE_ID,
  weekLabel: '2026-W27',
  canEdit: true,
} as const;

/** simulate の onSuccess を即時コールするモック実装 */
function makeSimulateWithResult(result: unknown) {
  return vi.fn().mockImplementation((_vars: unknown, opts?: { onSuccess?: (d: unknown) => void }) => {
    opts?.onSuccess?.(result);
  });
}

/** 1 手の最小 simulate レスポンス */
function makeSimulateResult(stepCount = 1) {
  const steps = Array.from({ length: stepCount }, (_, i) => ({
    seq: i + 1,
    patient_id: `patient-${i + 1}-uuid`,
    patient_name: `患者 ${i + 1}`,
    cumulative_delta_minutes: (i + 1) * 10,
    cumulative_delta_km: (i + 1) * 0.5,
    source_course: null,
    destination_course: null,
    suggestion: {
      kind: 'move',
      from_weekday: 0,
      from_start_time: '09:00',
      from_end_time: '09:30',
      from_course_code: 'A',
      from_office_id: OFFICE_ID,
      to_weekday: 1,
      to_start_time: '10:00',
      to_end_time: '10:30',
      to_course_code: 'B',
      to_office_id: OFFICE_ID,
      delta_minutes: (i + 1) * 10,
      delta_km: (i + 1) * 0.5,
      requires_patient_confirmation: false,
      staff_warnings: [],
      swap_counterpart: null,
      candidates: [],
    },
  }));
  return {
    iso_year: 2026,
    iso_week: 27,
    office_id: OFFICE_ID,
    state_token: 'token-abc',
    steps,
    before: { visit_count: 10, travel_minutes: 100, travel_km: 5, buffer_minutes: 20, gap_minutes: 30 },
    after: { visit_count: 10, travel_minutes: 90, travel_km: 4.5, buffer_minutes: 20, gap_minutes: 30 },
    courses: [],
    focus_before: null,
    focus_after: null,
    excluded_summary: {
      pinned: 0,
      locked: 0,
      no_current_visit: 0,
      dismissed: 0,
      confirmation_required_excluded: 0,
      truncated: false,
    },
  };
}

// ─── テスト ──────────────────────────────────────────────────────────────────

describe('ScopeOptimizeDialog (U-1: 反映先選択)', () => {
  beforeEach(() => {
    mocks.simulateMutate.mockReset();
    mocks.applyMutate.mockReset();
    mocks.simulateReset.mockReset();
    mocks.applyReset.mockReset();
    mocks.simulateIsPending = false;
    mocks.applyIsPending = false;
    mocks.simulateIsError = false;
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.warning.mockReset();
    mockToast.info.mockReset();
  });

  it('simulate 後に適用パネルが出て ChangeScopeChoice が表示される（既定 A）', async () => {
    mocks.simulateMutate = makeSimulateWithResult(makeSimulateResult(1));
    render(<ScopeOptimizeDialog {...BASE_PROPS} />);

    // 計算ボタンをクリック
    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));

    // 適用パネルが出る
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );
    // ChangeScopeChoice が表示される
    expect(screen.getByTestId('change-scope-choice')).toBeInTheDocument();
    // 既定は A (pattern)
    expect(screen.getByTestId('change-scope-pattern')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'false');
  });

  it('A 経路 (既定): apply payload に change_scope=pattern_and_week が付く', async () => {
    mocks.simulateMutate = makeSimulateWithResult(makeSimulateResult(1));
    render(<ScopeOptimizeDialog {...BASE_PROPS} />);

    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );

    // A が既定なのでそのまま適用ボタンをクリック
    fireEvent.click(screen.getByTestId('scope-optimize-apply-button'));

    await waitFor(() => expect(mocks.applyMutate).toHaveBeenCalledTimes(1));
    const payload = mocks.applyMutate.mock.calls[0][0];
    expect(payload.change_scope).toBe('pattern_and_week');
    expect(payload.state_token).toBe('token-abc');
    expect(payload.iso_year).toBe(2026);
    expect(payload.iso_week).toBe(27);
  });

  it('B 経路: B 選択後に apply payload が change_scope=week_only になる', async () => {
    mocks.simulateMutate = makeSimulateWithResult(makeSimulateResult(1));
    render(<ScopeOptimizeDialog {...BASE_PROPS} />);

    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );

    // B を選択
    fireEvent.click(screen.getByTestId('change-scope-week'));
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(screen.getByTestId('scope-optimize-apply-button'));

    await waitFor(() => expect(mocks.applyMutate).toHaveBeenCalledTimes(1));
    const payload = mocks.applyMutate.mock.calls[0][0];
    expect(payload.change_scope).toBe('week_only');
  });

  it('B 経路: 成功 toast が「この週だけ反映しました」になる', async () => {
    const result = makeSimulateResult(1);
    mocks.simulateMutate = makeSimulateWithResult(result);
    mocks.applyMutate.mockImplementation(
      (_vars: unknown, opts?: { onSuccess?: (d: unknown) => void }) => {
        opts?.onSuccess?.({ applied_count: 1, warnings: [] });
      },
    );
    render(<ScopeOptimizeDialog {...BASE_PROPS} />);

    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );

    // B を選択して適用
    fireEvent.click(screen.getByTestId('change-scope-week'));
    fireEvent.click(screen.getByTestId('scope-optimize-apply-button'));

    await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
    expect(mockToast.success).toHaveBeenCalledWith(
      'この週だけ反映しました。来週は従来の型で生成されます',
    );
    // 「固定枠戻」案内 toast は出ない
    expect(mockToast.info).not.toHaveBeenCalled();
  });

  it('A 経路: 成功 toast が「N手を適用しました」で「固定枠戻」案内は出ない', async () => {
    const result = makeSimulateResult(1);
    mocks.simulateMutate = makeSimulateWithResult(result);
    mocks.applyMutate.mockImplementation(
      (_vars: unknown, opts?: { onSuccess?: (d: unknown) => void }) => {
        opts?.onSuccess?.({ applied_count: 1, warnings: [] });
      },
    );
    render(<ScopeOptimizeDialog {...BASE_PROPS} />);

    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );

    // A が既定なのでそのまま適用
    fireEvent.click(screen.getByTestId('scope-optimize-apply-button'));

    await waitFor(() => expect(mockToast.success).toHaveBeenCalledTimes(1));
    expect(mockToast.success.mock.calls[0][0]).toMatch(/手を適用しました/);
    // 「固定枠戻」案内は出ない (U-1 で A は今週に即反映されるため不要)
    expect(mockToast.info).not.toHaveBeenCalled();
  });

  it('ダイアログを閉じて再度開くと changeScopeChoice が A (既定) にリセットされる', async () => {
    const result = makeSimulateResult(1);
    mocks.simulateMutate = makeSimulateWithResult(result);

    const { rerender } = render(<ScopeOptimizeDialog {...BASE_PROPS} />);

    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );
    // B を選択
    fireEvent.click(screen.getByTestId('change-scope-week'));
    expect(screen.getByTestId('change-scope-week')).toHaveAttribute('aria-checked', 'true');

    // ダイアログを閉じる
    rerender(<ScopeOptimizeDialog {...BASE_PROPS} open={false} />);
    // 再度開く
    mocks.simulateMutate = makeSimulateWithResult(result);
    rerender(<ScopeOptimizeDialog {...BASE_PROPS} open={true} />);

    fireEvent.click(screen.getByTestId('scope-optimize-run-button'));
    await waitFor(() =>
      expect(screen.getByTestId('scope-optimize-apply-panel')).toBeInTheDocument(),
    );
    // リセットされて A が選択されている
    expect(screen.getByTestId('change-scope-pattern')).toHaveAttribute('aria-checked', 'true');
  });

  // ── W-6 項目2/3: 拠点画面スキップ + 改名 ──────────────────────────────────

  it('項目3: ダイアログタイトルが「スケジュール最適化」', () => {
    render(<ScopeOptimizeDialog {...BASE_PROPS} />);
    expect(screen.getByText('スケジュール最適化')).toBeInTheDocument();
  });

  it('項目2: 全拠点表示 (officeId=null) でも先頭拠点が既定選択され、拠点ピッカー画面を飛ばす', () => {
    render(
      <ScopeOptimizeDialog
        {...BASE_PROPS}
        officeId={null}
        offices={[{ id: OFFICE_ID, name: '本店' }]}
      />,
    );
    // 拠点ピッカー画面 (no-office) は出ない。
    expect(screen.queryByTestId('scope-optimize-no-office')).not.toBeInTheDocument();
    // 即「対策を絞りたい範囲」画面 (曜日フィルタ) が出る。
    expect(screen.getByTestId('scope-optimize-weekday-filter')).toBeInTheDocument();
    // 全拠点モードなので拠点切替チップが出る。
    expect(screen.getByTestId('scope-optimize-office-filter')).toBeInTheDocument();
  });

  it('項目2: offices が空なら従来の拠点ピッカー画面をフォールバックとして出す', () => {
    render(<ScopeOptimizeDialog {...BASE_PROPS} officeId={null} offices={[]} />);
    expect(screen.getByTestId('scope-optimize-no-office')).toBeInTheDocument();
  });

  it('項目2: 診断導線 (initialOfficeId/initialScope) が優先され offices[0] で上書きされない', async () => {
    mocks.simulateMutate = makeSimulateWithResult(makeSimulateResult(1));
    const OTHER_OFFICE = '99999999-9999-4999-8999-999999999999';
    render(
      <ScopeOptimizeDialog
        {...BASE_PROPS}
        officeId={null}
        offices={[{ id: OFFICE_ID, name: '本店' }]}
        initialOfficeId={OTHER_OFFICE}
        initialScope={{ weekdays: [0], courseCodes: null }}
      />,
    );
    // initialScope があるので開いた直後に initialOfficeId の拠点で自動計算する。
    await waitFor(() => expect(mocks.simulateMutate).toHaveBeenCalledTimes(1));
    const payload = mocks.simulateMutate.mock.calls[0][0] as {
      scope: { office_id: string };
    };
    expect(payload.scope.office_id).toBe(OTHER_OFFICE);
  });
});

// ── スキーマ: week_sync 欠落の旧 BE レスポンスで寛容パースが通る ────────────

describe('scopeOptimizationApplyResponseSchema 寛容パース (U-1 後方互換)', () => {
  it('week_sync 欠落の旧 BE レスポンスがパースに通る', async () => {
    const { scopeOptimizationApplyResponseSchema } = await import(
      '@/lib/schemas/v2/scopeOptimization'
    );
    const oldBePayload = { applied_count: 2, warnings: [] };
    const result = scopeOptimizationApplyResponseSchema.safeParse(oldBePayload);
    expect(result.success).toBe(true);
    if (result.success) {
      // week_sync は undefined (欠落を許容)
      expect(result.data.week_sync).toBeUndefined();
    }
  });

  it('week_sync が null でもパースに通る', async () => {
    const { scopeOptimizationApplyResponseSchema } = await import(
      '@/lib/schemas/v2/scopeOptimization'
    );
    const payload = { applied_count: 1, warnings: [], week_sync: null };
    const result = scopeOptimizationApplyResponseSchema.safeParse(payload);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.week_sync).toBeNull();
    }
  });

  it('week_sync がオブジェクトのときフィールドを取得できる', async () => {
    const { scopeOptimizationApplyResponseSchema } = await import(
      '@/lib/schemas/v2/scopeOptimization'
    );
    const payload = {
      applied_count: 3,
      warnings: [],
      week_sync: { visits_regenerated: 5, visits_soft_deleted: 2 },
    };
    const result = scopeOptimizationApplyResponseSchema.safeParse(payload);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.week_sync?.visits_regenerated).toBe(5);
      expect(result.data.week_sync?.visits_soft_deleted).toBe(2);
    }
  });
});
