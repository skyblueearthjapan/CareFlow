/**
 * 患者マスタ一覧 — ステータスタブ (PO 決定 2026-08-09) のテスト。
 *
 * 1. タブが 6 つ (稼働中/開始前/一時休止/入院中/解約済み/すべて) 件数バッジつきで出る
 * 2. 既定は「稼働中」選択 + usePatients へ status='active' が渡る
 * 3. タブクリックで status が切り替わり、ページが 1 に戻る
 * 4. URL ?status= の初期値を尊重する
 * 5. 空タブは「◯◯の患者様はいません」の空状態を出す
 * 連絡先 (設計 2026-11-05): 管理者だけ「お知らせ」列と絞り込み
 */
import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/queries/patients', () => ({ usePatients: vi.fn() }));
vi.mock('@/lib/queries/offices', () => ({ useOffices: vi.fn() }));
vi.mock('@/lib/queries/patientContacts', () => ({ usePatientContactsSummary: vi.fn() }));
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
import { usePatientContactsSummary } from '@/lib/queries/patientContacts';

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

function sum(patient_id: string, p: Partial<Record<string, number>>) {
  return {
    patient_id,
    contact_count: 1,
    targets: 1,
    sms: 0,
    email: 0,
    no_means: 0,
    pending_consent: 0,
    ng: 0,
    ...p,
  };
}

const SUMMARY = [
  sum('s1', { contact_count: 2, targets: 2, sms: 2 }),
  sum('s2', { email: 1 }),
  sum('s3', { pending_consent: 1 }),
  sum('s4', { no_means: 1 }),
  sum('s5', { targets: 0, ng: 1 }),
];

const COUNTS = { all: 9, active: 5, pending: 1, suspended: 1, admitted: 1, cancelled: 1 };

function setup(items: unknown[] = [makePatient('1', '山田 太郎', 'active')]) {
  (useSession as Mock).mockReturnValue({
    data: { user: { role: 'admin' } },
    status: 'authenticated',
  });
  (useOffices as Mock).mockReturnValue({ offices: [] });
  (usePatientContactsSummary as Mock).mockReturnValue({ data: SUMMARY });
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

describe('患者マスタ 住所の札 (設計 2026-10-22)', () => {
  const INS = '千葉県千葉市花見川区こてはし台9-9-9';
  const rows = [
    {
      ...makePatient('1', '青木 一郎', 'active'),
      insurance_address: INS,
      visit_same_as_insurance: true,
      address: INS,
    },
    {
      ...makePatient('2', '山田 花子', 'active'),
      insurance_address: INS,
      visit_same_as_insurance: false,
      address: '千葉県千葉市稲毛区園生町99-9',
    },
    {
      ...makePatient('3', '佐藤 次郎', 'active'),
      insurance_address: null,
      visit_same_as_insurance: false,
      address: '千葉県千葉市美浜区磯辺9-9-9',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, '', '/patients');
  });

  it('管理者: 訪問先が別の方に「訪問先は別」、保険証の住所が空の方に「未記入」を名前の下に付ける', () => {
    setup(rows);
    render(<PatientsPage />);
    const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;

    expect(rowOf('青木 一郎')).not.toHaveTextContent('訪問先は別');
    expect(rowOf('青木 一郎')).not.toHaveTextContent('未記入');
    expect(rowOf('山田 花子')).toHaveTextContent('訪問先は別');
    expect(rowOf('佐藤 次郎')).toHaveTextContent('保険証の住所 未記入');
    expect(rowOf('佐藤 次郎')).not.toHaveTextContent('訪問先は別');
  });

  it('職員: 札を出さない', () => {
    setup(rows);
    (useSession as Mock).mockReturnValue({
      data: { user: { role: 'staff' } },
      status: 'authenticated',
    });
    render(<PatientsPage />);
    expect(screen.queryByTestId('badge-visit-separate')).not.toBeInTheDocument();
    expect(screen.queryByTestId('badge-insurance-missing')).not.toBeInTheDocument();
  });
});

describe('患者マスタ お知らせ列 (連絡先の設計 2026-11-05)', () => {
  const rows = [
    makePatient('s1', '見本 花子', 'active'),
    makePatient('s2', '見本 一郎', 'active'),
    makePatient('s3', '見本 和子', 'active'),
    makePatient('s4', '見本 次郎', 'active'),
    makePatient('s5', '見本 三郎', 'active'),
    makePatient('s6', '見本 四郎', 'active'),
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(null, '', '/patients');
  });

  it('管理者: 列の札を summary から出す', () => {
    setup(rows);
    render(<PatientsPage />);
    const rowOf = (name: string) => screen.getByText(name).closest('tr') as HTMLElement;
    expect(screen.getByRole('columnheader', { name: 'お知らせ' })).toBeInTheDocument();
    expect(rowOf('見本 花子')).toHaveTextContent('SMS 2 件');
    expect(rowOf('見本 一郎')).toHaveTextContent('メール');
    expect(rowOf('見本 和子')).toHaveTextContent('同意を確認中');
    expect(rowOf('見本 次郎')).toHaveTextContent('送り先なし');
    expect(rowOf('見本 三郎')).toHaveTextContent('NG 回答');
    // 連絡先が無い方 (summary に無い) も送り先なし
    expect(rowOf('見本 四郎')).toHaveTextContent('送り先なし');
    expect(usePatientContactsSummary).toHaveBeenCalledWith(true);
  });

  it('絞り込み: 同意を確認中 = その方だけ / 送り先なし = 送れる方を除く', () => {
    setup(rows);
    render(<PatientsPage />);
    fireEvent.change(screen.getByTestId('patient-notify-filter'), {
      target: { value: 'pending' },
    });
    let last = (usePatients as Mock).mock.calls.at(-1)?.[0];
    expect(last.idFilter).toEqual({ mode: 'include', ids: ['s3'] });

    fireEvent.change(screen.getByTestId('patient-notify-filter'), { target: { value: 'none' } });
    last = (usePatients as Mock).mock.calls.at(-1)?.[0];
    expect(last.idFilter).toEqual({ mode: 'exclude', ids: ['s1', 's2', 's3', 's5'] });
  });

  it('職員: 列も絞り込みも出さず、summary も取りに行かない', () => {
    setup(rows);
    (useSession as Mock).mockReturnValue({
      data: { user: { role: 'staff' } },
      status: 'authenticated',
    });
    render(<PatientsPage />);
    expect(screen.queryByRole('columnheader', { name: 'お知らせ' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('patient-notify-filter')).not.toBeInTheDocument();
    expect(usePatientContactsSummary).toHaveBeenCalledWith(false);
  });
});
