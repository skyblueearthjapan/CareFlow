/**
 * PatientNgStaffSection vitest tests (patient-ng-staff-design.md §8-1).
 *
 * カバーするシナリオ:
 *   1. 一覧表示 (氏名 / メモ / 退職注記)
 *   2. 0 件メッセージ
 *   3. 追加: StaffCombobox 選択 + メモ → PUT (note は空文字 → null)
 *   4. 削除: DELETE 呼び出し
 *   5. staff ロールは閲覧のみ (追加 UI / 削除ボタンなし)
 *   6. 追加済みスタッフは Combobox 候補から除外される (excludeIds)
 *   7. loading 中は Skeleton (= 0 件メッセージを出さない)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// ─── Mock query hooks ─────────────────────────────────────────────────────────
vi.mock('@/lib/queries/patient_ng_staff', () => ({
  useNgStaffList: vi.fn(),
  useUpsertNgStaff: vi.fn(),
  useDeleteNgStaff: vi.fn(),
}));

// ─── Mock toast ───────────────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// ─── Mock StaffCombobox (radix popover を避けて素の select で代替) ─────────────
vi.mock('@/components/master/StaffCombobox', () => ({
  StaffCombobox: ({
    value,
    onChange,
    excludeIds,
    disabled,
  }: {
    value: string;
    onChange: (id: string) => void;
    excludeIds?: readonly string[];
    disabled?: boolean;
  }) => (
    <input
      data-testid="staff-combobox"
      data-exclude-ids={(excludeIds ?? []).join(',')}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

import { useSession } from 'next-auth/react';
import { useDeleteNgStaff, useNgStaffList, useUpsertNgStaff } from '@/lib/queries/patient_ng_staff';

import { PatientNgStaffSection } from '../PatientNgStaffSection';

const PATIENT_ID = '00000000-0000-0000-0000-000000000001';
const STAFF_A = '00000000-0000-0000-0000-0000000000a1';
const STAFF_B = '00000000-0000-0000-0000-0000000000b1';

function makeSession(role: 'admin' | 'manager' | 'staff') {
  return { data: { user: { role } }, status: 'authenticated' };
}

function makeMutation(fn: Mock = vi.fn().mockResolvedValue(undefined)) {
  return { mutateAsync: fn, isPending: false, isError: false };
}

function setupMocks(opts: {
  role?: 'admin' | 'manager' | 'staff';
  rows?: unknown[];
  isLoading?: boolean;
  upsertFn?: Mock;
  deleteFn?: Mock;
}) {
  (useSession as unknown as Mock).mockReturnValue(makeSession(opts.role ?? 'admin'));
  (useNgStaffList as unknown as Mock).mockReturnValue({
    data: opts.rows ?? [],
    isLoading: opts.isLoading ?? false,
    isError: false,
    error: null,
  });
  (useUpsertNgStaff as unknown as Mock).mockReturnValue(makeMutation(opts.upsertFn));
  (useDeleteNgStaff as unknown as Mock).mockReturnValue(makeMutation(opts.deleteFn));
}

const rowA = {
  staff_id: STAFF_A,
  staff_name: '田中 一郎',
  staff_is_deleted: false,
  note: 'ご家族からの申し出',
  decided_by_user_id: null,
  created_at: '2026-08-11T00:00:00Z',
};

const rowRetired = {
  staff_id: STAFF_B,
  staff_name: '退職 花子',
  staff_is_deleted: true,
  note: null,
  decided_by_user_id: null,
  created_at: '2026-08-11T00:00:00Z',
};

describe('PatientNgStaffSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. 一覧表示 (氏名 + メモ + 退職注記)', () => {
    setupMocks({ rows: [rowA, rowRetired] });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);

    expect(screen.getByTestId('patient-ng-staff-section')).toBeInTheDocument();
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
    expect(screen.getByText(/ご家族からの申し出/)).toBeInTheDocument();
    expect(screen.getByText('退職 花子')).toBeInTheDocument();
    expect(screen.getByText('(退職)')).toBeInTheDocument();
  });

  it('2. 0 件なら「NGスタッフの指定はありません」', () => {
    setupMocks({ rows: [] });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);
    expect(screen.getByTestId('patient-ng-staff-empty')).toBeInTheDocument();
  });

  it('3. 追加: スタッフ選択 + メモ入力 → PUT が呼ばれる', async () => {
    const upsertFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({ rows: [], upsertFn });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);

    // 未選択のうちは「追加」ボタンが無効.
    expect(screen.getByTestId('patient-ng-staff-add-button')).toBeDisabled();

    fireEvent.change(screen.getByTestId('staff-combobox'), { target: { value: STAFF_A } });
    fireEvent.change(screen.getByTestId('patient-ng-staff-note-input'), {
      target: { value: '相性が合わないため' },
    });
    fireEvent.click(screen.getByTestId('patient-ng-staff-add-button'));

    await waitFor(() => expect(upsertFn).toHaveBeenCalledTimes(1));
    expect(upsertFn).toHaveBeenCalledWith({
      patientId: PATIENT_ID,
      staffId: STAFF_A,
      note: '相性が合わないため',
    });
  });

  it('4. メモ未入力の追加は note=null で送る', async () => {
    const upsertFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({ rows: [], upsertFn });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);

    fireEvent.change(screen.getByTestId('staff-combobox'), { target: { value: STAFF_A } });
    fireEvent.click(screen.getByTestId('patient-ng-staff-add-button'));

    await waitFor(() => expect(upsertFn).toHaveBeenCalledTimes(1));
    expect(upsertFn).toHaveBeenCalledWith({
      patientId: PATIENT_ID,
      staffId: STAFF_A,
      note: null,
    });
  });

  it('5. 削除: DELETE が staff_id 付きで呼ばれる', async () => {
    const deleteFn = vi.fn().mockResolvedValue(undefined);
    setupMocks({ rows: [rowA], deleteFn });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);

    fireEvent.click(screen.getByTestId(`patient-ng-staff-delete-${STAFF_A}`));

    await waitFor(() => expect(deleteFn).toHaveBeenCalledTimes(1));
    expect(deleteFn).toHaveBeenCalledWith({ patientId: PATIENT_ID, staffId: STAFF_A });
  });

  it('6. staff ロールは閲覧のみ (追加 UI / 削除ボタンなし・一覧は見える)', () => {
    setupMocks({ role: 'staff', rows: [rowA] });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);

    expect(screen.getByText('閲覧のみ')).toBeInTheDocument();
    expect(screen.queryByTestId('patient-ng-staff-add-row')).not.toBeInTheDocument();
    expect(screen.queryByTestId(`patient-ng-staff-delete-${STAFF_A}`)).not.toBeInTheDocument();
    // 一覧自体は全ロールに見える (RBAC UI 統一).
    expect(screen.getByText('田中 一郎')).toBeInTheDocument();
  });

  it('7. readOnly=true なら admin でも編集 UI を出さない', () => {
    setupMocks({ role: 'admin', rows: [rowA] });
    render(<PatientNgStaffSection patientId={PATIENT_ID} readOnly />);
    expect(screen.queryByTestId('patient-ng-staff-add-row')).not.toBeInTheDocument();
  });

  it('8. 追加済みスタッフは Combobox 候補から除外される (excludeIds)', () => {
    setupMocks({ rows: [rowA, rowRetired] });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);
    expect(screen.getByTestId('staff-combobox')).toHaveAttribute(
      'data-exclude-ids',
      `${STAFF_A},${STAFF_B}`,
    );
  });

  it('9. loading 中は 0 件メッセージを出さない', () => {
    setupMocks({ rows: [], isLoading: true });
    render(<PatientNgStaffSection patientId={PATIENT_ID} />);
    expect(screen.queryByTestId('patient-ng-staff-empty')).not.toBeInTheDocument();
  });
});
