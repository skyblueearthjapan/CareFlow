/**
 * SpecialVisitPoolSection / SpecialTicketPlacePanel — 特別訪問週間のプール統合テスト.
 *
 * 正典: `docs/plans/special-visit-week-design.md` §6-2 (Wave2)。
 *
 * 検証:
 *   1. チケットが専用セクションに表示される (0 件のときはセクションごと非表示)
 *   2. チケット展開で propose-slots が呼ばれ、preferred_weekdays がチケット曜日になる
 *   3. last_placement があれば「前回はここでした」ヒントが出る
 *   4. 「この枠に配置」で place mutation が (office_id + course_code + start_time) で呼ばれる
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { mockToast, mocks } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  mocks: {
    poolTickets: [] as unknown[],
    proposeMutate: vi.fn(),
    proposeData: undefined as unknown,
    proposePending: false,
    placeMutate: vi.fn(),
    patientData: undefined as unknown,
  },
}));

vi.mock('sonner', () => ({ toast: mockToast }));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  CalendarDays: () => <span />,
  Loader2: () => <span data-testid="loader" />,
  Star: () => <span />,
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

vi.mock('../SpecialVisitWeekDialog', () => ({
  SpecialVisitWeekDialog: ({ patientName }: { patientName: string }) => (
    <div data-testid="svw-dialog-stub">{patientName}</div>
  ),
}));

vi.mock('@/lib/queries/specialVisitWeek', () => ({
  useSpecialVisitPool: () => ({ data: mocks.poolTickets, isLoading: false, isError: false }),
  usePlaceSpecialMark: () => ({ mutate: mocks.placeMutate, isPending: false }),
}));

vi.mock('@/lib/queries/fieldBoard', () => ({
  useProposeSlots: () => ({
    mutate: mocks.proposeMutate,
    isPending: mocks.proposePending,
    isError: false,
    data: mocks.proposeData,
  }),
  proposeWarningLabel: (code: string) => code,
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatient: () => ({ data: mocks.patientData, isLoading: false, isError: false }),
}));

vi.mock('@/lib/schemas/patient', () => ({
  coerceWeeklyPattern: () => ({
    frequency_per_week: 1,
    visit_frequency: null,
    visit_weeks: null,
    preferred_weekdays: ['Mon'],
    service_minutes: 45,
    time_type: '固定',
    preferred_start: '14:00',
    preferred_end: null,
    ng_weekdays: null,
  }),
}));

import { SpecialVisitPoolSection } from '../SpecialTicketPlacePanel';

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';
const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const MARK_ID = '33333333-3333-4333-8333-333333333333';

function makeTicket(over: Record<string, unknown> = {}) {
  return {
    mark: {
      id: MARK_ID,
      period_id: '44444444-4444-4444-8444-444444444444',
      patient_id: PATIENT_ID,
      iso_year: 2026,
      iso_week: 31,
      // 3 = 木曜.
      weekday: 3,
      kind: 'extra',
      status: 'pool',
      placed_visit_id: null,
      placed_summary: null,
    },
    patient: {
      id: PATIENT_ID,
      name: '中尾 要太',
      code: 'P-001',
      sex: 'male',
      sex_restriction: null,
      requires_multiple_staff: false,
      lat: 35.66,
      lng: 140.12,
      primary_office_id: OFFICE_ID,
    },
    period: {
      id: '44444444-4444-4444-8444-444444444444',
      weekly_target: 5,
      end_date: '2026-08-16',
    },
    last_placement: null,
    ...over,
  };
}

function makeSlot(over: Record<string, unknown> = {}) {
  return {
    office_id: OFFICE_ID,
    office_name: '稲毛',
    weekday: 3,
    weekday_code: 'Thu',
    course_code: 'A',
    course_label: '稲毛A',
    staff_name: '山田 花子',
    start_time: '14:00:00',
    end_time: '14:45:00',
    score: 91,
    reasons: [],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    is_efficiency_alternative: false,
    marginal_cost_minutes: 5,
    overcapacity: false,
    event_conflicts: [],
    ...over,
  };
}

function renderSection(canEdit = true) {
  return render(
    <SpecialVisitPoolSection isoYear={2026} isoWeek={31} officeId={OFFICE_ID} canEdit={canEdit} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.poolTickets = [];
  mocks.proposeData = undefined;
  mocks.proposePending = false;
  mocks.patientData = { id: PATIENT_ID, address: '千葉県千葉市稲毛区1-1', weekly_pattern: {} };
});

describe('SpecialVisitPoolSection', () => {
  it('チケットが 0 件のときはセクションごと描画しない', () => {
    mocks.poolTickets = [];
    renderSection();
    expect(screen.queryByTestId('special-visit-pool-section')).toBeNull();
  });

  it('チケットを専用セクションに「◯◯様・木曜」+ 種別 + 目標つきで表示する', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection();

    expect(screen.getByTestId('special-visit-pool-section')).toBeTruthy();
    expect(screen.getByTestId(`special-visit-ticket-${MARK_ID}`)).toBeTruthy();
    expect(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`).textContent).toContain(
      '中尾 要太 様・木曜',
    );
    expect(screen.getByTestId(`special-visit-ticket-kind-${MARK_ID}`).textContent).toContain(
      '追加枠',
    );
    expect(screen.getByTestId('special-visit-pool-section').textContent).toContain('週5回以上');
  });

  it('固定退避チケットは「固定退避」バッジで区別する', () => {
    mocks.poolTickets = [makeTicket({ mark: { ...makeTicket().mark, kind: 'displaced' } })];
    renderSection();
    expect(screen.getByTestId(`special-visit-ticket-kind-${MARK_ID}`).textContent).toContain(
      '固定退避',
    );
  });

  it('「カレンダー」ボタンで設定モーダルを開く', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection();
    expect(screen.queryByTestId('svw-dialog-stub')).toBeNull();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-calendar-${MARK_ID}`));
    expect(screen.getByTestId('svw-dialog-stub').textContent).toContain('中尾 要太');
  });
});

describe('SpecialTicketPlacePanel', () => {
  it('チケット展開で propose-slots がチケット曜日 (preferred_weekdays) と対象週で呼ばれる', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection();

    expect(mocks.proposeMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`));

    expect(mocks.proposeMutate).toHaveBeenCalledTimes(1);
    const payload = mocks.proposeMutate.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.preferred_weekdays).toEqual(['Thu']);
    expect(payload.iso_year).toBe(2026);
    expect(payload.iso_week).toBe(31);
    expect(payload.existing_patient_id).toBe(PATIENT_ID);
    expect(payload.office_ids).toEqual([OFFICE_ID]);
    expect(payload.service_minutes).toBe(45);
    expect(payload.limit).toBe(5);
  });

  it('last_placement があれば「前回はここでした」ヒントを候補の上に出す', () => {
    mocks.poolTickets = [
      makeTicket({
        last_placement: {
          weekday: 1,
          start_time: '14:00:00',
          course_label: '稲毛A',
          staff_name: '山田 花子',
        },
      }),
    ];
    mocks.proposeData = { slots: [makeSlot()] };
    renderSection();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`));

    const hint = screen.getByTestId('special-ticket-last-placement');
    expect(hint.textContent).toContain('前回はここでした');
    expect(hint.textContent).toContain('火曜');
    expect(hint.textContent).toContain('14:00');
    expect(hint.textContent).toContain('稲毛A');
    expect(hint.textContent).toContain('山田 花子');
  });

  it('「この枠に配置」で place mutation が (office_id + course_code + start_time) で呼ばれる', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    mocks.poolTickets = [makeTicket()];
    mocks.proposeData = { slots: [makeSlot()] };
    renderSection();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`));

    const key = `${OFFICE_ID}-A-3-14:00`;
    fireEvent.click(screen.getByTestId(`special-ticket-place-${key}`));

    expect(confirmSpy).toHaveBeenCalled();
    expect(mocks.placeMutate).toHaveBeenCalledTimes(1);
    const [vars] = mocks.placeMutate.mock.calls[0] as [
      { markId: string; payload: Record<string, unknown> },
    ];
    expect(vars.markId).toBe(MARK_ID);
    expect(vars.payload).toEqual({
      office_id: OFFICE_ID,
      course_code: 'A',
      start_time: '14:00',
    });
    confirmSpy.mockRestore();
  });

  it('確認をキャンセルしたら place を呼ばない', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    mocks.poolTickets = [makeTicket()];
    mocks.proposeData = { slots: [makeSlot()] };
    renderSection();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`));
    fireEvent.click(screen.getByTestId(`special-ticket-place-${OFFICE_ID}-A-3-14:00`));
    expect(mocks.placeMutate).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('座標も住所も無い患者は「住所未確定のため提案できません」を出し propose を呼ばない', () => {
    mocks.patientData = { id: PATIENT_ID, address: '', weekly_pattern: {} };
    mocks.poolTickets = [
      makeTicket({ patient: { ...makeTicket().patient, lat: null, lng: null } }),
    ];
    renderSection();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`));

    expect(screen.getByTestId('special-ticket-no-location')).toBeTruthy();
    expect(mocks.proposeMutate).not.toHaveBeenCalled();
  });

  it('canEdit=false のときは配置ボタンを出さない', () => {
    mocks.poolTickets = [makeTicket()];
    mocks.proposeData = { slots: [makeSlot()] };
    renderSection(false);
    fireEvent.click(screen.getByTestId(`special-visit-ticket-toggle-${MARK_ID}`));
    expect(screen.queryByTestId(`special-ticket-place-${OFFICE_ID}-A-3-14:00`)).toBeNull();
  });
});
