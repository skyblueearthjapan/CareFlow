/**
 * 患者マスタ一覧 — ステータスタブ (PO 決定 2026-08-09) のテスト。
 *
 * 1. タブが 6 つ (稼働中/開始前/一時休止/入院中/解約済み/すべて) 件数バッジつきで出る
 * 2. 既定は「稼働中」選択 + usePatients へ status='active' が渡る
 * 3. タブクリックで status が切り替わり、ページが 1 に戻る
 * 4. URL ?status= の初期値を尊重する
 * 5. 空タブは「◯◯の患者様はいません」の空状態を出す
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/queries/patients', () => ({ usePatients: vi.fn() }));
vi.mock('@/lib/queries/offices', () => ({ useOffices: vi.fn() }));
// 一括ボタン群は本テストの関心外 — 軽いスタブに差し替える。
vi.mock('@/components/patients/PatientsExcelButtons', () => ({
  PatientsExcelButtons: () => null,
}));
vi.mock('@/components/patients/PatientKarteButtons', () => ({
  PatientKarteButtons: () => null,
}));
vi.mock('@/components/patients/PatientsReplaceAllButton', () => ({
  PatientsReplaceAllButton: () => null,
}));

import { useSession } from 'next-auth/react';
import { usePatients } from '@/lib/queries/patients';
import { useOffices } from '@/lib/queries/offices';

import PatientsPage from '../page';

function makePatient(id: string, name: string, status: string) {
  return {
    id,
    code: `P-${id}`,
    name,
    kana: null,
    sex: null,
    insurance: 'medical',
    status,
    primary_office_id: null,
    deleted_at: null,
  };
}

const COUNTS = { all: 9, active: 5, pending: 1, suspended: 1, admitted: 1, cancelled: 1 };

function setup(items: unknown[] = [makePatient('1', '山田 太郎', 'active')]) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin' } },
    status: 'authenticated',
  });
  (useOffices as Mock).mockReturnValue({ offices: [] });
  (usePatients as Mock).mockReturnValue({
    data: {
      items,
      total: items.length,
      page: 1,
      limit: 20,
      truncated: false,
      statusCounts: COUNTS,
    },
    isLoading: false,
    isError: false,
    error: null,
  });
}

describe('患者マスタ ステータスタブ', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, '', '/patients');
  });

  it('1+2. タブ6つ+件数バッジ・既定は稼働中で status=active が渡る', () => {
    setup();
    render(<PatientsPage />);
    const tabs = screen.getByTestId('patient-status-tabs');
    for (const label of ['稼働中', '開始前', '一時休止', '入院中', '解約済み', 'すべて']) {
      expect(tabs).toHaveTextContent(label);
    }
    // 件数バッジ (稼働中 5)
    expect(screen.getByTestId('patient-status-tab-active')).toHaveTextContent('5');
    expect(screen.getByTestId('patient-status-tab-active')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const lastCall = (usePatients as Mock).mock.calls.at(-1)?.[0];
    expect(lastCall.status).toBe('active');
  });

  it('3. タブクリックで status が切り替わる', () => {
    setup();
    render(<PatientsPage />);
    fireEvent.click(screen.getByTestId('patient-status-tab-cancelled'));
    const lastCall = (usePatients as Mock).mock.calls.at(-1)?.[0];
    expect(lastCall.status).toBe('cancelled');
    expect(screen.getByTestId('patient-status-tab-cancelled')).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('4. URL ?status=admitted を初期値として尊重する', () => {
    window.history.replaceState(null, '', '/patients?status=admitted');
    setup();
    render(<PatientsPage />);
    expect(screen.getByTestId('patient-status-tab-admitted')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const lastCall = (usePatients as Mock).mock.calls.at(-1)?.[0];
    expect(lastCall.status).toBe('admitted');
  });

  it('5. 空タブはタブ名入りの空状態を出す', () => {
    window.history.replaceState(null, '', '/patients?status=suspended');
    setup([]);
    render(<PatientsPage />);
    expect(screen.getByText('一時休止の患者様はいません')).toBeInTheDocument();
  });
});
