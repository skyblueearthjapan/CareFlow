/**
 * FullOptimizeDialog — P3/P4 早期検知 UX テスト.
 *
 * カバーするシナリオ:
 *   P3: バナーに will_be_inserted_count と unassigned_count が両方表示される.
 *   P4: unassigned > 0 のとき採用ボタン押下で 2 段階目の confirm dialog が出る.
 *   P4: unassigned > 0 で confirm をキャンセルすると apply されない.
 *   P4: unassigned === 0 のとき 2 段階目 confirm をスキップして即 apply する.
 *
 * + countWillBeInserted の純関数 unit test.
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';

// ─── 共通モック ─────────────────────────────────────────────────────────────

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
  mocks: {
    fullOptimizeMutateAsync: vi.fn(),
    applyWeekOnlyMutateAsync: vi.fn(),
    applyIndividualMutateAsync: vi.fn(),
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  ArrowRight: () => <span />,
  CalendarRange: () => <span />,
  CheckCircle2: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  Pin: () => <span />,
  RefreshCw: () => <span />,
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
  DialogClose: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogPortal: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DialogOverlay: () => null,
}));

vi.mock('@/components/ui/tabs', () => ({
  Tabs: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsList: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TabsTrigger: ({ children }: { children: React.ReactNode }) => <button>{children}</button>,
  TabsContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
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

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

vi.mock('@/lib/queries/staff', () => ({
  useStaffList: () => ({ data: [], isLoading: false }),
}));

vi.mock('@/lib/queries/autoScheduleV2', () => ({
  useFullOptimizeMutation: () => ({
    mutateAsync: mocks.fullOptimizeMutateAsync,
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
  useApplyWeekOnlyMutation: () => ({
    mutateAsync: mocks.applyWeekOnlyMutateAsync,
    reset: vi.fn(),
    isPending: false,
    error: null,
    isSuccess: false,
  }),
}));

// 重量サブコンポーネントは描画不要 (今回のテストは banner + confirm dialog).
vi.mock('../../WeekdayScheduleCard', () => ({
  WeekdayScheduleCard: () => <div />,
}));

vi.mock('../ProposalWeekCalendar', () => ({
  ProposalWeekCalendar: () => <div />,
}));

vi.mock('../FixedTimeEditModal', () => ({
  FixedTimeEditModal: () => <div />,
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { FullOptimizeDialog, countWillBeInserted } from '../FullOptimizeDialog';
import type { FullOptimizeResponse } from '@/lib/schemas/v2/autoScheduleV2';

// ─── Fixtures ───────────────────────────────────────────────────────────────

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';

function makeVisitPlan(weekday = 0) {
  return {
    weekday,
    start_time: '09:00',
    end_time: '10:00',
    duration_min: 60,
    course_code: 'A',
    office_id: OFFICE_ID,
    am_pm: 'am' as const,
    assigned_staff_id: null,
  };
}

function makeProposal(patientId: string, hasPlan = true) {
  return {
    proposal_id: `aaaaaaaa-aaaa-4aaa-8aaa-${patientId.slice(-12).padStart(12, '0')}`,
    patient_id: patientId,
    patient_name: `患者-${patientId.slice(-4)}`,
    patient_code: null,
    current_pfv: [],
    proposed_pfv: hasPlan ? [makeVisitPlan()] : [],
    delta: {
      distance_km: 0,
      capacity: null,
      course_visits_count_before: 0,
      course_visits_count_after: 0,
    },
    warnings: [],
  };
}

function uuid(seed: string): string {
  // 適当な UUID 風文字列 (zod schema は strict だが Frontend では型のみ参照される).
  const base = seed.padEnd(12, '0').slice(0, 12);
  return `00000000-0000-4000-8000-${base}`;
}

function makeResponse(opts: {
  assignedPatientIds: string[];
  unassignedPatientIds: string[];
}): FullOptimizeResponse {
  const proposals = opts.assignedPatientIds.map((id) => makeProposal(uuid(id), true));
  return {
    proposal_batch_id: uuid('batch'),
    week_proposals: [
      {
        weekday: 0,
        before: { courses: [] },
        after: {
          courses: [
            {
              code: 'A',
              office_id: OFFICE_ID,
              office_name: '本店',
              assigned_staff_id: null,
              visits: opts.assignedPatientIds.map((id) => ({
                patient_id: uuid(id),
                patient_name: `患者-${id.slice(-4)}`,
                patient_code: null,
                start_time: '09:00',
                end_time: '10:00',
                duration_min: 60,
                am_pm: 'am' as const,
                address: null,
                area_label: null,
                time_type: null,
                sex_restriction: null,
                same_address_group_id: null,
                preferred_start: null,
                preferred_end: null,
                distance_to_next_km: null,
                visit_id: null,
              })),
              distance_km: 0,
              visits_count: opts.assignedPatientIds.length,
            },
          ],
        },
      },
    ],
    individual_proposals: proposals,
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
    unassigned_patients: opts.unassignedPatientIds.map((id) => ({
      patient_id: uuid(id),
      patient_name: `未割当-${id.slice(-4)}`,
      patient_code: null,
      // P2: reason は enum (UnassignedReason). pool 残相当の代表値として "unknown".
      reason: 'unknown' as const,
      reason_detail: null,
      dropped_at_stage: null,
    })),
  };
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('countWillBeInserted (P3 純関数)', () => {
  it('proposed_pfv 非空の unique patient_id 数を返す', () => {
    const res = makeResponse({
      assignedPatientIds: ['p1', 'p2', 'p3'],
      unassignedPatientIds: [],
    });
    expect(countWillBeInserted(res)).toBe(3);
  });

  it('proposed_pfv が空の proposal はカウントしない', () => {
    const res = makeResponse({ assignedPatientIds: ['p1'], unassignedPatientIds: [] });
    res.individual_proposals.push(makeProposal(uuid('px'), false));
    expect(countWillBeInserted(res)).toBe(1);
  });

  it('同じ patient_id の重複は 1 回だけカウント', () => {
    const res = makeResponse({ assignedPatientIds: ['p1'], unassignedPatientIds: [] });
    res.individual_proposals.push(makeProposal(uuid('p1'), true));
    expect(countWillBeInserted(res)).toBe(1);
  });

  it('proposals が空なら 0', () => {
    const res = makeResponse({ assignedPatientIds: [], unassignedPatientIds: ['u1'] });
    expect(countWillBeInserted(res)).toBe(0);
  });
});

describe('FullOptimizeDialog — P3 バナー表示', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('unassigned > 0: バナーに will_be_inserted_count と unassigned_count が両方表示される', async () => {
    const res = makeResponse({
      assignedPatientIds: ['p1', 'p2'],
      unassignedPatientIds: ['u1'],
    });
    mocks.fullOptimizeMutateAsync.mockResolvedValue(res);

    render(
      <FullOptimizeDialog
        open
        onClose={vi.fn()}
        isoYear={2026}
        isoWeek={20}
        officeId={OFFICE_ID}
      />,
    );

    // 警告バナーが表示される (unassigned > 0)
    const banner = await screen.findByTestId('full-optimize-assignment-banner-warn');
    expect(banner).toBeInTheDocument();

    // will_be_inserted_count = 2 (assigned 2 名 = proposed_pfv 非空 2 件)
    const insertedCount = screen.getByTestId('full-optimize-will-be-inserted-count');
    expect(insertedCount.textContent).toMatch(/2 名/);

    // unassigned_count = 1 名 がプール残として併記される
    expect(banner.textContent).toMatch(/1 名がプール残/);

    // 警告メッセージ本文 (旧 visit が削除されたまま新規 visit が作成されない可能性)
    expect(banner.textContent).toMatch(/旧 visit が削除されたまま/);
  });

  it('unassigned === 0: 成功バナーに will_be_inserted_count が表示される', async () => {
    const res = makeResponse({
      assignedPatientIds: ['p1', 'p2', 'p3'],
      unassignedPatientIds: [],
    });
    mocks.fullOptimizeMutateAsync.mockResolvedValue(res);

    render(
      <FullOptimizeDialog
        open
        onClose={vi.fn()}
        isoYear={2026}
        isoWeek={20}
        officeId={OFFICE_ID}
      />,
    );

    const okBanner = await screen.findByTestId('full-optimize-assignment-banner-ok');
    expect(okBanner).toBeInTheDocument();
    expect(okBanner.textContent).toMatch(/全 3 名/);

    const insertedCount = screen.getByTestId('full-optimize-will-be-inserted-count');
    expect(insertedCount.textContent).toMatch(/3 名/);
  });
});

describe('FullOptimizeDialog — P4 unassigned 二段階確認', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function openAndArriveAtWeekOnlyConfirm(res: FullOptimizeResponse) {
    mocks.fullOptimizeMutateAsync.mockResolvedValue(res);
    const onClose = vi.fn();
    render(
      <FullOptimizeDialog
        open
        onClose={onClose}
        isoYear={2026}
        isoWeek={20}
        officeId={OFFICE_ID}
      />,
    );
    // 結果サマリーが描画されるまで待つ
    await screen.findByTestId('full-optimize-decision-panel');
    // 「この週だけ試す」(1 段目を開く)
    fireEvent.click(screen.getByTestId('full-optimize-week-only-button'));
    // 1 段目の confirm panel が出る
    await screen.findByTestId('full-optimize-week-only-confirm-panel');
    return { onClose };
  }

  it('unassigned > 0: 1 段目 confirm 後に 2 段目の dialog が出て、API は呼ばれない', async () => {
    const res = makeResponse({
      assignedPatientIds: ['p1', 'p2'],
      unassignedPatientIds: ['u1'],
    });
    await openAndArriveAtWeekOnlyConfirm(res);

    // 1 段目「この週だけ反映する」をクリック
    fireEvent.click(screen.getByTestId('full-optimize-week-only-confirm'));

    // 2 段目 dialog が描画される
    const ack = await screen.findByTestId('full-optimize-unassigned-ack-dialog');
    expect(ack).toBeInTheDocument();
    expect(ack.textContent).toMatch(/1 名の患者が未割当です/);
    expect(ack.textContent).toMatch(/旧 visit が削除されたまま新規 visit が作成されません/);

    // API はまだ呼ばれていない (2 段目を経由するまで)
    expect(mocks.applyWeekOnlyMutateAsync).not.toHaveBeenCalled();
  });

  it('unassigned > 0 + 2 段目キャンセル: apply されない', async () => {
    const res = makeResponse({
      assignedPatientIds: ['p1'],
      unassignedPatientIds: ['u1', 'u2'],
    });
    await openAndArriveAtWeekOnlyConfirm(res);

    fireEvent.click(screen.getByTestId('full-optimize-week-only-confirm'));
    await screen.findByTestId('full-optimize-unassigned-ack-dialog');

    // キャンセルボタンをクリック
    fireEvent.click(screen.getByTestId('full-optimize-unassigned-ack-cancel'));

    // 2 段目 dialog は消える (1 段目に戻る)
    await waitFor(() => {
      expect(screen.queryByTestId('full-optimize-unassigned-ack-dialog')).toBeNull();
    });
    // 1 段目 confirm panel が残っている
    expect(screen.getByTestId('full-optimize-week-only-confirm-panel')).toBeInTheDocument();
    // API は呼ばれていない
    expect(mocks.applyWeekOnlyMutateAsync).not.toHaveBeenCalled();
  });

  it('unassigned > 0 + 2 段目「続行」: apply API が呼ばれる', async () => {
    const res = makeResponse({
      assignedPatientIds: ['p1'],
      unassignedPatientIds: ['u1'],
    });
    mocks.applyWeekOnlyMutateAsync.mockResolvedValue({
      iso_year: 2026,
      iso_week: 20,
      visits_created: 1,
      visits_soft_deleted: 0,
      courses_created: 0,
      visit_staff_assignments_created: 0,
      warnings: [],
    });
    await openAndArriveAtWeekOnlyConfirm(res);

    fireEvent.click(screen.getByTestId('full-optimize-week-only-confirm'));
    await screen.findByTestId('full-optimize-unassigned-ack-dialog');

    await act(async () => {
      fireEvent.click(screen.getByTestId('full-optimize-unassigned-ack-proceed'));
    });

    // apply API が 1 回呼ばれる
    expect(mocks.applyWeekOnlyMutateAsync).toHaveBeenCalledTimes(1);
    const call = mocks.applyWeekOnlyMutateAsync.mock.calls[0]![0];
    expect(call.confirm).toBe(true);
    expect(call.visit_plans_per_patient).toHaveLength(1);
  });

  it('unassigned === 0: 2 段目 dialog をスキップして即 apply が呼ばれる', async () => {
    const res = makeResponse({
      assignedPatientIds: ['p1', 'p2'],
      unassignedPatientIds: [],
    });
    mocks.applyWeekOnlyMutateAsync.mockResolvedValue({
      iso_year: 2026,
      iso_week: 20,
      visits_created: 2,
      visits_soft_deleted: 0,
      courses_created: 0,
      visit_staff_assignments_created: 0,
      warnings: [],
    });
    await openAndArriveAtWeekOnlyConfirm(res);

    await act(async () => {
      fireEvent.click(screen.getByTestId('full-optimize-week-only-confirm'));
    });

    // 2 段目 dialog は出ない
    expect(screen.queryByTestId('full-optimize-unassigned-ack-dialog')).toBeNull();
    // API が直接呼ばれる
    expect(mocks.applyWeekOnlyMutateAsync).toHaveBeenCalledTimes(1);
  });
});
