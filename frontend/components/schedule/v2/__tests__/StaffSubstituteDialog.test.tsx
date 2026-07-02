/**
 * StaffSubstituteDialog — P3-① 当日欠勤の代替スタッフ提案 (FE) テスト.
 *
 * カバー:
 *   1. 検索 → visit + 候補ラジオが描画される.
 *   2. 候補ゼロ → 理由内訳 (N-6 日本語辞書) が描画される.
 *   3. 候補選択 → 適用 → 確認 → apply payload (substitutions) が正しい.
 *   4. register_absence チェックボックスが payload に反映される.
 *   5. 409 エラー → detail をトースト.
 *   6. zod: candidates レスポンス / apply リクエストの parse.
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { ApiError } from '@/lib/api-client';

// ─── 共通モック ─────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
  mocks: {
    applyMutateAsync: vi.fn(),
    candidatesResult: {
      data: undefined as unknown,
      isFetching: false,
      isError: false,
      error: null as unknown,
    },
    applyPending: false,
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  Loader2: () => <span data-testid="loader" />,
  Search: () => <span />,
  UserX: () => <span />,
  Users: () => <span />,
  X: () => <span />,
  Check: () => <span />,
}));

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children, ...rest }: React.HTMLAttributes<HTMLSpanElement>) => (
    <span {...rest}>{children}</span>
  ),
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    ...rest
  }: {
    children: React.ReactNode;
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
      checked={checked}
      onChange={(e) => onCheckedChange?.(e.target.checked)}
      {...rest}
    />
  ),
}));

// 注: vi.mock factory は hoist されるため top-level const を参照できない
// (TDZ). UUID はここでは literal で埋める (下の const と一致させる).
vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({
    data: [
      {
        id: '11111111-1111-4111-8111-111111111111',
        name: '欠勤 太郎',
        kana: 'けっきん',
        status: 'active',
      },
      {
        id: '22222222-2222-4222-8222-222222222222',
        name: '代替 花子',
        kana: 'だいたいはなこ',
        status: 'active',
      },
      {
        id: '33333333-3333-4333-8333-333333333333',
        name: '代替 次郎',
        kana: 'だいたいじろう',
        status: 'active',
      },
    ],
  }),
}));

vi.mock('@/lib/queries/staffSubstitute', () => ({
  useSubstituteCandidates: () => mocks.candidatesResult,
  useApplySubstitutions: () => ({
    mutateAsync: mocks.applyMutateAsync,
    isPending: mocks.applyPending,
  }),
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { StaffSubstituteDialog } from '../StaffSubstituteDialog';
import {
  substituteApplyRequestSchema,
  substituteCandidatesResponseSchema,
} from '@/lib/schemas/v2/staffSubstitute';

// ─── Fixtures ───────────────────────────────────────────────────────────────

const STAFF_ABSENT = '11111111-1111-4111-8111-111111111111';
const STAFF_SUB_A = '22222222-2222-4222-8222-222222222222';
const STAFF_SUB_B = '33333333-3333-4333-8333-333333333333';
const VISIT_1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const VISIT_2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const PATIENT_1 = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const PATIENT_2 = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';

function candidatesResponse() {
  return {
    absent_staff_id: STAFF_ABSENT,
    absent_staff_name: '欠勤 太郎',
    target_date: '2026-07-03',
    visits: [
      {
        visit_id: VISIT_1,
        patient_id: PATIENT_1,
        patient_name: '患者 A',
        start_time: '09:00',
        end_time: '10:00',
        sex_restriction: null,
        visit_group_id: null,
        required_staff_count: 1,
        is_secondary: false,
        candidates: [
          {
            staff_id: STAFF_SUB_A,
            staff_name: '代替 花子',
            staff_sex: 'female',
            score: 88.0,
            score_breakdown: {
              continuity_bonus: 100,
              travel_penalty: -12,
              load_penalty: -15,
            },
          },
          {
            staff_id: STAFF_SUB_B,
            staff_name: '代替 次郎',
            staff_sex: 'male',
            score: 20.0,
            score_breakdown: { continuity_bonus: 0, travel_penalty: -5, load_penalty: -10 },
          },
        ],
        no_candidate_reasons: [],
      },
      {
        visit_id: VISIT_2,
        patient_id: PATIENT_2,
        patient_name: '患者 B',
        start_time: '11:00',
        end_time: '12:00',
        sex_restriction: 'female_only',
        visit_group_id: null,
        required_staff_count: 1,
        is_secondary: false,
        candidates: [],
        no_candidate_reasons: [
          { code: 'time_conflict', count: 2 },
          { code: 'sex_mismatch', count: 1 },
        ],
      },
    ],
  };
}

function openWithResults() {
  mocks.candidatesResult.data = candidatesResponse();
  render(<StaffSubstituteDialog open onClose={() => {}} />);
  // Step1: 欠勤スタッフを選び「検索」.
  fireEvent.change(screen.getByTestId('staff-substitute-absent-select'), {
    target: { value: STAFF_ABSENT },
  });
  fireEvent.click(screen.getByTestId('staff-substitute-search-button'));
}

/**
 * 候補ラベルをクリックする。
 * ラジオの onChange は実装側で label onClick に集約されているため、
 * label 要素自体を click する (input を直接 click すると label onClick が
 * 発火しない jsdom の挙動に依存しないようにする)。
 */
