/**
 * DiffAddDialog — Phase G-92 FE (プール投入 コースカード様式) テスト.
 *
 * カバーするシナリオ:
 *   1. proposal_source='fixed'                    → ✅緑系「固定枠で入れられます」.
 *   2. proposal_source='preferred'                → 🟡黄系「希望枠で入れられます」.
 *   3. proposal_source='fixed_fallback_preferred' → 🔴固定枠不可理由 (日本語化) +
 *      🟡希望枠案 の 2 段表示.
 *   4. fixed_fallback_preferred + 理由空 → 中立文言「固定枠に入れられませんでした」.
 *   5. 「この枠で採用」 → 確認モーダル → apply-individual が呼ばれ、カードが除去される.
 *
 * 注: DiffAddDialog は内部で /diff-add を fetch するため、 query hook を mock して
 * 提案リストを注入する。 重量サブコンポーネント (DiffAddProposalTimeline) は
 * stub に差し替える。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

// ─── 共通モック ─────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
  mocks: {
    diffAddMutateAsync: vi.fn(),
    applyIndividualMutateAsync: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  CheckCircle2: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  Plus: () => <span />,
  X: () => <span />,
}));

// Dialog: portal を介さず children を素通しさせる (jsdom + Radix portal 回避).
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

vi.mock('@/lib/queries/autoScheduleV2', () => ({
  useDiffAddProposalsMutation: () => ({
    mutateAsync: mocks.diffAddMutateAsync,
    reset: vi.fn(),
    isPending: false,
    data: undefined,
    error: null,
    isSuccess: false,
  }),
  useApplyIndividualMutation: () => ({
    mutateAsync: mocks.applyIndividualMutateAsync,
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
}));

// 重量タイムライン (DB fetch を伴う) は stub に置換.
vi.mock('../DiffAddProposalTimeline', () => ({
  DiffAddProposalTimeline: () => <div data-testid="timeline-stub" />,
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { DiffAddDialog } from '../DiffAddDialog';
// ドリフト防止: 採用 payload は単体表示 (PatientScheduleDetailDialog) と同じ共有ヘルパーで
// 決まることを両側で施錠する (詳細は pool-proposal テスト参照).
import { adoptedVisitPlans } from '../DiffAddProposalCard';
import type { DiffAddProposal, DiffAddProposalSource } from '@/lib/schemas/v2/autoScheduleV2';

// ─── Fixtures ───────────────────────────────────────────────────────────────

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';

function makeVisitPlan() {
  return {
    weekday: 1, // 火
    start_time: '09:30:00',
    end_time: '10:30:00',
    duration_min: 60,
    course_code: 'B',
    office_id: OFFICE_ID,
    am_pm: 'am' as const,
    assigned_staff_id: null,
  };
}

function makeProposal(
  proposalId: string,
  source: DiffAddProposalSource,
  reasons: string[] = [],
): DiffAddProposal {
  return {
    proposal_id: proposalId,
    patient_id: `22222222-2222-4222-8222-${proposalId.slice(-12).padStart(12, '0')}`,
    patient_name: `患者-${proposalId.slice(-4)}`,
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
    proposal_source: source,
    fixed_unavailable_reasons: reasons,
  };
}

function mockProposals(proposals: DiffAddProposal[]) {
  mocks.diffAddMutateAsync.mockResolvedValue({
    proposal_batch_id: '33333333-3333-4333-8333-333333333333',
    proposals,
    kpi_overall: {
      total_distance_km_before: 0,
      total_distance_km_after: 0,
      distance_reduction_pct: 0,
      courses_count_before: 0,
      courses_count_after: 0,
      capacity_overflows: 0,
      h_violations: {},
    },
    warnings: [],
  });
}

const COMMON_PROPS = {
  open: true,
  onClose: () => {},
  isoYear: 2026,
  isoWeek: 24,
  officeId: OFFICE_ID,
};

describe('DiffAddDialog (Phase G-92 FE コースカード)', () => {
  beforeEach(() => {
    mocks.diffAddMutateAsync.mockReset();
    mocks.applyIndividualMutateAsync.mockReset();
    mockToast.success.mockReset();
    mockToast.error.mockReset();
  });

  it('1. fixed → ✅緑系「固定枠で入れられます」 を描画する', async () => {
    const p = makeProposal('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'fixed');
    mockProposals([p]);
    render(<DiffAddDialog {...COMMON_PROPS} />);

    const card = await screen.findByTestId(`diff-add-card-${p.proposal_id}`);
    expect(card).toHaveAttribute('data-source', 'fixed');
    const headline = screen.getByTestId(`diff-add-card-${p.proposal_id}-headline`);
    expect(headline).toHaveTextContent('固定枠で入れられます');
    // fallback の 2 段表示は出ない.
    expect(
      screen.queryByTestId(`diff-add-card-${p.proposal_id}-fixed-reason`),
    ).not.toBeInTheDocument();
  });

  it('2. preferred → 🟡黄系「希望枠で入れられます」 を描画する', async () => {
    const p = makeProposal('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'preferred');
    mockProposals([p]);
    render(<DiffAddDialog {...COMMON_PROPS} />);

    const card = await screen.findByTestId(`diff-add-card-${p.proposal_id}`);
    expect(card).toHaveAttribute('data-source', 'preferred');
    expect(screen.getByTestId(`diff-add-card-${p.proposal_id}-headline`)).toHaveTextContent(
      '希望枠で入れられます',
    );
  });

  it('3. fixed_fallback_preferred → 固定枠不可理由 (日本語化) + 希望枠案 の 2 段表示', async () => {
    const p = makeProposal('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'fixed_fallback_preferred', [
      'time_no_fit',
      'capacity_over',
    ]);
    mockProposals([p]);
    render(<DiffAddDialog {...COMMON_PROPS} />);

    await screen.findByTestId(`diff-add-card-${p.proposal_id}`);
    const reasonBox = screen.getByTestId(`diff-add-card-${p.proposal_id}-fixed-reason`);
    // 理由コードが日本語化される.
    expect(reasonBox).toHaveTextContent('移動時間が足りません');
    expect(reasonBox).toHaveTextContent('コースが定員オーバーです');
    // 希望枠案 (黄) の段が出る.
    expect(
      screen.getByTestId(`diff-add-card-${p.proposal_id}-preferred-suggest`),
    ).toHaveTextContent('希望枠ならこちらに入れられます');
  });

  it('4. fixed_fallback_preferred + 理由空 → 中立文言を出す', async () => {
    const p = makeProposal('dddddddd-dddd-4ddd-8ddd-dddddddddddd', 'fixed_fallback_preferred', []);
    mockProposals([p]);
    render(<DiffAddDialog {...COMMON_PROPS} />);

    await screen.findByTestId(`diff-add-card-${p.proposal_id}`);
    const reasonBox = screen.getByTestId(`diff-add-card-${p.proposal_id}-fixed-reason`);
    expect(reasonBox).toHaveTextContent('固定枠に入れられませんでした');
  });

  it('5. 「この枠で採用」→ 確認モーダル → apply-individual 呼出 + カード除去', async () => {
    const p = makeProposal('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', 'fixed');
    mockProposals([p]);
    mocks.applyIndividualMutateAsync.mockResolvedValue({
      patient_id: p.patient_id,
      applied: true,
      fixed_visit_ids: [],
      idempotent: false,
      warnings: [],
    });
    render(<DiffAddDialog {...COMMON_PROPS} />);

    await screen.findByTestId(`diff-add-card-${p.proposal_id}`);
    // 採用ボタン → 確認モーダルが開く.
    fireEvent.click(screen.getByTestId(`diff-add-card-${p.proposal_id}-adopt`));
    expect(screen.getByTestId(`diff-add-confirm-${p.proposal_id}`)).toBeInTheDocument();
    // 確認モーダルで採用.
    fireEvent.click(screen.getByTestId('diff-add-confirm-apply'));

    await waitFor(() => expect(mocks.applyIndividualMutateAsync).toHaveBeenCalledTimes(1));
    // visit_plans に suggested_visits が乗る.
    const arg = mocks.applyIndividualMutateAsync.mock.calls[0][0];
    expect(arg.proposal_id).toBe(p.proposal_id);
    expect(arg.patient_id).toBe(p.patient_id);
    expect(arg.confirm).toBe(true);
    // 採用 payload は共有ヘルパーで決まる = 単体表示と同一 (取り残し検知).
    expect(arg.visit_plans).toEqual(adoptedVisitPlans(p));
    // 採用後はカードが除去される.
    await waitFor(() =>
      expect(screen.queryByTestId(`diff-add-card-${p.proposal_id}`)).not.toBeInTheDocument(),
    );
  });
});
