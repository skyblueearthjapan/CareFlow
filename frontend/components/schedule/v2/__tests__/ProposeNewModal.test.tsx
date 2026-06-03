/**
 * ProposeNewModal — 親機 統合提案モーダル「＋新規提案」(StageA+C+B) 振る舞いテスト.
 *
 * カバー:
 *   候補ソース切替:
 *     - 既定 (プール) / 既存 / 新規 のタブ切替でフォーム表示が変わる。
 *     - 新規タブでカルテ項目 (氏名/コード/カナ/性別/保険) が出る。
 *   propose 呼び出し:
 *     - 「提案する」で useProposeSlots.mutate が iso_year/iso_week・住所付きで呼ばれる。
 *   カバレッジ表示 (StageC):
 *     - 「希望 週N日 → M/N日 提案可」+ per_day ○/× バッジ + fully_covered バッジ。
 *   採用 → 確定:
 *     - 既存: 採用 → PUT fixed-visits (useConfirmFixedVisits.mutate) のみ。
 *     - 新規: 採用 → 登録へ進む → 登録して確定 で POST patients (useCreatePatient)
 *       → PUT fixed-visits (useConfirmFixedVisits) の順で呼ばれる。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

import type {
  ProposeSlotsResponse,
  ProposeSlotItem,
  ProposeCoverage,
} from '@/lib/schemas/v2/propose_slots';
import type { PatientRead } from '@/lib/schemas/patient';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';

// ─── 共通モック ─────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    proposeMutate: vi.fn(),
    confirmMutate: vi.fn(),
    confirmMutateAsync: vi.fn(),
    createMutateAsync: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { accessToken: 'a', refreshToken: 'r' }, status: 'authenticated' }),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

// Radix portal / jsdom 回避: UI primitives を素通し化。
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

// Tabs mock: onValueChange を React context で TabsTrigger へ伝播し、
// クリックでタブ切替を実際に駆動できるようにする。
const TabsCtx = React.createContext<((v: string) => void) | undefined>(undefined);
vi.mock('@/components/ui/tabs', () => ({
  Tabs: ({
    children,
    onValueChange,
  }: {
    children: React.ReactNode;
    onValueChange?: (v: string) => void;
  }) => <TabsCtx.Provider value={onValueChange}>{children}</TabsCtx.Provider>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children, value }: { children: React.ReactNode; value: string }) => {
    const onValueChange = React.useContext(TabsCtx);
    return (
      <button data-testid={`tab-${value}`} onClick={() => onValueChange?.(value)}>
        {children}
      </button>
    );
  },
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
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

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
  AlertTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
}));

vi.mock('@/components/ui/input', () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

vi.mock('@/components/ui/label', () => ({
  Label: ({ children, ...rest }: React.LabelHTMLAttributes<HTMLLabelElement>) => (
    <label {...rest}>{children}</label>
  ),
}));

// Select → native select (jsdom で操作可能に)。SelectValue は placeholder 表示のみ。
vi.mock('@/components/ui/select', () => ({
  Select: ({
    children,
    value,
    onValueChange,
  }: {
    children: React.ReactNode;
    value?: string;
    onValueChange?: (v: string) => void;
  }) => (
    <select value={value ?? ''} onChange={(e) => onValueChange?.(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <option value={value}>{children}</option>
  ),
}));

// useQueries (course-templates 並列 fetch) は空配列を返す (course_template_id=null fallback)。
vi.mock('@tanstack/react-query', async () => {
  const actual =
    await vi.importActual<typeof import('@tanstack/react-query')>('@tanstack/react-query');
  return { ...actual, useQueries: () => [] };
});

vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

vi.mock('@/lib/queries/fieldBoard', async () => {
  const actual = await vi.importActual<typeof import('@/lib/queries/fieldBoard')>(
    '@/lib/queries/fieldBoard',
  );
  return { ...actual, useProposeSlots: vi.fn() };
});

vi.mock('@/lib/queries/patients', () => ({
  usePatients: vi.fn(),
  useCreatePatient: vi.fn(),
}));

vi.mock('@/lib/queries/propose_confirm', () => ({
  useConfirmFixedVisits: vi.fn(),
}));

vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: vi.fn(),
}));

import { useProposeSlots } from '@/lib/queries/fieldBoard';
import { usePatients, useCreatePatient } from '@/lib/queries/patients';
import { useConfirmFixedVisits } from '@/lib/queries/propose_confirm';
import { useFixedVisits } from '@/lib/queries/patient_fixed_visits';
import { ProposeNewModal } from '../ProposeNewModal';

const OFFICE_ID = '11111111-1111-1111-1111-111111111111';

function slot(over: Partial<ProposeSlotItem>): ProposeSlotItem {
  return {
    office_id: OFFICE_ID,
    office_name: '稲毛',
    weekday: 0,
    weekday_code: 'Mon',
    course_code: 'A',
    course_label: '稲A',
    staff_name: '佐藤 Ns',
    start_time: '10:00',
    end_time: '11:00',
    score: 90,
    reasons: [],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    ...over,
  } as ProposeSlotItem;
}

const monSlot = slot({ weekday: 0, weekday_code: 'Mon', start_time: '10:00', end_time: '11:00' });
const friSlot = slot({
  weekday: 4,
  weekday_code: 'Fri',
  course_code: 'B',
  course_label: '稲B',
  start_time: '14:00',
  end_time: '15:00',
});

function coverage(over: Partial<ProposeCoverage> = {}): ProposeCoverage {
  return {
    required_days: 2,
    requested_weekdays: [0, 4],
    per_day: [
      { weekday: 0, weekday_code: 'Mon', has_slot: true, best_slot: monSlot },
      { weekday: 4, weekday_code: 'Fri', has_slot: true, best_slot: friSlot },
    ],
    covered_days: 2,
    fully_covered: true,
    ...over,
  };
}

function response(over: Partial<ProposeSlotsResponse> = {}): ProposeSlotsResponse {
  return {
    iso_year: 2026,
    iso_week: 23,
    candidate_lat: null,
    candidate_lng: null,
    resolved_office_id: null,
    slots: [monSlot, friSlot],
    coverage: coverage(),
    message: null,
    ...over,
  };
}

const POOL_PATIENT: PatientRead = {
  id: '22222222-2222-2222-2222-222222222222',
  code: 'P-001',
  name: '山田 太郎',
  kana: 'ヤマダ タロウ',
  address: '千葉市稲毛区小仲台6-2-1',
  status: 'active',
  weekly_pattern: { frequency_per_week: 2, preferred_weekdays: ['Mon', 'Fri'] },
} as unknown as PatientRead;

function setPropose(data: ProposeSlotsResponse | undefined) {
  (useProposeSlots as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: mocks.proposeMutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    data,
  });
}

function fixedVisit(over: Partial<PatientFixedVisitV2Read>): PatientFixedVisitV2Read {
  return {
    id: '99999999-9999-9999-9999-999999999999',
    patient_id: POOL_PATIENT.id,
    mode: 'normal',
    weekday: 2,
    start_time: '09:00',
    duration_min: 45,
    slot_index: 0,
    course_template_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  } as PatientFixedVisitV2Read;
}

function setExistingFixedVisits(
  data: PatientFixedVisitV2Read[],
  over: { isLoading?: boolean; isFetching?: boolean; isError?: boolean } = {},
) {
  (useFixedVisits as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data,
    isLoading: over.isLoading ?? false,
    isFetching: over.isFetching ?? false,
    isError: over.isError ?? false,
  });
}

function renderModal(poolPatients: PatientRead[] = [POOL_PATIENT]) {
  return render(
    <ProposeNewModal
      open
      onClose={vi.fn()}
      isoYear={2026}
      isoWeek={23}
      officeId={OFFICE_ID}
      poolPatients={poolPatients}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  setPropose(undefined);
  (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items: [POOL_PATIENT], total: 1, page: 1, limit: 200, truncated: false },
    isLoading: false,
    isFetching: false,
    isError: false,
  });
  (useCreatePatient as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutateAsync: mocks.createMutateAsync,
    isPending: false,
  });
  (useConfirmFixedVisits as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
    mutate: mocks.confirmMutate,
    mutateAsync: mocks.confirmMutateAsync,
    isPending: false,
  });
  // 既定: 既存 normal 固定枠は空 (読み込み完了)。
  setExistingFixedVisits([]);
  mocks.createMutateAsync.mockResolvedValue({
    id: '33333333-3333-3333-3333-333333333333',
    name: '新規 花子',
  });
  mocks.confirmMutateAsync.mockResolvedValue({});
});

describe('候補ソース切替', () => {
  it('プール / 既存 / 新規 のタブが出る', () => {
    renderModal();
    expect(screen.getByTestId('tab-pool')).toBeInTheDocument();
    expect(screen.getByTestId('tab-existing')).toBeInTheDocument();
    expect(screen.getByTestId('tab-new')).toBeInTheDocument();
  });

  it('既定 (プール) ではプール患者の候補が出る', () => {
    renderModal();
    expect(screen.getByTestId('propose-patient-candidates')).toBeInTheDocument();
    expect(screen.getByText('山田 太郎')).toBeInTheDocument();
  });

  it('プール候補は渡された poolPatients のみで、全患者 (usePatients) は使わない', () => {
    // usePatients は「全患者」(プール外の患者を含む) を返す状態にしておく。
    const NON_POOL_PATIENT = {
      ...POOL_PATIENT,
      id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      code: 'P-999',
      name: 'プール外 次郎',
    } as PatientRead;
    (usePatients as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [POOL_PATIENT, NON_POOL_PATIENT],
        total: 2,
        page: 1,
        limit: 12,
        truncated: false,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
    });
    // poolPatients prop には山田 太郎のみ渡す。
    renderModal([POOL_PATIENT]);
    // プールタブの候補は渡された poolPatients のみ。
    expect(screen.getByText('山田 太郎')).toBeInTheDocument();
    // usePatients が返す「プール外」患者は出ない (全患者フィルタを使っていない証拠)。
    expect(screen.queryByText('プール外 次郎')).not.toBeInTheDocument();
  });

  it('poolPatients が空のときはプール候補なし表示', () => {
    renderModal([]);
    expect(screen.getByText('プール候補がありません。')).toBeInTheDocument();
    expect(screen.queryByText('山田 太郎')).not.toBeInTheDocument();
  });
});

describe('propose 呼び出し', () => {
  it('プール患者選択 + 住所あり → 提案する で iso_year/iso_week・existing_patient_id 付きで mutate', () => {
    renderModal();
    // プール患者を選択 (住所がプリフィルされる)。
    fireEvent.click(screen.getByText('山田 太郎'));
    fireEvent.click(screen.getByTestId('propose-run-button'));

    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    const arg = mocks.proposeMutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(arg.iso_year).toBe(2026);
    expect(arg.iso_week).toBe(23);
    expect(arg.address).toBe('千葉市稲毛区小仲台6-2-1');
    expect(arg.existing_patient_id).toBe(POOL_PATIENT.id);
    expect(arg.office_ids).toEqual([OFFICE_ID]);
  });

  it('患者未選択では mutate されず警告 toast', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('propose-run-button'));
    expect(mocks.proposeMutate).not.toHaveBeenCalled();
    expect(mockToast.warning).toHaveBeenCalled();
  });
});

describe('カバレッジ表示 (StageC)', () => {
  it('「希望 週N日 → M/N日 提案可」+ 全日OK バッジ + ○/× チップ', () => {
    setPropose(response());
    renderModal();
    expect(screen.getByText('希望 週2日 → 2/2日 提案可')).toBeInTheDocument();
    expect(screen.getByText('全日OK')).toBeInTheDocument();
    expect(screen.getByLabelText('月曜 提案あり')).toBeInTheDocument();
    expect(screen.getByLabelText('金曜 提案あり')).toBeInTheDocument();
  });

  it('未充足曜日は × チップで出て全日OK は出ない', () => {
    setPropose(
      response({
        coverage: coverage({
          per_day: [
            { weekday: 0, weekday_code: 'Mon', has_slot: true, best_slot: monSlot },
            { weekday: 4, weekday_code: 'Fri', has_slot: false, best_slot: null },
          ],
          covered_days: 1,
          fully_covered: false,
        }),
        slots: [monSlot],
      }),
    );
    renderModal();
    expect(screen.getByText('希望 週2日 → 1/2日 提案可')).toBeInTheDocument();
    expect(screen.getByLabelText('金曜 提案なし')).toBeInTheDocument();
    expect(screen.queryByText('全日OK')).not.toBeInTheDocument();
  });
});

describe('採用 → 確定: 既存 (登録済)', () => {
  it('採用 → 固定枠を確定 で PUT fixed-visits のみ (POST patients は呼ばない)', () => {
    setPropose(response());
    renderModal();
    // プール患者を選択。
    fireEvent.click(screen.getByText('山田 太郎'));
    // 月曜枠を採用。
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    // 確定。
    fireEvent.click(screen.getByTestId('propose-existing-confirm-button'));

    expect(mocks.confirmMutate).toHaveBeenCalledTimes(1);
    const arg = mocks.confirmMutate.mock.calls[0]![0] as {
      patientId: string;
      body: { mode: string; items: Array<Record<string, unknown>> };
    };
    expect(arg.patientId).toBe(POOL_PATIENT.id);
    expect(arg.body.mode).toBe('normal');
    expect(arg.body.items).toHaveLength(1);
    expect(arg.body.items[0]!.weekday).toBe(0);
    expect(arg.body.items[0]!.start_time).toBe('10:00');
    expect(arg.body.items[0]!.duration_min).toBe(60);
    // 既存フローでは患者作成しない。
    expect(mocks.createMutateAsync).not.toHaveBeenCalled();
  });

  it('採用枠ゼロでは確定ボタンが無効でクリックしても mutate されない', () => {
    setPropose(response());
    renderModal();
    fireEvent.click(screen.getByText('山田 太郎'));
    fireEvent.click(screen.getByTestId('propose-existing-confirm-button'));
    expect(mocks.confirmMutate).not.toHaveBeenCalled();
  });

  it('既存の非採用曜日の固定枠はマージで保持される (水=既存, 月=採用)', () => {
    // 患者は水曜 09:00 の既存 normal 枠を持つ。
    setExistingFixedVisits([
      fixedVisit({ weekday: 2, start_time: '09:00', duration_min: 45, slot_index: 0 }),
    ]);
    setPropose(response());
    renderModal();
    fireEvent.click(screen.getByText('山田 太郎'));
    // 月曜枠のみ採用。
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-existing-confirm-button'));

    expect(mocks.confirmMutate).toHaveBeenCalledTimes(1);
    const arg = mocks.confirmMutate.mock.calls[0]![0] as {
      patientId: string;
      body: { mode: string; items: Array<Record<string, unknown>> };
    };
    expect(arg.body.mode).toBe('normal');
    // 既存の水曜枠 + 採用した月曜枠 = 2 件 (非採用曜日が消えない)。
    expect(arg.body.items).toHaveLength(2);
    const byWeekday = new Map(arg.body.items.map((it) => [it.weekday, it]));
    // 採用した月曜 (weekday=0)。
    expect(byWeekday.get(0)).toMatchObject({ weekday: 0, start_time: '10:00' });
    // 保持された既存の水曜 (weekday=2)。
    expect(byWeekday.get(2)).toMatchObject({ weekday: 2, start_time: '09:00', duration_min: 45 });
  });

  it('採用曜日でも slot_index!=0 の既存枠 (2名体制の相方) は保持される', () => {
    // 患者は月曜に slot_index=0 (主担当 08:00) と slot_index=1 (相方 08:00) を持つ。
    // 月曜を採用すると slot_index=0 は置換されるが、slot_index=1 (相方) は残す。
    setExistingFixedVisits([
      fixedVisit({ weekday: 0, start_time: '08:00', duration_min: 30, slot_index: 0 }),
      fixedVisit({
        id: '88888888-8888-8888-8888-888888888888',
        weekday: 0,
        start_time: '08:00',
        duration_min: 30,
        slot_index: 1,
      }),
    ]);
    setPropose(response());
    renderModal();
    fireEvent.click(screen.getByText('山田 太郎'));
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-existing-confirm-button'));

    const arg = mocks.confirmMutate.mock.calls[0]![0] as {
      body: { items: Array<Record<string, unknown>> };
    };
    const mondays = arg.body.items.filter((it) => it.weekday === 0);
    // 月曜は 2 件: 採用した slot_index=0 (10:00) + 保持された相方 slot_index=1 (08:00)。
    expect(mondays).toHaveLength(2);
    const partner = mondays.find((it) => it.slot_index === 1);
    expect(partner).toMatchObject({ weekday: 0, start_time: '08:00', slot_index: 1 });
    const primary = mondays.find((it) => it.slot_index === 0);
    expect(primary).toMatchObject({ weekday: 0, start_time: '10:00' });
  });

  it('採用曜日と同じ曜日の既存枠は今回の枠で置換される (二重化しない)', () => {
    // 患者は月曜 08:00 の既存枠を持つ → 月曜を採用すると置換される。
    setExistingFixedVisits([
      fixedVisit({ weekday: 0, start_time: '08:00', duration_min: 30, slot_index: 0 }),
    ]);
    setPropose(response());
    renderModal();
    fireEvent.click(screen.getByText('山田 太郎'));
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-existing-confirm-button'));

    const arg = mocks.confirmMutate.mock.calls[0]![0] as {
      body: { items: Array<Record<string, unknown>> };
    };
    // 月曜は 1 件のみ (採用枠で置換、既存 08:00 は破棄)。
    const mondays = arg.body.items.filter((it) => it.weekday === 0);
    expect(mondays).toHaveLength(1);
    expect(mondays[0]).toMatchObject({ start_time: '10:00' });
  });

  it('既存固定枠の読み込み中は確定ボタンが無効', () => {
    setExistingFixedVisits([], { isFetching: true });
    setPropose(response());
    renderModal();
    fireEvent.click(screen.getByText('山田 太郎'));
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-existing-confirm-button'));
    expect(mocks.confirmMutate).not.toHaveBeenCalled();
  });
});

describe('採用 → 確定: 新規 (作成 + 確定)', () => {
  it('新規タブ → カルテ入力 + 採用 → 登録へ進む → 登録して確定 で POST patients → PUT fixed-visits', async () => {
    setPropose(response());
    renderModal();

    // 新規タブへ切替。
    fireEvent.click(screen.getByTestId('tab-new'));

    // 新規タブのカルテ欄が出る。
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.change(screen.getByLabelText('フリガナ'), { target: { value: 'シンキ ハナコ' } });

    // 月曜枠を採用。
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));

    // 登録へ進む → 確認 dialog。
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    expect(screen.getByTestId('propose-new-confirm-dialog')).toBeInTheDocument();

    // 登録して確定。
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    const formValues = mocks.createMutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(formValues.name).toBe('新規 花子');
    expect(formValues.code).toBe('P-999');

    await waitFor(() => expect(mocks.confirmMutateAsync).toHaveBeenCalledTimes(1));
    const confirmArg = mocks.confirmMutateAsync.mock.calls[0]![0] as {
      patientId: string;
      body: { mode: string; items: unknown[] };
    };
    expect(confirmArg.patientId).toBe('33333333-3333-3333-3333-333333333333');
    expect(confirmArg.body.mode).toBe('normal');
    expect(confirmArg.body.items).toHaveLength(1);
  });

  it('propose の candidate_lat/lng が create payload に lat/lng として乗る', async () => {
    setPropose(response({ candidate_lat: 35.123, candidate_lng: 140.456 }));
    renderModal();

    fireEvent.click(screen.getByTestId('tab-new'));
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    const formValues = mocks.createMutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(formValues.lat).toBe('35.123');
    expect(formValues.lng).toBe('140.456');
  });

  it('propose の resolved_office_id が create payload の primary_office_id に乗る', async () => {
    setPropose(response({ resolved_office_id: OFFICE_ID }));
    renderModal();

    fireEvent.click(screen.getByTestId('tab-new'));
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    const formValues = mocks.createMutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(formValues.primary_office_id).toBe(OFFICE_ID);
  });

  it('resolved_office_id が無い場合は primary_office_id は空文字', async () => {
    setPropose(response({ resolved_office_id: null }));
    renderModal();

    fireEvent.click(screen.getByTestId('tab-new'));
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    const formValues = mocks.createMutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(formValues.primary_office_id).toBe('');
  });

  it('candidate 座標が無い場合は lat/lng 空文字 (住所のみ)', async () => {
    setPropose(response({ candidate_lat: null, candidate_lng: null }));
    renderModal();

    fireEvent.click(screen.getByTestId('tab-new'));
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    const formValues = mocks.createMutateAsync.mock.calls[0]![0] as Record<string, unknown>;
    expect(formValues.lat).toBe('');
    expect(formValues.lng).toBe('');
  });

  it('POST 成功・PUT 失敗 時は「登録済・固定枠のみ失敗」のメッセージを出す', async () => {
    mocks.confirmMutateAsync.mockRejectedValueOnce(new Error('boom'));
    setPropose(response());
    renderModal();

    fireEvent.click(screen.getByTestId('tab-new'));
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    // 患者作成は成功している。
    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    // PUT 失敗時のメッセージは「登録されました」+「固定枠の確定のみ失敗」を含む。
    await waitFor(() => expect(mockToast.error).toHaveBeenCalledTimes(1));
    const msg = String(mockToast.error.mock.calls[0]![0]);
    expect(msg).toContain('登録されました');
    expect(msg).toContain('固定枠の確定のみ失敗');
  });

  it('POST 自体が失敗した場合は別メッセージ (患者登録に失敗)', async () => {
    mocks.createMutateAsync.mockRejectedValueOnce(new Error('boom'));
    setPropose(response());
    renderModal();

    fireEvent.click(screen.getByTestId('tab-new'));
    fireEvent.change(screen.getByLabelText('氏名（必須）'), { target: { value: '新規 花子' } });
    fireEvent.change(screen.getByLabelText('患者コード（必須）'), { target: { value: 'P-999' } });
    fireEvent.click(screen.getByTestId(`propose-adopt-${OFFICE_ID}-0-A-10:00`));
    fireEvent.click(screen.getByTestId('propose-new-proceed-button'));
    fireEvent.click(screen.getByTestId('propose-new-confirm-button'));

    await waitFor(() => expect(mocks.createMutateAsync).toHaveBeenCalledTimes(1));
    // PUT は呼ばれない。
    expect(mocks.confirmMutateAsync).not.toHaveBeenCalled();
    await waitFor(() => expect(mockToast.error).toHaveBeenCalledTimes(1));
    const msg = String(mockToast.error.mock.calls[0]![0]);
    expect(msg).toContain('患者の登録に失敗');
    expect(msg).not.toContain('固定枠の確定のみ失敗');
  });
});

describe('2名体制の注記 (solver 未対応の正直表示)', () => {
  it('2名体制 ON で提案結果がある時は注記を出す', () => {
    setPropose(response());
    renderModal();
    // プール患者を選択 (提案結果を表示するため)。
    fireEvent.click(screen.getByText('山田 太郎'));
    // 「2名体制が必要」トグルを ON。
    fireEvent.click(screen.getByText('2名体制が必要'));
    // 結果近傍に注記が出る。
    expect(screen.getByTestId('propose-multistaff-note')).toBeInTheDocument();
    expect(screen.getByText('2名体制の空き判定は未対応です')).toBeInTheDocument();
  });

  it('2名体制 OFF の時は注記を出さない', () => {
    setPropose(response());
    renderModal();
    fireEvent.click(screen.getByText('山田 太郎'));
    expect(screen.queryByTestId('propose-multistaff-note')).not.toBeInTheDocument();
  });
});
