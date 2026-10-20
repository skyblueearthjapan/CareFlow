/**
 * /admin/leave の試験の月 (2026 年 11 月) の材料。名前は架空 (職員A〜D)。
 * 11/1 は日曜。11/3 文化の日・11/23 勤労感謝の日。週は 11/15 まで作り済み。
 */
import type {
  LeaveMonthCell,
  LeaveMonthDay,
  LeaveMonthStaff,
  LeaveMonthView,
  LeavePendingItem,
} from '@/lib/schemas/leave';

export const S1 = '11111111-1111-4111-8111-111111111111';
export const S2 = '22222222-2222-4222-8222-222222222222';
export const S3 = '33333333-3333-4333-8333-333333333333';
export const S4 = '44444444-4444-4444-8444-444444444444';
export const P1 = 'aaaaaaa1-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
export const P2 = 'aaaaaaa2-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
export const P3 = 'aaaaaaa3-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
export const P4 = 'aaaaaaa4-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

const HOLIDAYS: Record<string, string> = {
  '2026-11-03': '文化の日',
  '2026-11-23': '勤労感謝の日',
};

function iso(d: number): string {
  return `2026-11-${String(d).padStart(2, '0')}`;
}

function days(): LeaveMonthDay[] {
  return Array.from({ length: 30 }, (_, i) => {
    const date = iso(i + 1);
    return {
      date,
      // 11/1 は日曜 = 6 (0=月)
      weekday: (i + 6) % 7,
      holiday_name: HOLIDAYS[date] ?? null,
      closed_label: null,
      week_generated: i + 1 <= 15,
      off_count: date === '2026-11-10' ? 2 : 0,
      off_warn: date === '2026-11-10',
      pending_count:
        date === '2026-11-12' ? 2 : date === '2026-11-16' || date === '2026-11-17' ? 1 : 0,
    };
  });
}

function staff(
  over: Partial<LeaveMonthStaff> & Pick<LeaveMonthStaff, 'id' | 'name' | 'order'>,
): LeaveMonthStaff {
  return {
    code: null,
    employment_type: 'full_time',
    is_full_time: true,
    is_trainee: false,
    status: 'active',
    off_weekdays: [5, 6],
    holidays: 11,
    required_holidays: 11,
    shortfall: 0,
    paid_days: 0,
    pending_count: 0,
    pending_gain: 0,
    confirmed_at: null,
    ...over,
  };
}

function cell(
  over: Partial<LeaveMonthCell> & Pick<LeaveMonthCell, 'staff_id' | 'date'>,
): LeaveMonthCell {
  return {
    leave_type: null,
    source: null,
    note: null,
    approved_request_id: null,
    time_change_start: null,
    time_change_end: null,
    pending: null,
    cancelled_after_approval: false,
    visit_count: 0,
    send_status: null,
    ...over,
  };
}

function pending(
  over: Partial<LeavePendingItem> & Pick<LeavePendingItem, 'id' | 'staff_id' | 'date'>,
): LeavePendingItem {
  return {
    staff_name: '',
    staff_order: 0,
    override_type: 'off',
    kind: 'normal',
    note: null,
    created_at: '2026-10-15T00:00:00Z',
    current_leave_type: null,
    past_day: false,
    move_visit_count: 0,
    off_count_now: 0,
    off_count_if_approved: 1,
    ...over,
  };
}

export function makeView(over: Partial<LeaveMonthView> = {}): LeaveMonthView {
  return {
    month: '2026-11-01',
    today: '2026-10-21',
    required_holidays: 11,
    required_hours: null,
    previous_required_hours: 160,
    off_warn_threshold: 2,
    days: days(),
    staff: [
      staff({
        id: S1,
        name: '職員A',
        order: 1,
        code: '01',
        holidays: 8,
        shortfall: 3,
        paid_days: 0,
        pending_count: 1,
        pending_gain: 1,
      }),
      staff({ id: S2, name: '職員B', order: 2, paid_days: 1, pending_count: 1, pending_gain: 0.5 }),
      staff({
        id: S3,
        name: '職員C',
        order: 3,
        employment_type: 'part_time',
        is_full_time: false,
        off_weekdays: [2, 5, 6],
        holidays: 13,
        required_holidays: null,
        shortfall: null,
        pending_count: 2,
      }),
      staff({ id: S4, name: '職員D', order: 4, is_trainee: true }),
    ],
    cells: [
      cell({
        staff_id: S1,
        date: '2026-11-10',
        leave_type: 'off',
        source: 'request',
        send_status: 'unsent',
      }),
      cell({
        staff_id: S2,
        date: '2026-11-10',
        leave_type: 'off',
        source: 'request',
        send_status: 'baseline',
      }),
      cell({
        staff_id: S1,
        date: '2026-11-11',
        leave_type: 'am_off',
        source: 'admin',
        visit_count: 2,
      }),
      cell({
        staff_id: S2,
        date: '2026-11-11',
        leave_type: 'pm_off',
        source: 'kaipoke',
        send_status: 'sent',
      }),
      cell({
        staff_id: S2,
        date: '2026-11-13',
        leave_type: 'paid_off',
        source: 'admin',
        send_status: 'kaipoke_action',
      }),
      cell({
        staff_id: S1,
        date: '2026-11-12',
        visit_count: 3,
        pending: { request_id: P1, override_type: 'off', kind: 'normal' },
      }),
      cell({
        staff_id: S2,
        date: '2026-11-12',
        visit_count: 1,
        pending: { request_id: P2, override_type: 'pm_off', kind: 'change' },
      }),
      cell({
        staff_id: S3,
        date: '2026-11-16',
        pending: { request_id: P3, override_type: 'paid_off', kind: 'normal' },
      }),
      cell({
        staff_id: S3,
        date: '2026-11-17',
        pending: { request_id: P4, override_type: 'off', kind: 'normal' },
      }),
      cell({ staff_id: S1, date: '2026-11-05', cancelled_after_approval: true, visit_count: 1 }),
      cell({
        staff_id: S1,
        date: '2026-11-06',
        time_change_start: '09:00:00',
        time_change_end: '15:00:00',
        visit_count: 4,
      }),
    ],
    pending: [
      pending({
        id: P1,
        staff_id: S1,
        staff_name: '職員A',
        staff_order: 1,
        date: '2026-11-12',
        note: '用事',
        move_visit_count: 3,
      }),
      pending({
        id: P2,
        staff_id: S2,
        staff_name: '職員B',
        staff_order: 2,
        date: '2026-11-12',
        override_type: 'pm_off',
        kind: 'change',
        move_visit_count: 1,
      }),
      pending({
        id: P3,
        staff_id: S3,
        staff_name: '職員C',
        staff_order: 3,
        date: '2026-11-16',
        override_type: 'paid_off',
      }),
      pending({ id: P4, staff_id: S3, staff_name: '職員C', staff_order: 3, date: '2026-11-17' }),
    ],
    holidays: Object.entries(HOLIDAYS).map(([date, name]) => ({ date, name })),
    closed_days: [],
    confirmation: {
      confirmed: false,
      confirmed_staff_count: 0,
      staff_count: 4,
      last_confirmed_at: null,
      changed_since_confirm: false,
    },
    compare: {
      last_compared_at: null,
      last_compare_run_id: null,
      diff_count: null,
      planned_send_count: null,
      unconfirmed_days: null,
      last_leave_changed_at: null,
      changed_since_compare: false,
      baseline_pending_count: 5,
    },
    visits_on_leave_count: 3,
    ...over,
  };
}
