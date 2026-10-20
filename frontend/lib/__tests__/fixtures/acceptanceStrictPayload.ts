/**
 * 受け入れ枠 (数週続けて空いているか) の試験用の応答。
 *
 * 職員A・職員B (職員)、管理M (マネージャー)、新人T (新人)。曜日は月・火だけ。
 * - 職員A: 毎日 9・10 時台に入れる (休みを無いとすると 11 時台も)。
 * - 職員B: 月曜は有給 (休みを無いとすると 14 時台に入れる)、火曜は隙間なし。
 * - 管理M: 毎日 15 時台に入れる。新人T: 入職前。
 * - 10/12 (月) は祝日。毎週の上書き: 月 9 時 = ○ (自動と同じ)、月 14 時 = ○ (自動は ×)。
 */
import type { StrictPayload } from '@/lib/acceptanceStrict';

const MONDAYS = [
  '2026-10-19',
  '2026-10-12',
  '2026-10-05',
  '2026-09-28',
  '2026-09-21',
  '2026-09-14',
  '2026-09-07',
  '2026-08-31',
];

function plus(iso: string, days: number): string {
  const [y = 0, m = 1, d = 1] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

export function makePayload(): StrictPayload {
  const dates = MONDAYS.flatMap((m) => [m, plus(m, 1)]);
  const cells: StrictPayload['cells'] = { a: {}, b: {}, m: {}, t: {} };
  for (const d of dates) {
    const isMonday = MONDAYS.includes(d);
    cells.a![d] = {
      n: 3,
      state: 'ok',
      real: { state: 'free', hours: [9, 10], why: '', why_kind: null, unknown_leave: null },
      ignore: { state: 'free', hours: [9, 10, 11], why: '', why_kind: null },
    };
    cells.b![d] = isMonday
      ? {
          n: 0,
          state: 'ok',
          real: {
            state: 'off',
            hours: [],
            why: '有給（らく助）',
            why_kind: 'full',
            unknown_leave: null,
          },
          ignore: { state: 'free', hours: [14], why: '', why_kind: null },
        }
      : {
          n: 6,
          state: 'ok',
          real: { state: 'tight', hours: [], why: '', why_kind: null, unknown_leave: null },
          ignore: { state: 'tight', hours: [], why: '', why_kind: null },
        };
    cells.m![d] = {
      n: 2,
      state: 'ok',
      real: { state: 'free', hours: [15], why: '', why_kind: null, unknown_leave: null },
      ignore: { state: 'free', hours: [15], why: '', why_kind: null },
    };
    cells.t![d] = { n: 0, state: 'not_employed' };
  }
  return {
    office_id: 'office-1',
    today: '2026-10-20',
    plan_only: false,
    weekdays: [0, 1],
    weeks: MONDAYS.map((monday, i) => ({ iso_year: 2026, iso_week: 43 - i, monday })),
    rules: {
      visit_min: 35,
      cap: 7,
      buffer_min: 8,
      travel_guess_min: 12,
      lunch_min: 60,
      lunch_from: '11:30',
      lunch_to: '13:30',
      hours: [9, 17],
    },
    staff: [
      { id: 'a', name: '職員A', kind: 'staff', default_selected: true },
      { id: 'b', name: '職員B', kind: 'staff', default_selected: true },
      { id: 'm', name: '管理M', kind: 'manager', default_selected: false },
      { id: 't', name: '新人T', kind: 'trainee', default_selected: false },
    ],
    days: dates.map((date) => ({
      date,
      state: date === '2026-10-12' ? 'holiday' : 'ok',
      holiday_name: date === '2026-10-12' ? 'スポーツの日' : null,
      src: date === '2026-10-05' ? 'plan_fallback' : 'actual',
      tasks_from: date === '2026-10-19' ? 'staff_events' : 'snapshot',
    })),
    cells,
    overrides: [
      { weekday: 0, hour: 9, time_slot: '09:00:00', status: 'available', note: null },
      {
        weekday: 0,
        hour: 14,
        time_slot: '14:00:00',
        status: 'available',
        note: '月曜の午後は受ける',
      },
    ],
    sources: {
      oldest_fetched_at: '2026-10-20T00:12:00Z',
      snapshots: [
        {
          kind: 'plan_csv',
          period_start: '2026-10-01',
          status: 'ok',
          fetched_at: '2026-10-20T00:12:00Z',
        },
      ],
      unmatched_staff_names: { 山田: 2 },
      dropped_events: 3,
      last_job: {
        id: 'job-1',
        status: 'completed',
        scope: 'recent',
        done: 3,
        total: 3,
        current: null,
        failed: [{ kind: 'tasks', period_start: '2026-10-05', reason: '取れませんでした' }],
        error: null,
        started_at: '2026-10-20T01:00:00Z',
        completed_at: '2026-10-20T01:05:00Z',
      },
    },
  };
}
