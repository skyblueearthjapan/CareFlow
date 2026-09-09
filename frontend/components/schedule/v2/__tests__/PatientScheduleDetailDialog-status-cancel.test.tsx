/**
 * PatientScheduleDetailDialog — 型⇄今週 比較表の取消の扱い + ステータス表示。
 *
 * design 2026-09-09 §7-4 (PO 決定):
 *   - 患者ステータス連動の取消 (source='status_cancel' + status='cancelled') は
 *     盤面から消える。比較表にだけ残ると「今週の予定を型へ反映」が、消えたはずの
 *     枠を型へ書き戻してしまうため、ここでも外す。
 *   - 「今週だけ取消」= manual_cancel は従来どおり残す (今週の実態そのもの)。
 *   - 基本情報の「ステータス」は生値ではなく日本語ラベルで出す。
 *
 * 描画ハーネスは PatientScheduleDetailDialog-pool-proposal.test.tsx と同じ流儀
 * (fetch 系フックを全部モックし、ネストするダイアログ/候補リストは stub)。
 */
import * as React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import type { VisitRead } from '@/lib/schemas/visit';

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const OFFICE_ID = '11111111-1111-4111-8111-111111111111';

const { state } = vi.hoisted(() => ({
  state: {
    patientStatus: 'active' as string,
    visits: [] as unknown[],
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: null, status: 'unauthenticated' }),
}));

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

vi.mock('@/lib/utils', () => ({
  cn: (...args: unknown[]) =>
    args
      .flat()
      .filter((a) => typeof a === 'string' && a)
      .join(' '),
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => ({
    data: {
      id: PATIENT_ID,
      name: '患者-取消',
      code: 'P-002',
      status: state.patientStatus,
      note: null,
    },
    isLoading: false,
    isError: false,
  }),
}));
vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: () => ({ data: [], isLoading: false, isError: false }),
}));
vi.mock('@/lib/queries/visits', () => ({
  useVisits: () => ({ data: { items: state.visits }, isLoading: false, isError: false }),
}));
vi.mock('@/lib/queries/patient_ng_staff', () => ({
  useNgStaffList: () => ({ data: [], isLoading: false, isError: false }),
}));
vi.mock('@/lib/api/patientSync', () => ({
  useSyncWeekVisitsToFixedMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('../PatientEditDialog', () => ({
  PatientEditDialog: () => <div data-testid="edit-stub" />,
}));
vi.mock('../PoolCandidateList', () => ({
  PoolCandidateList: () => <div data-testid="pool-candidate-stub" />,
}));

import { PatientScheduleDetailDialog } from '../PatientScheduleDetailDialog';
import { useUIStore } from '@/lib/stores/ui';

const COMMON_PROPS = {
  patientId: PATIENT_ID,
  open: true as const,
  onClose: () => {},
  isoYear: 2026,
  isoWeek: 37,
  canEdit: true,
  enablePoolProposal: true,
  officeId: OFFICE_ID,
};

/** 2026-09-07 = 月 (weekday 0) / 2026-09-08 = 火 (weekday 1)。 */
function makeVisit(over: Partial<VisitRead> & { id: string }): VisitRead {
  return {
    patient_id: PATIENT_ID,
    visit_date: '2026-09-07',
    start_time: '10:00:00',
    end_time: '11:00:00',
    status: 'planned',
    source: 'auto',
    deleted_at: null,
    ...over,
  } as unknown as VisitRead;
}

beforeEach(() => {
  state.patientStatus = 'active';
  state.visits = [];
  useUIStore.setState({ showInactiveVisits: false });
});

describe('PatientScheduleDetailDialog — 型⇄今週 比較表', () => {
  it('manual_cancel は今週の列に残る', () => {
    state.visits = [makeVisit({ id: 'v-manual', status: 'cancelled', source: 'manual_cancel' })];
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);
    expect(screen.getByTestId('patient-schedule-week-cell-0')).toHaveTextContent('10:00');
  });

  it('status_cancel は今週の列から消える (行ごと出ない)', () => {
    state.visits = [makeVisit({ id: 'v-status', status: 'cancelled', source: 'status_cancel' })];
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);
    expect(screen.queryByTestId('patient-schedule-week-cell-0')).not.toBeInTheDocument();
    expect(screen.getByText('この週には固定枠も今週 visit もありません。')).toBeInTheDocument();
  });

  it('「非稼働を表示」トグルの影響を受けない (型へ反映の入力なので常に除外)', () => {
    // トグルは盤面の見え方だけを変える。この比較表は BE の「型へ反映」の入力
    // そのもので、BE は連動取消を必ず除外する (design §3-4)。
    state.visits = [makeVisit({ id: 'v-status', status: 'cancelled', source: 'status_cancel' })];
    useUIStore.setState({ showInactiveVisits: true });
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);
    expect(screen.queryByTestId('patient-schedule-week-cell-0')).not.toBeInTheDocument();
  });

  it('通常訪問と混在しても status_cancel の曜日だけ落ちる', () => {
    state.visits = [
      makeVisit({ id: 'v-plain' }),
      makeVisit({
        id: 'v-status',
        visit_date: '2026-09-08',
        status: 'cancelled',
        source: 'status_cancel',
      }),
    ];
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);
    expect(screen.getByTestId('patient-schedule-week-cell-0')).toHaveTextContent('10:00');
    expect(screen.queryByTestId('patient-schedule-week-cell-1')).not.toBeInTheDocument();
  });
});

describe('PatientScheduleDetailDialog — ステータス表示', () => {
  it('生値ではなく日本語ラベルを出す (admitted → 入院中)', () => {
    state.patientStatus = 'admitted';
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);
    expect(screen.getByText('入院中')).toBeInTheDocument();
    expect(screen.queryByText('admitted')).not.toBeInTheDocument();
  });

  it('active → 稼働中', () => {
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);
    expect(screen.getByText('稼働中')).toBeInTheDocument();
  });
});
