/**
 * PoolCandidateList — ③ 単体MVP テスト.
 *
 * 検証:
 *   1. 初期は「他の空き枠も見る」ボタンのみ. 押すと propose-slots を当該患者で呼ぶ
 *      (existing_patient_id / office_ids / 希望条件が乗る).
 *   2. 候補スロットがランキング表示される.
 *   3. 候補の「この枠で採用」→ 確認 → fixed-visits マージ確定 (PUT) が、 採用枠を含む
 *      body で呼ばれる (他曜日保持の確定経路).
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    proposeMutate: vi.fn(),
    proposeData: undefined as unknown,
    confirmMutate: vi.fn(),
    existingFixedVisits: [] as unknown[],
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  CheckCircle2: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  Plus: () => <span />,
  Sparkles: () => <span />,
  X: () => <span />,
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({
    data: { accessToken: 'tok', refreshToken: 'ref' },
    status: 'authenticated',
  }),
}));

vi.mock('@tanstack/react-query', () => ({
  // course-templates 並列取得は本テストでは空 (course_template_id 解決は null).
  useQueries: () => [],
}));

vi.mock('@/lib/api/fetcher', () => ({ fetcher: vi.fn() }));

vi.mock('@/components/ui/alert', () => ({
  Alert: ({ children, ...rest }: React.HTMLAttributes<HTMLDivElement>) => (
    <div {...rest}>{children}</div>
  ),
  AlertTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  AlertDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
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

vi.mock('@/lib/queries/fieldBoard', () => ({
  useProposeSlots: () => ({
    mutate: mocks.proposeMutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    data: mocks.proposeData,
  }),
  proposeWarningLabel: (code: string) => code,
}));

vi.mock('@/lib/queries/propose_confirm', () => ({
  useConfirmFixedVisits: () => ({ mutate: mocks.confirmMutate, isPending: false }),
}));

vi.mock('@/lib/queries/patient_fixed_visits', () => ({
  useFixedVisits: () => ({
    data: mocks.existingFixedVisits,
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
}));

vi.mock('@/lib/schemas/patient', () => ({
  coerceWeeklyPattern: () => ({
    frequency_per_week: 1,
    visit_frequency: null,
    preferred_weekdays: ['Tue'],
    service_minutes: 35,
    time_type: '固定',
    preferred_start: '16:00',
    preferred_end: null,
  }),
}));

import { PoolCandidateList } from '../PoolCandidateList';

const PATIENT = {
  id: '22222222-2222-4222-8222-222222222222',
  name: '中尾 要太',
  address: '千葉県千葉市花見川区検見川町1-52',
  lat: 35.66,
  lng: 140.12,
  sex_restriction: null,
  weekly_pattern: {},
} as unknown as Parameters<typeof PoolCandidateList>[0]['patient'];

function makeSlot(over: Record<string, unknown> = {}) {
  return {
    office_id: '11111111-1111-4111-8111-111111111111',
    office_name: '稲毛',
    weekday: 1, // 火
    weekday_code: 'Tue',
    course_code: 'C',
    course_label: '稲C',
    staff_name: '山田',
    start_time: '14:00:00',
    end_time: '14:35:00',
    score: 80,
    reasons: ['近接'],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    ...over,
  };
}

const COMMON = {
  patient: PATIENT,
  isoYear: 2026,
  isoWeek: 24,
  officeId: '11111111-1111-4111-8111-111111111111',
  canEdit: true,
};

describe('PoolCandidateList (③ 単体MVP)', () => {
  beforeEach(() => {
    mocks.proposeMutate.mockReset();
    mocks.confirmMutate.mockReset();
    mocks.proposeData = undefined;
    mocks.existingFixedVisits = [];
    mockToast.success.mockReset();
    mockToast.error.mockReset();
  });

  it('初期はボタンのみ. 押すと当該患者で propose-slots を呼ぶ', () => {
    render(<PoolCandidateList {...COMMON} />);
    const btn = screen.getByTestId('pool-candidate-run-button');
    fireEvent.click(btn);
    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    const req = mocks.proposeMutate.mock.calls[0][0];
    expect(req.existing_patient_id).toBe(PATIENT.id);
    expect(req.office_ids).toEqual([COMMON.officeId]);
    expect(req.iso_year).toBe(2026);
    expect(req.iso_week).toBe(24);
    // 固定希望なので preferred_start が乗る.
    expect(req.preferred_start).toBe('16:00');
  });

  it('候補スロットがランキング表示される (コース当日スケジュール + 挿入位置を含む)', () => {
    mocks.proposeData = {
      slots: [
        makeSlot({
          mini_schedule: [
            { time: '13:00', name: '既存A', ins: null, is_here: false, is_pair: false },
            { time: '14:00', name: '(提案)', ins: null, is_here: true, is_pair: false },
          ],
        }),
      ],
      message: null,
    };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('pool-candidate-slots')).toBeInTheDocument();
    // 稲C は候補ヘッダ + ミニスケジュール見出しの 2 箇所に出る.
    expect(screen.getAllByText(/稲C/).length).toBeGreaterThan(0);
    expect(screen.getByText(/担当: 山田/)).toBeInTheDocument();
    // コース当日の既存訪問 + 「ここに入れますか」挿入位置が見える.
    expect(screen.getByText('既存A')).toBeInTheDocument();
    expect(screen.getByText('ここに入れますか')).toBeInTheDocument();
  });

  it('採用 → 確認 → fixed-visits マージ確定が採用枠を含む body で呼ばれる', async () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));

    const adoptBtn = screen.getByTestId(
      `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
    );
    fireEvent.click(adoptBtn);
    // 確認バーが出る.
    expect(screen.getByTestId('pool-candidate-confirm')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const arg = mocks.confirmMutate.mock.calls[0][0];
    expect(arg.patientId).toBe(PATIENT.id);
    expect(arg.body.mode).toBe('normal');
    // 既存枠なし → 採用枠 1 件のみ.
    expect(arg.body.items).toHaveLength(1);
    expect(arg.body.items[0].weekday).toBe(1);
    expect(arg.body.items[0].start_time).toBe('14:00:00');
    expect(arg.body.items[0].duration_min).toBe(35); // 14:00-14:35.
  });

  it('採用時、他曜日/他slotの既存固定枠は保持され、採用曜日のslot0のみ置換される', async () => {
    const slot = makeSlot(); // 火(weekday=1) slot0 を採用.
    mocks.proposeData = { slots: [slot], message: null };
    // 既存: 月(0,slot0) / 木(3,slot0) / 火(1,slot1=2名体制相方) / 火(1,slot0=置換対象).
    mocks.existingFixedVisits = [
      { weekday: 0, start_time: '09:00:00', duration_min: 30, slot_index: 0 },
      { weekday: 3, start_time: '10:00:00', duration_min: 60, slot_index: 0 },
      { weekday: 1, start_time: '08:00:00', duration_min: 30, slot_index: 1 },
      { weekday: 1, start_time: '08:00:00', duration_min: 30, slot_index: 0 },
    ];
    render(<PoolCandidateList {...COMMON} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    fireEvent.click(
      screen.getByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    );
    fireEvent.click(screen.getByTestId('pool-candidate-confirm-apply'));

    await waitFor(() => expect(mocks.confirmMutate).toHaveBeenCalledTimes(1));
    const items = mocks.confirmMutate.mock.calls[0][0].body.items as Array<{
      weekday: number;
      slot_index: number;
      start_time: string;
    }>;
    // 月(0)・木(3) は保持.
    expect(items.find((i) => i.weekday === 0 && i.slot_index === 0)).toBeTruthy();
    expect(items.find((i) => i.weekday === 3 && i.slot_index === 0)).toBeTruthy();
    // 火 slot1 (相方) は保持.
    expect(items.find((i) => i.weekday === 1 && i.slot_index === 1)).toBeTruthy();
    // 火 slot0 は採用枠で置換 (14:00, 既存 08:00 ではない).
    const tueSlot0 = items.filter((i) => i.weekday === 1 && i.slot_index === 0);
    expect(tueSlot0).toHaveLength(1);
    expect(tueSlot0[0]!.start_time).toBe('14:00:00');
    // 合計 4 件 (月/木/火slot1 保持 + 火slot0 置換).
    expect(items).toHaveLength(4);
  });

  it('canEdit=false では採用ボタンを出さない', () => {
    const slot = makeSlot();
    mocks.proposeData = { slots: [slot], message: null };
    render(<PoolCandidateList {...COMMON} canEdit={false} />);
    fireEvent.click(screen.getByTestId('pool-candidate-run-button'));
    expect(screen.getByTestId('pool-candidate-slots')).toBeInTheDocument();
    expect(
      screen.queryByTestId(
        `pool-candidate-adopt-${slot.office_id}-${slot.weekday}-${slot.course_code}-${slot.start_time}`,
      ),
    ).not.toBeInTheDocument();
  });
});