function selectRadio(testid: string) {
  const label = screen.getByTestId(testid);
  fireEvent.click(label);
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('StaffSubstituteDialog', () => {
  beforeEach(() => {
    mocks.applyMutateAsync.mockReset();
    mocks.candidatesResult.data = undefined;
    mocks.candidatesResult.isFetching = false;
    mocks.candidatesResult.isError = false;
    mocks.candidatesResult.error = null;
    mocks.applyPending = false;
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mockToast.info.mockReset();
    mockToast.warning.mockReset();
  });

  it('1. 検索 → visit と候補ラジオが描画される', () => {
    openWithResults();
    expect(screen.getByTestId(`staff-substitute-visit-${VISIT_1}`)).toBeInTheDocument();
    expect(
      screen.getByTestId(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`),
    ).toHaveTextContent('代替 花子');
    // スコア内訳の短縮表記 (継続◎ / 移動+12分 / 当日3件).
    expect(
      screen.getByTestId(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`),
    ).toHaveTextContent('継続◎');
    expect(
      screen.getByTestId(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`),
    ).toHaveTextContent('移動+12分');
    expect(
      screen.getByTestId(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`),
    ).toHaveTextContent('当日3件');
  });

  it('2. 候補ゼロ → 理由内訳 (日本語辞書) が描画される', () => {
    openWithResults();
    const noCand = screen.getByTestId(`staff-substitute-no-candidates-${VISIT_2}`);
    expect(noCand).toHaveTextContent('時間衝突 2');
    expect(noCand).toHaveTextContent('性別条件 1');
  });

  it('3. 候補選択 → 適用 → 確認 → apply payload が正しい', async () => {
    mocks.applyMutateAsync.mockResolvedValue({ applied: [{}], warnings: [] });
    openWithResults();

    selectRadio(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`);
    // 適用ボタン (1 件).
    fireEvent.click(screen.getByTestId('staff-substitute-apply-button'));
    // 確認パネル → 差し替える.
    fireEvent.click(screen.getByTestId('staff-substitute-confirm-apply'));

    expect(mocks.applyMutateAsync).toHaveBeenCalledTimes(1);
    const payload = mocks.applyMutateAsync.mock.calls[0][0];
    expect(payload.absent_staff_id).toBe(STAFF_ABSENT);
    expect(payload.substitutions).toEqual([
      { visit_id: VISIT_1, substitute_staff_id: STAFF_SUB_A },
    ]);
    expect(payload.register_absence).toBe(false);
  });

  it('4. register_absence チェックで payload が true になる', async () => {
    mocks.applyMutateAsync.mockResolvedValue({ applied: [{}], warnings: [] });
    openWithResults();

    selectRadio(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_B}`);
    fireEvent.click(screen.getByTestId('staff-substitute-apply-button'));
    fireEvent.click(screen.getByTestId('staff-substitute-register-absence'));
    fireEvent.click(screen.getByTestId('staff-substitute-confirm-apply'));

    const payload = mocks.applyMutateAsync.mock.calls[0][0];
    expect(payload.register_absence).toBe(true);
    expect(payload.substitutions).toEqual([
      { visit_id: VISIT_1, substitute_staff_id: STAFF_SUB_B },
    ]);
  });

  it('5. 409 エラー → detail をトースト表示', async () => {
    mocks.applyMutateAsync.mockRejectedValue(
      new ApiError('conflict', 409, { detail: '対象の訪問は既に確定済みです' }),
    );
    openWithResults();

    selectRadio(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`);
    fireEvent.click(screen.getByTestId('staff-substitute-apply-button'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('staff-substitute-confirm-apply'));
      // mutateAsync の reject → catch の state 更新を流す.
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockToast.error).toHaveBeenCalledWith('対象の訪問は既に確定済みです');
  });

  it('6. 422 エラー → detail をトースト表示 (422 専用フォールバック)', async () => {
    mocks.applyMutateAsync.mockRejectedValue(
      new ApiError('unprocessable', 422, { detail: '時間衝突により差し替え不可です' }),
    );
    openWithResults();

    selectRadio(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`);
    fireEvent.click(screen.getByTestId('staff-substitute-apply-button'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('staff-substitute-confirm-apply'));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mockToast.error).toHaveBeenCalledWith('時間衝突により差し替え不可です');
  });

  it('7. 候補選択 → 同じ候補を再クリック → selections から外れ適用ボタンが 0 件', () => {
    openWithResults();

    // 1 回クリックで選択 → 適用ボタンが 1 件になる.
    selectRadio(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`);
    expect(screen.getByTestId('staff-substitute-apply-button')).toHaveTextContent('1');

    // 同じ候補を再クリックで解除 → selectedCount が 0 に戻り、ボタンが disabled かつ 0 件表示になる.
    selectRadio(`staff-substitute-candidate-${VISIT_1}-${STAFF_SUB_A}`);
    const applyBtn = screen.getByTestId('staff-substitute-apply-button');
    expect(applyBtn).toBeDisabled();
    expect(applyBtn).toHaveTextContent('0');
  });
});

describe('staffSubstitute zod', () => {
  it('candidates レスポンスを parse できる', () => {
    const parsed = substituteCandidatesResponseSchema.parse(candidatesResponse());
    expect(parsed.visits).toHaveLength(2);
    expect(parsed.visits[0].candidates[0].score_breakdown.continuity_bonus).toBe(100);
    expect(parsed.visits[1].no_candidate_reasons[0].code).toBe('time_conflict');
  });

  it('apply リクエストは substitutions が空だと reject する', () => {
    const bad = {
      absent_staff_id: STAFF_ABSENT,
      target_date: '2026-07-03',
      substitutions: [],
    };
    expect(() => substituteApplyRequestSchema.parse(bad)).toThrow();
  });

  it('apply リクエストの register_absence は既定 false', () => {
    const parsed = substituteApplyRequestSchema.parse({
      absent_staff_id: STAFF_ABSENT,
      target_date: '2026-07-03',
      substitutions: [{ visit_id: VISIT_1, substitute_staff_id: STAFF_SUB_A }],
    });
    expect(parsed.register_absence).toBe(false);
  });
});
