/**
 * PatientScheduleDetailDialog — プール投入提案セクション (Pool-detail 統合) テスト.
 *
 * 目的 (ドリフト防止 / ユーザー懸念への回答):
 *   保留プールの患者クリックで開く詳細ダイアログ内の「プール投入の提案」が、
 *   一括ダイアログ (DiffAddDialog) と **同じ共有部品 / 同じ採用ロジック** を使う
 *   ことを施錠する。具体的には:
 *     - 共有 ProposalCard (data-testid=`diff-add-card-*`) を描画すること。
 *     - 採用時の apply-individual payload の visit_plans が、共有ヘルパー
 *       ``adoptedVisitPlans(proposal)`` と完全一致すること
 *       (= DiffAddDialog test #5 と同じ contract)。
 *   これにより、将来どちらか片方の表示/採用ロジックだけが変更されて取り残される
 *   事態を検知できる。
 *
 * 提案の「算出」自体は BE 単一エンドポイント (/v2/diff-add) が唯一のソースであり、
 * 一括も単体も同一 query (useDiffAddProposalsQuery) を叩くため、本テストは FE 側の
 * 表示・採用の共有を担保する。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ─── 共通モック ─────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    diffAddQueryResult: { data: undefined, isLoading: false, isError: false } as {
      data: unknown;
      isLoading: boolean;
      isError: boolean;
    },
    applyIndividualMutateAsync: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  Loader2: () => <span data-testid="loader" />,
  Pencil: () => <span />,
  CheckCircle2: () => <span />,
  X: () => <span />,
}));

vi.mock('@/components/schedule/WeekSelector', () => ({
  addDays: (d: Date, n: number) => new Date(d.getTime() + n * 86400000),
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

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
  AlertTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

// データ fetch 系 hook は最小限の成功レスポンスを返す.
vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => ({
    data: { id: PATIENT_ID, name: '患者-Pool', code: 'P-001', status: 'active', note: null },
    isLoading: false,
    isError: false,
  }),
}));
vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: () => ({ data: [], isLoading: false, isError: false }),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: () => ({ data: { items: [] }, isLoading: false, isError: false }),
}));
vi.mock('@/lib/api/patientSync', () => ({
  useSyncWeekVisitsToFixedMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/lib/queries/autoScheduleV2', () => ({
  useDiffAddProposalsQuery: () => mocks.diffAddQueryResult,
  useApplyIndividualMutation: () => ({
    mutateAsync: mocks.applyIndividualMutateAsync,
    isPending: false,
  }),
}));

// ネスト編集ダイアログ / 重量タイムラインは stub.
vi.mock('../PatientEditDialog', () => ({
  PatientEditDialog: () => <div data-testid="edit-stub" />,
}));
vi.mock('../DiffAddProposalTimeline', () => ({
  DiffAddProposalTimeline: () => <div data-testid="timeline-stub" />,
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { PatientScheduleDetailDialog } from '../PatientScheduleDetailDialog';
// 共有ヘルパー — 一括ダイアログと単体表示が同じ採用 payload を作ることを照合する.
import { adoptedVisitPlans } from '../DiffAddProposalCard';
import type { DiffAddProposal } from '@/lib/schemas/v2/autoScheduleV2';

// ─── Fixtures ───────────────────────────────────────────────────────────────

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const OTHER_PATIENT_ID = '99999999-9999-4999-8999-999999999999';
const OFFICE_ID = '11111111-1111-4111-8111-111111111111';
const PROPOSAL_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

function makeVisitPlan() {
  return {
    weekday: 1,
    start_time: '09:30:00',
    end_time: '10:30:00',
    duration_min: 60,
    course_code: 'B',
    office_id: OFFICE_ID,
    am_pm: 'am' as const,
    assigned_staff_id: null,
  };
}

function makeProposal(patientId: string): DiffAddProposal {
  return {
    proposal_id: PROPOSAL_ID,
    patient_id: patientId,
    patient_name: '患者-Pool',
    patient_code: null,
    suggested: makeVisitPlan(),
    suggested_visits: [makeVisitPlan()],
    before_summary: { course_visits_count: 3, distance_km: 1.2 },
    after_summary: { course_visits_count: 4, distance_km: 1.5 },
    delta: {
      distance_km: 0.3,
      capacity: null,
      course_visits_count_before: 3,
      course_visits_count_after: 4,
    },
    warnings: [],
    proposal_source: 'fixed',
    fixed_unavailable_reasons: [],
  };
}

function setQuery(proposals: DiffAddProposal[]) {
  mocks.diffAddQueryResult.data = {
    proposal_batch_id: '33333333-3333-4333-8333-333333333333',
    proposals,
    kpi_overall: {},
    warnings: [],
  };
  mocks.diffAddQueryResult.isLoading = false;
  mocks.diffAddQueryResult.isError = false;
}

const COMMON_PROPS = {
  patientId: PATIENT_ID,
  open: true as const,
  onClose: () => {},
  isoYear: 2026,
  isoWeek: 24,
  canEdit: true,
  enablePoolProposal: true,
  officeId: OFFICE_ID,
};

describe('PatientScheduleDetailDialog プール投入提案 (Pool-detail 統合)', () => {
  beforeEach(() => {
    mocks.applyIndividualMutateAsync.mockReset();
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    mocks.diffAddQueryResult.data = undefined;
    mocks.diffAddQueryResult.isLoading = false;
    mocks.diffAddQueryResult.isError = false;
  });

  it('プール由来クリック時、当該患者の共有 ProposalCard を描画する', () => {
    setQuery([makeProposal(PATIENT_ID)]);
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);

    expect(screen.getByTestId('patient-schedule-pool-proposal')).toBeInTheDocument();
    // 一括ダイアログと同一の共有カード (data-testid 体系) が出る.
    expect(screen.getByTestId(`diff-add-card-${PROPOSAL_ID}`)).toBeInTheDocument();
  });

  it('採用 → 確認モーダル → apply-individual の visit_plans が共有ヘルパーと一致する (ドリフト防止)', async () => {
    const proposal = makeProposal(PATIENT_ID);
    setQuery([proposal]);
    mocks.applyIndividualMutateAsync.mockResolvedValue({
      patient_id: PATIENT_ID,
      applied: true,
      fixed_visit_ids: [],
      idempotent: false,
      warnings: [],
    });
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);

    fireEvent.click(screen.getByTestId(`diff-add-card-${PROPOSAL_ID}-adopt`));
    expect(screen.getByTestId(`diff-add-confirm-${PROPOSAL_ID}`)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('diff-add-confirm-apply'));

    await waitFor(() => expect(mocks.applyIndividualMutateAsync).toHaveBeenCalledTimes(1));
    const arg = mocks.applyIndividualMutateAsync.mock.calls[0][0];
    expect(arg.proposal_id).toBe(PROPOSAL_ID);
    expect(arg.patient_id).toBe(PATIENT_ID);
    expect(arg.confirm).toBe(true);
    // 採用 payload は一括ダイアログと同じ共有ヘルパーで決まる = 取り残し検知の要.
    expect(arg.visit_plans).toEqual(adoptedVisitPlans(proposal));

    // 採用後は成功表示に切り替わる.
    await waitFor(() =>
      expect(screen.getByTestId('patient-schedule-pool-adopted')).toBeInTheDocument(),
    );
  });

  it('当該患者の提案が無い場合は「候補なし」を表示する', () => {
    setQuery([makeProposal(OTHER_PATIENT_ID)]);
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);

    expect(screen.getByTestId('patient-schedule-pool-no-proposal')).toBeInTheDocument();
    expect(screen.queryByTestId(`diff-add-card-${PROPOSAL_ID}`)).not.toBeInTheDocument();
  });

  it('enablePoolProposal=false (通常テーブル由来) ではセクションを出さない', () => {
    setQuery([makeProposal(PATIENT_ID)]);
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} enablePoolProposal={false} />);

    expect(screen.queryByTestId('patient-schedule-pool-proposal')).not.toBeInTheDocument();
  });

  it('canEdit=false では採用ボタンを出さない (閲覧専用)', () => {
    setQuery([makeProposal(PATIENT_ID)]);
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} canEdit={false} />);

    expect(screen.getByTestId(`diff-add-card-${PROPOSAL_ID}`)).toBeInTheDocument();
    expect(screen.queryByTestId(`diff-add-card-${PROPOSAL_ID}-adopt`)).not.toBeInTheDocument();
  });
});
