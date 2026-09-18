/**
 * QR 印刷ビューの vitest テスト.
 *
 * A4 1 枚 = A5 横カード 2 面 (2026-09-18 お客様要望) の構造を検証する。
 * 1. RBAC: staff は /dashboard へリダイレクト
 * 2. 個別モード: シート 1 枚・カード 1 面 + QR レンダ
 * 3. 一括モード: 選択 3 名 → シート 2 枚・カード 3 面 + 件数カウント
 * C. カードに お問い合わせ先 + ロゴが出る / 「訪問介護」表記が無い
 * P. カードに拠点名を載せない (PO 判断)
 * D. 一括で全解除すると「🖨 印刷」が disabled
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

  it('2. 個別モード: シート 1 枚・カード 1 面 + QR レンダ', async () => {
    setupCommon({ mode: 'single', patientId: 'patient-0001' });
    render(<QrPrintPage />);

    await waitFor(() => {
      const sheets = screen.getAllByTestId('qrprint-sheet');
      expect(sheets).toHaveLength(1);
    });
    // 下半分は白紙 = カードは 1 面だけ。
    expect(screen.getAllByTestId('qrprint-card')).toHaveLength(1);
    expect(screen.getByText('山田 花子 様')).toBeInTheDocument();
    expect(screen.getByText('印刷 1枚')).toBeInTheDocument();
    // QR が URL を符号化している。
    const qr = screen.getByTestId('qrprint-qr');
    expect(qr.getAttribute('data-value')).toContain('/q/tok-abc123');
    // 発行日 + QR バージョン印字。
    expect(screen.getByText(/QR v1/)).toBeInTheDocument();
    // 1 面だけでも切り取り線は出る (A5 に切り分けてパウチするため)。
    expect(screen.getByText(/ここで切り取り/)).toBeInTheDocument();
  });

  it('3. 一括モード: 選択 3 名 → シート 2 枚・カード 3 面 + 件数カウント', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎' }),
      makePatient({ id: 'p3', code: 'P003', name: '鈴木 二郎' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    // 3 名 = A4 2 枚 (2 面 + 1 面)。
    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(2);
    });
    expect(screen.getAllByTestId('qrprint-card')).toHaveLength(3);
    // 初期は全選択 → コンパクト行は無し。
    expect(screen.queryAllByTestId('qrprint-selrow')).toHaveLength(0);
    expect(screen.getByText('表示 3名 / 印刷 3名（A4 2枚）')).toBeInTheDocument();
    // 拠点チップ + 全選択/全解除ボタンが出る。
    expect(screen.getByText('全拠点')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '全選択' })).toBeInTheDocument();
  });

  it('C. カード: お問い合わせ先 + ロゴが各面に出る / 「訪問介護」表記は無い', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    const { container } = render(<QrPrintPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-card')).toHaveLength(2);
    });

    // 連絡先ブロックはカードごとに 1 つ (2 名 = 2 つ)。
    expect(screen.getAllByText('お問い合わせ先')).toHaveLength(2);
    expect(screen.getAllByText('TEL 043-215-8991')).toHaveLength(2);
    expect(screen.getAllByText('対応時間 9:00〜18:00')).toHaveLength(2);
    expect(screen.getAllByText('対応日 日曜・年末年始休暇を除く')).toHaveLength(2);
    expect(screen.getAllByText('訪問看護ステーション よりより')).toHaveLength(2);
    // ロゴは装飾 (alt="") なので src で確認する。
    expect(container.querySelectorAll('img[src$="yoriyori-logo-h.svg"]')).toHaveLength(2);

    // 「訪問介護」→「訪問看護」に統一済み。CareFlow 表記も撤去。
    expect(container.textContent).not.toContain('訪問介護');
    expect(container.textContent).not.toContain('CareFlow');
  });

  it('P. カード: 拠点名は載せない (PO 判断 2026-09-18・ご利用者様には社内区分は不要)', async () => {
    setupCommon({ mode: 'bulk', patients: [makePatient()] });
    render(<QrPrintPage />);

    const card = await screen.findByTestId('qrprint-card');
    // 拠点名「稲毛」はカードの中には出ない (絞り込みチップ/未選択行には出てよい)。
    expect(card.textContent).not.toContain('稲毛');
  });

  it('D. 一括: 全解除すると「🖨 印刷」が disabled になる', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    // 初期は全選択 → 押せる。
    await waitFor(() => {
      expect(screen.getByTestId('qrprint-print')).toBeEnabled();
    });

    // 全解除 → 刷る中身が無いので塞がれる (押しても白紙が出るだけ)。
    fireEvent.click(screen.getByRole('button', { name: '全解除' }));
    await waitFor(() => {
      expect(screen.getByTestId('qrprint-print')).toBeDisabled();
    });
    expect(screen.queryAllByTestId('qrprint-card')).toHaveLength(0);
    expect(screen.getAllByTestId('qrprint-selrow')).toHaveLength(2);
  });

  it('B. 戻るボタン: 一括=患者マスタへ (status引き継ぎ)・個別=患者詳細へ (PO要望 2026-08-10)', async () => {
    // 一括モード (既定タブ=稼働中) → /patients (稼働中は既定なので ?status なし)
    setupCommon({ mode: 'bulk', patients: [makePatient()] });
    const { unmount } = render(<QrPrintPage />);
    await waitFor(() => {
      expect(screen.getByTestId('qrprint-back')).toHaveAttribute('href', '/patients');
    });
    expect(screen.getByTestId('qrprint-back')).toHaveTextContent('患者マスタへ戻る');

    // 解約済みタブへ切替 → 戻り先にも status が付く
    fireEvent.click(screen.getByTestId('qrprint-status-cancelled'));
    await waitFor(() => {
      expect(screen.getByTestId('qrprint-back')).toHaveAttribute(
        'href',
        '/patients?status=cancelled',
      );
    });
    unmount();

    // 個別モード (patient 指定) → その患者の詳細へ戻る
    setupCommon({ mode: 'single', patients: [makePatient()], patientId: 'patient-0001' });
    render(<QrPrintPage />);
    await waitFor(() => {
      expect(screen.getByTestId('qrprint-back')).toHaveAttribute('href', '/patients/patient-0001');
    });
    expect(screen.getByTestId('qrprint-back')).toHaveTextContent('患者詳細へ戻る');
  });

  it('S. 一括: ステータス絞り込み (既定=稼働中・切替で対象が変わる・PO要望 2026-08-10)', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子', status: 'active' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎', status: 'cancelled' }),
      makePatient({ id: 'p3', code: 'P003', name: '鈴木 二郎', status: 'active' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    // 既定は「稼働中」だけ → 2 名 (解約済みの佐藤は対象外)。
    await waitFor(() => {
      expect(screen.getByText('表示 2名 / 印刷 2名（A4 1枚）')).toBeInTheDocument();
    });
    // 件数バッジつきのステータスチップ。
    expect(screen.getByTestId('qrprint-status-active')).toHaveTextContent('稼働中 2');
    expect(screen.getByTestId('qrprint-status-cancelled')).toHaveTextContent('解約済み 1');

    // 「解約済み」タブへ切替 → 1 名だけになり選択も作り直される。
    fireEvent.click(screen.getByTestId('qrprint-status-cancelled'));
    await waitFor(() => {
      expect(screen.getByText('表示 1名 / 印刷 1名（A4 1枚）')).toBeInTheDocument();
    });

    // 「すべて」で 3 名 = A4 2 枚。
    fireEvent.click(screen.getByTestId('qrprint-status-all'));
    await waitFor(() => {
      expect(screen.getByText('表示 3名 / 印刷 3名（A4 2枚）')).toBeInTheDocument();
    });
  });

  it('4. 一括: 選択解除でカード→コンパクト行に変わり GET 対象 (= カード) が減る', async () => {
    const patients = [
      makePatient({ id: 'p1', code: 'P001', name: '山田 花子' }),
      makePatient({ id: 'p2', code: 'P002', name: '佐藤 一郎' }),
    ];
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    // 2 名 = A4 1 枚にカード 2 面。
    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-card')).toHaveLength(2);
    });
    expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(1);

    // 1 名のチェックを外す → カードは 1 面、未選択はコンパクト行に。
    const uncheck = screen.getByLabelText('佐藤 一郎 を印刷対象にする');
    fireEvent.click(uncheck);

    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-card')).toHaveLength(1);
    });
    expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(1);
    expect(screen.getAllByTestId('qrprint-selrow')).toHaveLength(1);
    expect(screen.getByText('表示 2名 / 印刷 1名（A4 1枚）')).toBeInTheDocument();
  });

  it('5. 一括: 上限超過で警告＋選択は上限まで頭打ち', async () => {
    const patients = Array.from({ length: 61 }, (_, i) =>
      makePatient({ id: `p${i}`, code: `P${i}`, name: `患者 ${i}` }),
    );
    setupCommon({ mode: 'bulk', patients });
    render(<QrPrintPage />);

    // 上限 (60) までしか選択されない → カード 60 面 = A4 30 枚、残り 1 はコンパクト行。
    await waitFor(() => {
      expect(screen.getAllByTestId('qrprint-card')).toHaveLength(60);
    });
    expect(screen.getAllByTestId('qrprint-sheet')).toHaveLength(30);
    expect(screen.getAllByTestId('qrprint-selrow')).toHaveLength(1);
    expect(screen.getByText('表示 61名 / 印刷 60名（A4 30枚）')).toBeInTheDocument();
    // 上限警告 (拠点で絞り込みを促す)。
    expect(screen.getByRole('alert')).toHaveTextContent(/印刷上限/);
  });
});
