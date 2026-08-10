/**
 * PatientScheduleDetailDialog — プール投入提案セクション (Pool-detail 統合) テスト.
 *
 * 目的 (ユーザー懸念への回答 / Phase G-113):
 *   保留プールの患者を 1 人クリックして開く詳細ダイアログの「プール投入の提案」は、
 *   diff-add (全員分バッチ最適 → 1 人抽出) ではなく **その 1 人だけ** を確定済
 *   スケジュールに対して空き探索する propose-slots (PoolCandidateList primary) を
 *   主提案にする。diff-add は他プール患者との架空競合で当該患者が弾かれ得るため
 *   単体では使わない (一括ダイアログ DiffAddDialog は従来どおり全員バッチ)。
 *
 *   本テストは「ダイアログが PoolCandidateList を primary で配線し、enablePoolProposal
 *   ゲート・採用後の成功表示が正しく動く」ことを施錠する。propose-slots 候補列挙の
 *   詳細挙動は PoolCandidateList.test.tsx で別途検証する (ここでは stub)。
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ─── 共通モック ─────────────────────────────────────────────────────────────

const { mockToast, poolCandidateProps } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  // PoolCandidateList stub が受け取った最新 props を記録する (primary 等の照合用).
  poolCandidateProps: { current: null as Record<string, unknown> | null },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

// enablePoolProposal=false 経路は BulkFixToPatternButton 系 (useSession 参照) まで
// 描画に到達するため、SessionProvider の代わりにフックをモックする。
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
// NGスタッフ (基本情報サマリの 1 行). QueryClient を持たないので hook をモックする.
vi.mock('@/lib/queries/patient_ng_staff', () => ({
  useNgStaffList: () => ({ data: [], isLoading: false, isError: false }),
}));
vi.mock('@/lib/api/patientSync', () => ({
  useSyncWeekVisitsToFixedMutation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// ネスト編集ダイアログは stub.
vi.mock('../PatientEditDialog', () => ({
  PatientEditDialog: () => <div data-testid="edit-stub" />,
}));
// 主提案 = propose-slots 候補一覧 (PoolCandidateList primary). 独自フック群
// (QueryClient 必須) を持つため stub し、受け取った props と onAdopted 発火口を露出する.
vi.mock('../PoolCandidateList', () => ({
  PoolCandidateList: (props: Record<string, unknown>) => {
    poolCandidateProps.current = props;
    return (
      <div data-testid="pool-candidate-stub" data-primary={String(props.primary)}>
        <button
          type="button"
          data-testid="pool-candidate-stub-adopt"
          onClick={() => (props.onAdopted as (() => void) | undefined)?.()}
        >
          採用
        </button>
      </div>
    );
  },
}));

// ─── Subject under test ─────────────────────────────────────────────────────

import { PatientScheduleDetailDialog } from '../PatientScheduleDetailDialog';

// ─── Fixtures ───────────────────────────────────────────────────────────────

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const OFFICE_ID = '11111111-1111-4111-8111-111111111111';

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
    mockToast.success.mockReset();
    mockToast.error.mockReset();
    poolCandidateProps.current = null;
  });

  it('プール由来クリック時、主提案として PoolCandidateList を primary で描画する', () => {
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);

    expect(screen.getByTestId('patient-schedule-pool-proposal')).toBeInTheDocument();
    const stub = screen.getByTestId('pool-candidate-stub');
    expect(stub).toBeInTheDocument();
    // 単体は「その 1 人だけ空き探索」= primary モードで渡す (= モバイル盤と同方式).
    expect(stub).toHaveAttribute('data-primary', 'true');
    // 一括ダイアログと同一の拠点スコープ / 採用 RBAC を伝播する.
    expect(poolCandidateProps.current?.officeId).toBe(OFFICE_ID);
    expect(poolCandidateProps.current?.canEdit).toBe(true);
  });

  it('採用 (onAdopted) 後は成功表示に切り替わり、候補リストは隠れる', async () => {
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} />);

    expect(screen.getByTestId('pool-candidate-stub')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pool-candidate-stub-adopt'));

    await waitFor(() =>
      expect(screen.getByTestId('patient-schedule-pool-adopted')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('pool-candidate-stub')).not.toBeInTheDocument();
  });

  // enablePoolProposal=false の分岐は「一括固定」ボタン群 (react-query 利用) まで
  // 描画が進むため QueryClientProvider が要る。プール由来の他ケースは到達しない。
  it('enablePoolProposal=false (プール由来でない場合) ではセクションを出さない', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <PatientScheduleDetailDialog {...COMMON_PROPS} enablePoolProposal={false} />
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId('patient-schedule-pool-proposal')).not.toBeInTheDocument();
    expect(screen.queryByTestId('pool-candidate-stub')).not.toBeInTheDocument();
  });

  it('canEdit=false では採用 RBAC を PoolCandidateList に伝播する (閲覧専用)', () => {
    render(<PatientScheduleDetailDialog {...COMMON_PROPS} canEdit={false} />);

    expect(screen.getByTestId('pool-candidate-stub')).toBeInTheDocument();
    expect(poolCandidateProps.current?.canEdit).toBe(false);
  });
});
