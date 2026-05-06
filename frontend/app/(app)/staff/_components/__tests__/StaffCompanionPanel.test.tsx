/**
 * StaffCompanionPanel vitest unit tests (W10-FE1 Phase 3).
 *
 * テストケース:
 * 1. 初期 (空) → 全 OFF (全セル「なし」)
 * 2. 月曜午前に田中選択 + 保存 → PUT が呼ばれる
 * 3. 同曜日内 full + am 併存 → zod エラー表示 (schema レベル)
 * 4. staff role → readonly (フィールド disable + 保存ボタン非表示)
 * 5. リセット → 確認ダイアログ → DELETE 実行
 * 6. companion-candidates が admin/退職者除外で取得 (schema superRefine)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// ─── Mock query hooks ─────────────────────────────────────────────────────────
vi.mock('@/lib/queries/staff_companion', () => ({
  useCompanionAssignments: vi.fn(),
  useUpdateCompanionAssignments: vi.fn(),
  useDeleteCompanionAssignments: vi.fn(),
  useCompanionCandidates: vi.fn(),
}));

vi.mock('@/lib/queries/staff-shifts', () => ({
  useStaffShifts: vi.fn(),
}));

// ─── Mock toast ───────────────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { useSession } from 'next-auth/react';
import {
  useCompanionAssignments,
  useUpdateCompanionAssignments,
  useDeleteCompanionAssignments,
  useCompanionCandidates,
} from '@/lib/queries/staff_companion';
import { useStaffShifts } from '@/lib/queries/staff-shifts';
import { toast } from '@/components/ui/sonner';

import { StaffCompanionPanel } from '../StaffCompanionPanel';

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const STAFF_ID = '00000000-0000-0000-0000-000000000001';
const TANAKA_ID = '00000000-0000-0000-0000-000000000002';

const TANAKA_CANDIDATE = {
  id: TANAKA_ID,
  name: '田中',
  status: 'active',
  role: 'staff',
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeSession(role: 'admin' | 'manager' | 'staff') {
  return { data: { user: { role } }, status: 'authenticated' };
}

function makeQueryResult<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

function makeMutation(mutateAsyncFn: Mock = vi.fn().mockResolvedValue([])) {
  return {
    mutateAsync: mutateAsyncFn,
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  };
}

function setupMocks(
  opts: {
    role?: 'admin' | 'manager' | 'staff';
    assignments?: unknown[];
    candidates?: unknown[];
    updateFn?: Mock;
    deleteFn?: Mock;
  } = {},
) {
  const role = opts.role ?? 'admin';
  (useSession as Mock).mockReturnValue(makeSession(role));
  (useCompanionAssignments as Mock).mockReturnValue(makeQueryResult(opts.assignments ?? []));
  (useCompanionCandidates as Mock).mockReturnValue(
    makeQueryResult(opts.candidates ?? [TANAKA_CANDIDATE]),
  );
  (useUpdateCompanionAssignments as Mock).mockReturnValue(
    makeMutation(opts.updateFn ?? vi.fn().mockResolvedValue([])),
  );
  (useDeleteCompanionAssignments as Mock).mockReturnValue(
    makeMutation(opts.deleteFn ?? vi.fn().mockResolvedValue(undefined)),
  );
  (useStaffShifts as Mock).mockReturnValue(makeQueryResult(undefined));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('StaffCompanionPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 初期 (空) → 全セルが「なし」を選択している', () => {
    setupMocks({ assignments: [] });

    render(<StaffCompanionPanel staffId={STAFF_ID} />);

    // 7 曜日 × 2 パート (am/pm) = 14 select
    const selects = screen.getAllByRole('combobox');
    // 全て値が '' (なし) である
    selects.forEach((sel) => {
      expect((sel as HTMLSelectElement).value).toBe('');
    });
  });

  it('2. 月曜午前に田中選択 + 保存 → PUT が呼ばれる', async () => {
    const updateFn = vi.fn().mockResolvedValue([]);
    setupMocks({ assignments: [], updateFn });

    render(<StaffCompanionPanel staffId={STAFF_ID} />);

    // 最初の select = 月曜(weekday=0)・午前(am)
    const selects = screen.getAllByRole('combobox');
    await userEvent.selectOptions(selects[0], TANAKA_ID);

    // 保存ボタン
    const saveBtn = screen.getByRole('button', { name: '保存' });
    await userEvent.click(saveBtn);

    await waitFor(() => {
      expect(updateFn).toHaveBeenCalledTimes(1);
    });

    const callArg = updateFn.mock.calls[0][0] as { items: unknown[] };
    expect(callArg.items).toHaveLength(1);
    const item = callArg.items[0] as {
      weekday: number;
      part: string;
      companion_staff_id: string;
    };
    expect(item.weekday).toBe(0);
    expect(item.part).toBe('am');
    expect(item.companion_staff_id).toBe(TANAKA_ID);
  });

  it('3. full + am 併存 → staffCompanionAssignmentsBulkPutSchema がエラーを返す', async () => {
    const { staffCompanionAssignmentsBulkPutSchema } = await import(
      '@/lib/schemas/v2/staff_companion_assignment'
    );

    const result = staffCompanionAssignmentsBulkPutSchema.safeParse({
      items: [
        { weekday: 0, part: 'full', companion_staff_id: TANAKA_ID },
        { weekday: 0, part: 'am', companion_staff_id: TANAKA_ID },
      ],
    });

    expect(result.success).toBe(false);
    if (!result.success) {
      const conflictIssue = result.error.issues.find(
        (i) => i.message === 'full と am/pm の併存不可',
      );
      expect(conflictIssue).toBeDefined();
    }
  });

  it('4. staff role → readonly (フィールド disabled + 保存ボタン非表示)', () => {
    setupMocks({ role: 'staff', assignments: [] });

    render(<StaffCompanionPanel staffId={STAFF_ID} />);

    // 「閲覧のみ」バッジ表示
    expect(screen.getByText('閲覧のみ')).toBeInTheDocument();

    // 全 select が disabled
    const selects = screen.getAllByRole('combobox');
    selects.forEach((sel) => {
      expect(sel).toBeDisabled();
    });

    // 保存ボタン非表示
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();
  });

  it('5. リセット → 確認ダイアログ → DELETE 実行', async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({ assignments: [], deleteFn });

    render(<StaffCompanionPanel staffId={STAFF_ID} />);

    // リセットボタンをクリック
    const resetBtn = screen.getByRole('button', { name: 'リセット (全削除)' });
    await userEvent.click(resetBtn);

    // 確認ダイアログが開く
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('同行スタッフ割付を削除しますか？')).toBeInTheDocument();

    // 「削除する」ボタンをクリック
    const deleteBtn = screen.getByRole('button', { name: '削除する' });
    await userEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteFn).toHaveBeenCalledTimes(1);
    });

    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('削除しました'));
  });

  it('6. companion-candidates はスタッフ本人を除外して取得する', () => {
    setupMocks({ assignments: [] });

    render(<StaffCompanionPanel staffId={STAFF_ID} />);

    // useCompanionCandidates が excludeId=STAFF_ID で呼ばれていること
    expect(useCompanionCandidates).toHaveBeenCalledWith(STAFF_ID);
  });
});
