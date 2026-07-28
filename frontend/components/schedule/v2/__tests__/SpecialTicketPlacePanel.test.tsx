/**
 * SpecialVisitPoolSection — 特別訪問週間のプール統合テスト.
 *
 * 正典: `docs/plans/special-visit-week-design.md` §6-2 / PO 指示 2026-07-29
 * (既存 UI へ統一 = 通常プール患者カード + ポップアップ提案)。
 *
 * 検証:
 *   1. チケットが専用セクションに通常プールカードと同じ形で表示される
 *      (0 件のときはセクションごと非表示)
 *   2. ⭐ / 種別 (追加枠・固定退避) / 曜日のバッジは維持される
 *   3. カードクリックで PatientScheduleDetailDialog が特別モード props つきで開く
 *   4. 「カレンダー」ボタンは設定モーダルを開く (カードのクリックとは別導線)
 */
import * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { mocks } = vi.hoisted(() => ({
  mocks: {
    poolTickets: [] as unknown[],
    detailProps: null as Record<string, unknown> | null,
  },
}));

vi.mock('lucide-react', () => ({
  CalendarDays: () => <span />,
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

// 提案ポップアップ本体は独自フック群 (QueryClient 必須) を持つため stub し、
// 受け取った props (特に specialTicket) を露出する.
vi.mock('../PatientScheduleDetailDialog', () => ({
  PatientScheduleDetailDialog: (props: Record<string, unknown>) => {
    mocks.detailProps = props;
    return (
      <div data-testid="patient-detail-stub">
        <button
          type="button"
          data-testid="patient-detail-stub-close"
          onClick={() => (props.onClose as (() => void) | undefined)?.()}
        >
          閉じる
        </button>
      </div>
    );
  },
}));

vi.mock('@/lib/queries/specialVisitWeek', () => ({
  useSpecialVisitPool: () => ({ data: mocks.poolTickets, isLoading: false, isError: false }),
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
    service_minutes: 45,
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
  mocks.detailProps = null;
});

describe('SpecialVisitPoolSection', () => {
  it('チケットが 0 件のときはセクションごと描画しない', () => {
    mocks.poolTickets = [];
    renderSection();
    expect(screen.queryByTestId('special-visit-pool-section')).toBeNull();
  });

  it('通常プールカードと同じ形 (氏名 + コード) で表示し、⭐/種別/曜日バッジを維持する', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection();

    expect(screen.getByTestId('special-visit-pool-section')).toBeTruthy();
    const card = screen.getByTestId(`special-visit-ticket-card-${MARK_ID}`);
    expect(card.textContent).toContain('中尾 要太');
    expect(card.textContent).toContain('P-001');
    expect(card.textContent).toContain('⭐');
    expect(screen.getByTestId(`special-visit-ticket-kind-${MARK_ID}`).textContent).toContain(
      '追加枠',
    );
    expect(screen.getByTestId(`special-visit-ticket-weekday-${MARK_ID}`).textContent).toContain(
      '木曜',
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

  it('カードクリックで患者スケジュール詳細ダイアログを特別モードで開く', () => {
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
    renderSection();

    expect(screen.queryByTestId('patient-detail-stub')).toBeNull();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-card-${MARK_ID}`));

    expect(screen.getByTestId('patient-detail-stub')).toBeTruthy();
    const props = mocks.detailProps!;
    expect(props.patientId).toBe(PATIENT_ID);
    expect(props.enablePoolProposal).toBe(true);
    expect(props.officeId).toBe(OFFICE_ID);
    expect(props.canEdit).toBe(true);
    expect(props.specialTicket).toEqual({
      markId: MARK_ID,
      weekday: 3,
      isoYear: 2026,
      isoWeek: 31,
      serviceMinutes: 45,
      lastPlacement: {
        weekday: 1,
        start_time: '14:00:00',
        course_label: '稲毛A',
        staff_name: '山田 花子',
      },
    });
  });

  it('ダイアログを閉じるとポップアップが消える', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-card-${MARK_ID}`));
    expect(screen.getByTestId('patient-detail-stub')).toBeTruthy();
    fireEvent.click(screen.getByTestId('patient-detail-stub-close'));
    expect(screen.queryByTestId('patient-detail-stub')).toBeNull();
  });

  it('canEdit=false は RBAC をダイアログへ伝播する (閲覧専用)', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection(false);
    fireEvent.click(screen.getByTestId(`special-visit-ticket-card-${MARK_ID}`));
    expect(mocks.detailProps?.canEdit).toBe(false);
  });

  it('「カレンダー」ボタンで設定モーダルを開く (提案ポップアップは開かない)', () => {
    mocks.poolTickets = [makeTicket()];
    renderSection();
    expect(screen.queryByTestId('svw-dialog-stub')).toBeNull();
    fireEvent.click(screen.getByTestId(`special-visit-ticket-calendar-${MARK_ID}`));
    expect(screen.getByTestId('svw-dialog-stub').textContent).toContain('中尾 要太');
    expect(screen.queryByTestId('patient-detail-stub')).toBeNull();
  });
});
