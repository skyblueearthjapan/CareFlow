/**
 * Patient detail page vitest unit tests (W37 Phase 3-D).
 *
 * テストケース:
 * 1. requires_multiple_staff=false → コース 1 列のみ (regression)
 * 2. requires_multiple_staff=true + slot 0/1 両方 → コース 1, コース 2 両表示
 * 3. requires_multiple_staff=true + slot 0 のみ → コース 2 = 「コース 2: 未設定」警告表示
 * 4. 編集ボタンクリックで編集画面遷移 (regression)
 * 5. requires_multiple_staff=true → ヘッダに「2 名対応」バッジ表示
 * 6. requires_multiple_staff=false → 「2 名対応」バッジ非表示
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// ─── Mock next/navigation ─────────────────────────────────────────────────────
const mockPush = vi.fn();
vi.mock('next/navigation', () => ({
  useParams: vi.fn(() => ({ id: 'patient-0001' })),
  useRouter: vi.fn(() => ({ push: mockPush })),
}));

// ─── Mock next/link ───────────────────────────────────────────────────────────
vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({
  useSession: vi.fn(),
}));

// ─── Mock patient query hooks ─────────────────────────────────────────────────
vi.mock('@/lib/queries/patients', () => ({
  usePatient: vi.fn(),
  usePatients: vi.fn(),
  useDeletePatient: vi.fn(),
}));

// ─── Mock patient_fixed_visits query hooks ─────────────────────────────────────
vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: vi.fn(),
  useUpdateFixedVisits: vi.fn(),
  useDeleteFixedVisits: vi.fn(),
  useApplyFromWeek: vi.fn(),
}));

// ─── Mock course_templates query ──────────────────────────────────────────────
vi.mock('@/lib/queries/course_templates', () => ({
  useCourseTemplates: vi.fn(),
}));

// ─── Mock toast ───────────────────────────────────────────────────────────────
vi.mock('@/components/ui/sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { useSession } from 'next-auth/react';
import { usePatient, usePatients, useDeletePatient } from '@/lib/queries/patients';
import {
  useFixedVisits,
  useUpdateFixedVisits,
  useDeleteFixedVisits,
  useApplyFromWeek,
} from '@/lib/queries/patient_fixed_visits';
import { useCourseTemplates } from '@/lib/queries/course_templates';

import PatientDetailPage from '../page';

// ─── Helpers ──────────────────────────────────────────────────────────────────

const PATIENT_ID = 'patient-0001';
const OFFICE_ID = '00000000-0000-0000-0000-000000000010';

const COURSE_TEMPLATES = [
  {
    id: 'aaaaaaaa-0000-0000-0000-000000000001',
    label: 'A',
    office_id: OFFICE_ID,
    capacity_mon: 5,
    capacity_tue: 5,
    capacity_wed: 5,
    capacity_thu: 5,
    capacity_fri: 5,
    capacity_sat: 0,
    capacity_sun: 0,
    notes: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
  },
  {
    id: 'bbbbbbbb-0000-0000-0000-000000000002',
    label: 'B',
    office_id: OFFICE_ID,
    capacity_mon: 3,
    capacity_tue: 3,
    capacity_wed: 3,
    capacity_thu: 3,
    capacity_fri: 3,
    capacity_sat: 0,
    capacity_sun: 0,
    notes: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
  },
];

function makePatient(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: PATIENT_ID,
    code: 'P001',
    name: 'テスト 患者',
    kana: 'テスト カンジャ',
    sex: 'male',
    status: 'active',
    address: null,
    lat: null,
    lng: null,
    insurance: 'kaigo',
    primary_office_id: OFFICE_ID,
    sex_restriction: null,
    special_weekly_pattern: false,
    requires_multiple_staff: false,
    weekly_pattern: null,
    note: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    deleted_at: null,
    ...overrides,
  };
}

function makeQueryResult<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

function makeMutation(mutateAsyncFn: Mock = vi.fn().mockResolvedValue(undefined)) {
  return { mutateAsync: mutateAsyncFn, isPending: false, isError: false };
}

function setupMocks(
  opts: {
    patientOverrides?: Partial<Record<string, unknown>>;
    fixedVisitReads?: unknown[];
    role?: 'admin' | 'manager' | 'staff';
  } = {},
) {
  const role = opts.role ?? 'admin';
  (useSession as Mock).mockReturnValue({
    data: { user: { role } },
    status: 'authenticated',
  });

  const patientRecord = makePatient(opts.patientOverrides ?? {});
  (usePatient as Mock).mockReturnValue(makeQueryResult(patientRecord));
  (usePatients as Mock).mockReturnValue(
    makeQueryResult({ items: [patientRecord], total: 1, page: 1, limit: 500, truncated: false }),
  );
  (useDeletePatient as Mock).mockReturnValue(makeMutation());

  (useFixedVisits as Mock).mockReturnValue(makeQueryResult(opts.fixedVisitReads ?? []));
  (useUpdateFixedVisits as Mock).mockReturnValue(makeMutation());
  (useDeleteFixedVisits as Mock).mockReturnValue(makeMutation());
  (useApplyFromWeek as Mock).mockReturnValue(makeMutation());
  (useCourseTemplates as Mock).mockReturnValue(makeQueryResult(COURSE_TEMPLATES));
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('PatientDetailPage — W37 Phase 3-D', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. requires_multiple_staff=false → コース 2 列なし・コース 1 のみ表示 (regression)', async () => {
    setupMocks({
      patientOverrides: { requires_multiple_staff: false },
      fixedVisitReads: [
        {
          id: 'fv-001',
          patient_id: PATIENT_ID,
          weekday: 0,
          start_time: '09:00',
          duration_min: 30,
          mode: 'normal',
          course_template_id: 'aaaaaaaa-0000-0000-0000-000000000001',
          slot_index: 0,
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
      ],
    });

    render(<PatientDetailPage />);

    // コース 1 セルはある (「コース: A」形式)
    await waitFor(() => {
      expect(screen.getByTestId('ro-course-0')).toHaveTextContent('コース: A');
    });

    // コース 2 セルはない
    expect(screen.queryByTestId('ro-course2-0')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ro-course2-missing-0')).not.toBeInTheDocument();
  });

  it('2. requires_multiple_staff=true + slot 0/1 両方 → コース 1 / コース 2 両表示', async () => {
    setupMocks({
      patientOverrides: { requires_multiple_staff: true },
      fixedVisitReads: [
        {
          id: 'fv-001',
          patient_id: PATIENT_ID,
          weekday: 0,
          start_time: '09:00',
          duration_min: 60,
          mode: 'normal',
          course_template_id: 'aaaaaaaa-0000-0000-0000-000000000001',
          slot_index: 0,
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
        {
          id: 'fv-002',
          patient_id: PATIENT_ID,
          weekday: 0,
          start_time: '09:00',
          duration_min: 60,
          mode: 'normal',
          course_template_id: 'bbbbbbbb-0000-0000-0000-000000000002',
          slot_index: 1,
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
      ],
    });

    render(<PatientDetailPage />);

    await waitFor(() => {
      expect(screen.getByTestId('ro-course1-0')).toHaveTextContent('コース 1: A');
      expect(screen.getByTestId('ro-course2-0')).toHaveTextContent('コース 2: B');
    });

    // 未設定警告は出ない
    expect(screen.queryByTestId('ro-course2-missing-0')).not.toBeInTheDocument();
  });

  it('3. requires_multiple_staff=true + slot 0 のみ → コース 2 = 未設定警告表示', async () => {
    setupMocks({
      patientOverrides: { requires_multiple_staff: true },
      fixedVisitReads: [
        {
          id: 'fv-001',
          patient_id: PATIENT_ID,
          weekday: 0,
          start_time: '09:00',
          duration_min: 30,
          mode: 'normal',
          course_template_id: 'aaaaaaaa-0000-0000-0000-000000000001',
          slot_index: 0,
          created_at: '2026-01-01T00:00:00',
          updated_at: '2026-01-01T00:00:00',
        },
      ],
    });

    render(<PatientDetailPage />);

    await waitFor(() => {
      expect(screen.getByTestId('ro-course1-0')).toHaveTextContent('コース 1: A');
      // コース 2 は「未設定」警告色表示
      expect(screen.getByTestId('ro-course2-missing-0')).toHaveTextContent('コース 2: 未設定');
    });

    // 正常なコース 2 セルはない
    expect(screen.queryByTestId('ro-course2-0')).not.toBeInTheDocument();
  });

  it('4. 編集ボタンクリックで編集画面へのリンクが正しい (regression)', () => {
    setupMocks({ patientOverrides: { requires_multiple_staff: false } });

    render(<PatientDetailPage />);

    const editLink = screen.getByRole('link', { name: '編集' });
    expect(editLink).toHaveAttribute('href', `/patients/${PATIENT_ID}/edit`);
  });

  it('5. requires_multiple_staff=true → ヘッダに「2 名対応」バッジ表示', () => {
    setupMocks({ patientOverrides: { requires_multiple_staff: true } });

    render(<PatientDetailPage />);

    expect(screen.getByTestId('badge-multiple-staff')).toBeInTheDocument();
    expect(screen.getByTestId('badge-multiple-staff')).toHaveTextContent('2 名対応');
  });

  it('6. requires_multiple_staff=false → 「2 名対応」バッジ非表示', () => {
    setupMocks({ patientOverrides: { requires_multiple_staff: false } });

    render(<PatientDetailPage />);

    expect(screen.queryByTestId('badge-multiple-staff')).not.toBeInTheDocument();
  });

  it('7. 全曜日 OFF (fixed visits 空) → 全曜日「訪問なし」表示', async () => {
    setupMocks({
      patientOverrides: { requires_multiple_staff: false },
      fixedVisitReads: [],
    });

    render(<PatientDetailPage />);

    await waitFor(() => {
      // ReadOnlyWeekGrid で 7 曜日すべて「訪問なし」
      const noVisitItems = screen.getAllByText('訪問なし');
      // normal / special の 2 タブ分、少なくとも 7 件以上 (通常タブのみ初期表示なら 7)
      expect(noVisitItems.length).toBeGreaterThanOrEqual(7);
    });
  });

  it('8. requires_multiple_staff=true でも 保存ボタンなし (readOnly 確認)', () => {
    setupMocks({
      patientOverrides: { requires_multiple_staff: true },
      role: 'admin',
    });

    render(<PatientDetailPage />);

    // readOnly=true のため保存ボタンは表示されない
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();
  });
});
