/** 受け入れ枠「月で見る」の応答の見本 (試験用・月曜と火曜の 4 マスだけ)。 */
import type { MonthCell, MonthPayload } from '@/lib/acceptanceMonth';

function cell(weekday: number, slot: string, over: Partial<MonthCell> = {}): MonthCell {
  return {
    weekday,
    slot,
    mark: '×',
    ok: 1,
    counted: 4,
    total: 4,
    per_week: [1, 0, 0, 0],
    free_avg: 0.25,
    work_min: 240,
    busy_min: 96,
    utilization: 0.4,
    crowded: false,
    starts: 4,
    starts_avg: 1,
    busy_staff_avg: 0.5,
    pairs: 2,
    tight_ratio: 0.5,
    fixed_ratio: 0.25,
    prev_utilization: 0.35,
    trend_points: 5,
    trend: null,
    ...over,
  };
}

export function monthPayload(over: Partial<MonthPayload> = {}): MonthPayload {
  return {
    month: '2026-11',
    prev_month: '2026-10',
    office_id: null,
    offices: [{ id: 'o1', name: '拠点イ' }],
    today: '2026-11-01',
    source: 'plan',
    mode: 'real',
    lunch: true,
    weekdays: [0, 1],
    slots: ['09:30', '10:00'],
    rules: {
      visit_min: 35,
      cap: 7,
      buffer_min: 8,
      travel_guess_min: 12,
      lunch_min: 60,
      lunch_from: '11:30',
      lunch_to: '13:30',
      lunch_considered: true,
      slot_min: 30,
      crowded_utilization: 0.7,
      trend_points: 10,
      busy_day_visits: 6,
      open_day_min_visits: 5,
    },
    staff: [
      { id: 's1', name: '職員A', kind: 'staff', default_selected: true, office_id: 'o1' },
      { id: 'm1', name: '管理者M', kind: 'manager', default_selected: false, office_id: 'o1' },
    ],
    selected_staff_ids: ['s1'],
    days: [{ date: '2026-11-02', open: true }],
    visit_count: 120,
    prev_utilization: 0.38,
    utilization: 0.46,
    cells: {
      '0-09:30': cell(0, '09:30', { mark: '○', ok: 4, per_week: [1, 1, 2, 1] }),
      '0-10:00': cell(0, '10:00', {
        utilization: 0.82,
        crowded: true,
        trend: '▲',
        trend_points: 12,
      }),
      '1-09:30': cell(1, '09:30', { mark: '－', counted: 1, utilization: null }),
      '1-10:00': cell(1, '10:00', { trend: '▼', trend_points: -15 }),
    },
    weekday_summary: [
      { weekday: 0, days: 4, total: 5, visits_per_day: 29.8, utilization: 0.5 },
      { weekday: 1, days: 4, total: 4, visits_per_day: 28.5, utilization: 0.42 },
    ],
    weekday_summary_all: [
      { weekday: 0, days: 4, total: 5, visits_per_day: 41.3, utilization: 0.47 },
      { weekday: 1, days: 4, total: 4, visits_per_day: 39.0, utilization: 0.4 },
    ],
    peaks: {
      utilization: [{ weekday: 0, slot: '10:00', value: 0.82 }],
      starts: [{ weekday: 0, slot: '10:00', value: 3 }],
    },
    counts: { ok: 1, mid: 0, ng: 2, none: 1, crowded: 1 },
    ...over,
  };
}
