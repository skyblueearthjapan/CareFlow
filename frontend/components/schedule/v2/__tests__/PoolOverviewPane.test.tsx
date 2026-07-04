/**
 * PoolOverviewPane — Stage P-2 テスト.
 *
 * 検証:
 *   1. 計算ボタン押下 → pool-overview mutation が patient_ids 付きで呼ばれる
 *   2. 計算結果受信後 → delta バッジ / 投入先なしバッジが患者行に表示される
 *   3. 効果順ソートトグル → best_delta_minutes 昇順 (null 末尾 / candidate_count=0 最後尾)
 *   4. 計算前はソートトグルが disabled
 *   5. 患者 50 件超過時は先頭 50 件のみ送信 (toast.warning)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import type { PatientRead } from '@/lib/schemas/patient';

// ─── Hoisted mock state ─────────────────────────────────────────────────────

const { mockToast, mockMutationState } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  mockMutationState: {
    isPending: false,
    isSuccess: false,
    data: undefined as { items: Array<{ patient_id: string; best_delta_minutes: number | null; candidate_count: number; top_excluded_reason: string | null; best_slot: null }> } | undefined,
    mutate: vi.fn(),
  },
}));

// ─── Mocks ──────────────────────────────────────────────────────────────────

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { accessToken: 'tok', refreshToken: 'ref' }, status: 'authenticated' }),
}));

vi.mock('@/lib/queries/poolOverview', () => ({
  usePoolOverviewMutation: () => mockMutationState,
}));

vi.mock('@dnd-kit/core', () => ({
  useDroppable: () => ({ isOver: false, setNodeRef: vi.fn() }),
}));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  Inbox: () => <span aria-hidden />,
  Layers: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  Sparkles: () => <span />,
  ArrowUpDown: () => <span />,
}));

vi.mock('@/components/ui/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  PopoverTrigger: ({ children, asChild }: { children: React.ReactNode; asChild?: boolean }) =>
    asChild ? <>{children}</> : <div>{children}</div>,
  PopoverContent: ({
    children,
    'data-testid': testId,
    ...rest
  }: {
    children: React.ReactNode;
    'data-testid'?: string;
    [k: string]: unknown;
  }) => (
    <div data-testid={testId} {...rest}>
      {children}
    </div>
  ),
}));

vi.mock('@/components/ui/card', () => ({
  Card: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock('@/components/ui/badge', () => ({
  Badge: ({
    children,
    'data-testid': testId,
    title,
    ...rest
  }: {
    children: React.ReactNode;
    'data-testid'?: string;
    title?: string;
    [k: string]: unknown;
  }) => (
    <span data-testid={testId} title={title} {...rest}>
      {children}
    </span>
  ),
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({
    children,
    onClick,
    disabled,
    'data-testid': testId,
    title,
    ...rest
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    'data-testid'?: string;
    title?: string;
    [k: string]: unknown;
  }) => (
    <button onClick={onClick} disabled={disabled} data-testid={testId} title={title} {...rest}>
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

// ─── Test helpers ────────────────────────────────────────────────────────────

function makePatient(id: string, name: string): PatientRead {
  return {
    id,
    code: id,
    name,
    kana: null,
    sex: null,
    status: 'active',
    insurance: null,
    address: null,
    lat: null,
    lng: null,
    primary_office_id: null,
    sex_restriction: null,
    note: null,
    weekly_pattern: { preferred_weekdays: ['Mon'] },
    special_weekly_pattern: null,
    special_week_active: [],
    created_at: '',
    updated_at: '',
    deleted_at: null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

// ─── Import after mocks ──────────────────────────────────────────────────────

import { PoolOverviewPane, type PoolOverviewPaneHandle } from '../PoolOverviewPane';
import type { PoolCardSlotInfo } from '../PoolPanel';

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('PoolOverviewPane', () => {
  const BASE_PROPS = {
    isoYear: 2026,
    isoWeek: 27,
    officeId: 'office-1',
    renderCard: (p: PatientRead, _info: PoolCardSlotInfo) => (
      <div data-testid={`card-${p.id}`}>{p.name}</div>
    ),
  };

  beforeEach(() => {
    mockMutationState.isPending = false;
    mockMutationState.isSuccess = false;
    mockMutationState.data = undefined;
    mockMutationState.mutate = vi.fn();
    vi.clearAllMocks();
  });

  // ── 1. 計算ボタン ─────────────────────────────────────────────────────────

  it('計算ボタンが表示される', () => {
    render(
      <PoolOverviewPane {...BASE_PROPS} patients={[makePatient('p1', '田中')]} />,
    );
    expect(screen.getByTestId('pool-overview-compute-button')).toBeInTheDocument();
  });

  it('計算ボタン押下 → mutate が patient_ids 付きで呼ばれる', () => {
    const patients = [makePatient('p1', '田中'), makePatient('p2', '佐藤')];
    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} />);

    fireEvent.click(screen.getByTestId('pool-overview-compute-button'));

    expect(mockMutationState.mutate).toHaveBeenCalledOnce();
    const [payload] = mockMutationState.mutate.mock.calls[0]!;
    expect(payload.iso_year).toBe(2026);
    expect(payload.iso_week).toBe(27);
    expect(payload.office_id).toBe('office-1');
    expect(payload.patient_ids).toEqual(['p1', 'p2']);
  });

  // ── 2. バッジ表示 ─────────────────────────────────────────────────────────

  it('計算結果受信後 → delta バッジが患者行に表示される', () => {
    mockMutationState.isSuccess = true;
    mockMutationState.data = {
      items: [
        {
          patient_id: 'p1',
          best_delta_minutes: 12,
          candidate_count: 3,
          top_excluded_reason: null,
          best_slot: null,
        },
      ],
    };

    const patients = [makePatient('p1', '田中')];
    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} />);

    expect(screen.getByTestId('pool-overview-delta-badge')).toBeInTheDocument();
    expect(screen.getByTestId('pool-overview-delta-badge')).toHaveTextContent('+12分');
  });

  it('candidate_count=0 → 「投入先なし」バッジが表示される', () => {
    mockMutationState.isSuccess = true;
    mockMutationState.data = {
      items: [
        {
          patient_id: 'p1',
          best_delta_minutes: null,
          candidate_count: 0,
          top_excluded_reason: 'capacity_full',
          best_slot: null,
        },
      ],
    };

    render(<PoolOverviewPane {...BASE_PROPS} patients={[makePatient('p1', '田中')]} />);

    const badge = screen.getByTestId('pool-overview-no-slot-badge');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent('投入先なし');
    // top_excluded_reason の日本語ラベルが title に設定される
    expect(badge.getAttribute('title')).toBe('コース容量が上限');
  });

  it('未知の top_excluded_reason → そのまま title に表示 (フォールバック)', () => {
    mockMutationState.isSuccess = true;
    mockMutationState.data = {
      items: [
        {
          patient_id: 'p1',
          best_delta_minutes: null,
          candidate_count: 0,
          top_excluded_reason: 'future_unknown',
          best_slot: null,
        },
      ],
    };

    render(<PoolOverviewPane {...BASE_PROPS} patients={[makePatient('p1', '田中')]} />);
    const badge = screen.getByTestId('pool-overview-no-slot-badge');
    expect(badge.getAttribute('title')).toBe('future_unknown');
  });

  it('結果のない患者にはバッジが表示されない', () => {
    mockMutationState.isSuccess = true;
    mockMutationState.data = {
      items: [
        {
          patient_id: 'p1',
          best_delta_minutes: 5,
          candidate_count: 2,
          top_excluded_reason: null,
          best_slot: null,
        },
      ],
    };
    // p2 は result に含まれない
    const patients = [makePatient('p1', '田中'), makePatient('p2', '佐藤')];
    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} />);

    expect(screen.getAllByTestId('pool-overview-delta-badge')).toHaveLength(1);
    expect(screen.queryByTestId('pool-overview-no-slot-badge')).toBeNull();
  });

  // ── 3-4. 効果順自動ソート (PO 指示 2026-07-03: トグル廃止・結果表示後は常に効果順) ──

  it('ソートトグルは描画されない (廃止)', () => {
    render(<PoolOverviewPane {...BASE_PROPS} patients={[makePatient('p1', '田中')]} />);
    expect(screen.queryByTestId('pool-overview-sort-toggle')).toBeNull();
  });

  it('計算前は従来順のまま (自動ソートは結果表示後のみ)', () => {
    const patients = [makePatient('p2', '佐藤'), makePatient('p1', '田中')];
    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} />);
    const cards = screen
      .getAllByTestId(/^card-p/)
      .map((el) => el.getAttribute('data-testid')!.replace('card-', ''));
    expect(cards[0]).toBe('p2');
    expect(cards[1]).toBe('p1');
  });

  it('結果表示後は自動で効果順: delta 昇順 → null 末尾 → candidate_count=0 最後尾', () => {
    mockMutationState.isSuccess = true;
    mockMutationState.data = {
      items: [
        // p3: delta=null (末尾寄り)
        { patient_id: 'p3', best_delta_minutes: null, candidate_count: 2, top_excluded_reason: null, best_slot: null },
        // p4: candidate_count=0 (最後尾)
        { patient_id: 'p4', best_delta_minutes: null, candidate_count: 0, top_excluded_reason: null, best_slot: null },
        // p1: delta=5
        { patient_id: 'p1', best_delta_minutes: 5, candidate_count: 1, top_excluded_reason: null, best_slot: null },
        // p2: delta=10
        { patient_id: 'p2', best_delta_minutes: 10, candidate_count: 1, top_excluded_reason: null, best_slot: null },
      ],
    };

    const patients = [
      makePatient('p3', '鈴木'),
      makePatient('p4', '山田'),
      makePatient('p1', '田中'),
      makePatient('p2', '佐藤'),
    ];

    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} />);

    // トグル操作なしで自動的に効果順に並ぶ。
    const cards = screen
      .getAllByTestId(/^card-p/)
      .map((el) => el.getAttribute('data-testid')!.replace('card-', ''));

    // 期待: p1(delta=5) → p2(delta=10) → p3(delta=null) → p4(no-slot)
    expect(cards[0]).toBe('p1');
    expect(cards[1]).toBe('p2');
    expect(cards[2]).toBe('p3');
    expect(cards[3]).toBe('p4');
  });

  // ── 5. 50 件超過 ─────────────────────────────────────────────────────────

  it('patients > 50 件のとき先頭 50 件のみ送信し toast.warning を出す', async () => {
    const patients = Array.from({ length: 55 }, (_, i) =>
      makePatient(`p${i}`, `患者${i}`),
    );

    // mutate の onSuccess を即時実行するよう mock
    mockMutationState.mutate = vi.fn((_payload, options?: { onSuccess?: () => void }) => {
      options?.onSuccess?.();
    });

    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} />);
    fireEvent.click(screen.getByTestId('pool-overview-compute-button'));

    const [payload] = mockMutationState.mutate.mock.calls[0]!;
    expect(payload.patient_ids).toHaveLength(50);
    expect(payload.patient_ids[0]).toBe('p0');
    expect(payload.patient_ids[49]).toBe('p49');
    expect(mockToast.warning).toHaveBeenCalledWith(
      expect.stringContaining('55 名'),
    );
  });

  // ── 6. Stage P-3: 外部トリガー (forwardRef / triggerCompute) ──────────────

  it('forwardRef 経由の triggerCompute() 呼出で mutate が patient_ids 付きで実行される', () => {
    const patients = [makePatient('p1', '田中'), makePatient('p2', '佐藤')];
    const ref = React.createRef<PoolOverviewPaneHandle>();

    render(<PoolOverviewPane {...BASE_PROPS} patients={patients} ref={ref} />);

    act(() => {
      ref.current?.triggerCompute();
    });

    expect(mockMutationState.mutate).toHaveBeenCalledOnce();
    const [payload] = mockMutationState.mutate.mock.calls[0]!;
    expect(payload.iso_year).toBe(2026);
    expect(payload.iso_week).toBe(27);
    expect(payload.office_id).toBe('office-1');
    expect(payload.patient_ids).toEqual(['p1', 'p2']);
  });

  // ── W-3: 希望未登録チップ ────────────────────────────────────────────────

  it('W-3: unregisteredPatients が 1 名以上のとき「希望未登録 N名」チップが表示される', () => {
    const unregistered = [makePatient('u1', '未登録太郎'), makePatient('u2', '未登録花子')];
    render(
      <PoolOverviewPane
        {...BASE_PROPS}
        patients={[makePatient('p1', '田中')]}
        unregisteredPatients={unregistered}
      />,
    );
    const chip = screen.getByTestId('pool-unregistered-chip');
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveTextContent('希望未登録 2名');
  });

  it('W-3: チップ内に注意文言と患者名が表示され、クリックで onClickUnregisteredPatient が呼ばれる', () => {
    const unregistered = [makePatient('u1', '未登録太郎')];
    const onClickMock = vi.fn();
    render(
      <PoolOverviewPane
        {...BASE_PROPS}
        patients={[makePatient('p1', '田中')]}
        unregisteredPatients={unregistered}
        onClickUnregisteredPatient={onClickMock}
      />,
    );
    // ポップオーバーコンテンツが DOM に存在する (モック: Popover=常時展開)
    expect(screen.getByTestId('pool-unregistered-popover')).toBeInTheDocument();
    // 注意文言
    expect(screen.getByText(/希望訪問スケジュール未登録のため/)).toBeInTheDocument();
    // 患者名ボタン
    const btn = screen.getByTestId('pool-unregistered-patient-u1');
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveTextContent('未登録太郎');
    fireEvent.click(btn);
    expect(onClickMock).toHaveBeenCalledWith('u1');
  });

  it('W-3: unregisteredPatients が 0 名のときチップを表示しない', () => {
    render(
      <PoolOverviewPane
        {...BASE_PROPS}
        patients={[makePatient('p1', '田中')]}
        unregisteredPatients={[]}
      />,
    );
    expect(screen.queryByTestId('pool-unregistered-chip')).not.toBeInTheDocument();
  });
});
