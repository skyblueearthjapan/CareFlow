/**
 * QR 印刷ビュー (Phase 5-1) の vitest テスト.
 *
 * 1. RBAC: staff は /dashboard へリダイレクト
 * 2. 個別モード: 患者 1 名の A4 1 枚 + QR レンダ
 * 3. 一括モード: 表示中の患者ぶんシートが出る + 枚数カウント
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// ─── Mock next/navigation ─────────────────────────────────────────────────────
const mockReplace = vi.fn();
const mockGet = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: vi.fn(() => ({ replace: mockReplace, push: vi.fn() })),
  useSearchParams: vi.fn(() => ({ get: mockGet })),
}));

// ─── Mock next-auth ───────────────────────────────────────────────────────────
vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));

// ─── Mock query hooks ─────────────────────────────────────────────────────────
vi.mock('@/lib/queries/patients', () => ({ usePatients: vi.fn() }));
vi.mock('@/lib/queries/offices', () => ({ useOffices: vi.fn() }));
vi.mock('@/lib/queries/patientQr', () => ({ usePatientQr: vi.fn() }));

// ─── Mock qrcode.react (本物の SVG 生成は不要) ─────────────────────────────────
vi.mock('qrcode.react', () => ({
  QRCodeSVG: ({ value }: { value: string }) => <div data-testid="qrprint-qr" data-value={value} />,
}));

import { useSession } from 'next-auth/react';
import { usePatients } from '@/lib/queries/patients';
import { useOffices } from '@/lib/queries/offices';
import { usePatientQr } from '@/lib/queries/patientQr';

import QrPrintPage from '../page';

const OFFICE_ID = '00000000-0000-0000-0000-000000000010';

function makePatient(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'patient-0001',
    code: 'P001',
    name: '山田 花子',
    kana: null,
    sex: null,
    status: 'active',
    address: null,
    lat: null,
    lng: null,
    insurance: null,
    primary_office_id: OFFICE_ID,
    sex_restriction: null,
    special_weekly_pattern: null,
    requires_multiple_staff: false,
    weekly_pattern: null,
    note: null,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    deleted_at: null,
    ...overrides,
  };
}

function setupCommon(
  opts: {
    role?: 'admin' | 'manager' | 'staff';
    mode?: 'single' | 'bulk';
    patientId?: string;
    patients?: unknown[];
  } = {},
) {
  const role = opts.role ?? 'admin';
  (useSession as Mock).mockReturnValue({
    data: { user: { role }, accessToken: 't', refreshToken: 'r' },
    status: 'authenticated',
  });

  mockGet.mockImplementation((key: string) => {
    if (key === 'mode') return opts.mode ?? 'single';
    if (key === 'patient') return opts.patientId ?? '';
    return null;
  });

  const items = opts.patients ?? [makePatient()];
  (usePatients as Mock).mockReturnValue({
    data: { items, total: items.length, page: 1, limit: 500, truncated: false },
    isLoading: false,
    isError: false,
    error: null,
  });
  (useOffices as Mock).mockReturnValue({
    offices: [{ id: OFFICE_ID, name: '稲毛', address: null }],
    allOffices: [],
  });
  (usePatientQr as Mock).mockReturnValue({
    data: { token: 'tok-abc123', version: 1 },
    isLoading: false,
    isError: false,
  });
}

describe('QrPrintPage — Phase 5-1', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. staff は /dashboard へリダイレクト', () => {
    setupCommon({ role: 'staff' });
    render(<QrPrintPage />);
    expect(mockReplace).toHaveBeenCalledWith('/dashboard');
  });

  it('2. 個別モード: 患者 1 名の A4 1 枚 + QR レンダ', async () => {
    setupCommon({ mode: 'single', patientId: 'patient-0001' });
    render(<QrPrintPage />);

    await waitFor(() => {
      const sheets = screen.getAllByTestId('qrprint-sheet');
      expect(sheets).toHaveLength(1);
    });
    expect(screen.getByText('山田 花子 様')).toBeInTheDocument();
    expect(screen.getByText('印刷 1枚')).toBeInTheDocument();
    // QR が URL を符号化している。
    const qr = screen.getByTestId('qrprint-qr');
    expect(qr.getAttribute('data-value')).toContain('/q/tok-abc123');
    // 発行日 + QR バージョン印字。
    expect(screen.getByText(/QR v1/)).toBeInTheDocument();
  });

  it('3. 一括モード: 表示中の患者ぶんシート + 枚数カウント', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(2);
    });
    // 初期は全選択 → 2 枚とも full シート、コンパクト行は無し。
    expect(screen.queryAllByTestId('qrprint-selrow')).toHaveLength(0);
    // 初期状態は全選択 → 表示 2名 / 印刷 2枚。
    expect(screen.getByText('表示 2名 / 印刷 2枚')).toBeInTheDocument();
    // 拠点チップ + 全選択/全解除ボタンが出る。
    expect(screen.getByText('全拠点')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全選択' })).toBeInTheDocument();
  });

  it('4. 一括: 選択解除でシート→コンパクト行に変わり GET 対象 (= full シート) が減る', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(2);
    });

    // 1 名のチェックを外す → full シートは 1 枚、未選択はコンパクト行に。
    const uncheck = screen.getByLabelText('佐藤 一郎 を印刷対象にする');
    fireEvent.click(uncheck);

    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(1);
    });
    expect(screen.getAllByTestId('qrprint-selrow')).toHaveLength(1);
    expect(screen.getByText('表示 2名 / 印刷 1枚')).toBeInTheDocument();
  });

  it('5. 一括: 上限超過で警告＋選択は上限まで頭打ち', async () => {
    const patients = Array.from({ length: 61 }, (_, i) =>
      makePatient({ id: `p${i}`, code: `P${i}`, name: `患者 ${i}` }),
    );
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    // 上限 (60) までしか選択されない → full シートは 60、残り 1 はコンパクト行。
    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(60);
    });
    expect(screen.getAllByTestId('qrprint-selrow')).toHaveLength(1);
    expect(screen.getByText('表示 61名 / 印刷 60枚')).toBeInTheDocument();
    // 上限警告 (拠点で絞り込みを促す)。
    expect(screen.getByRole('alert')).toHaveTextContent(/印刷上限/);
  });
});
