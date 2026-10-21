/**
 * スケジュール画面の「見る単位 × 曜日/週 × 形」(schedule-unit-switch-design-2026-10-21.md §2)。
 *
 * URL の `?unit=course|staff&day=mon..sat|week&view=timeline|list` と行き来する。
 * 何も無いとき・読めない値のときの既定は今と同じ「職員・週・リスト」。
 */
export type ScheduleUnit = 'course' | 'staff';
/** 0=月..5=土、または 'week'。 */
export type ScheduleDay = number | 'week';
export type ScheduleView = 'timeline' | 'list';

export interface ScheduleViewState {
  unit: ScheduleUnit;
  day: ScheduleDay;
  view: ScheduleView;
}

export const DEFAULT_SCHEDULE_VIEW_STATE: ScheduleViewState = {
  unit: 'staff',
  day: 'week',
  view: 'list',
};

const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'] as const;

/** URL の値 → 状態。足りない値は既定で埋める。 */
export function parseScheduleViewParams(params: {
  get: (key: string) => string | null;
}): ScheduleViewState {
  const unitRaw = params.get('unit');
  const dayRaw = params.get('day');
  const viewRaw = params.get('view');
  const dayIdx = DAY_KEYS.indexOf((dayRaw ?? '') as (typeof DAY_KEYS)[number]);
  return {
    unit: unitRaw === 'course' || unitRaw === 'staff' ? unitRaw : DEFAULT_SCHEDULE_VIEW_STATE.unit,
    day: dayIdx >= 0 ? dayIdx : DEFAULT_SCHEDULE_VIEW_STATE.day,
    view: viewRaw === 'timeline' || viewRaw === 'list' ? viewRaw : DEFAULT_SCHEDULE_VIEW_STATE.view,
  };
}

/** 状態 → URL の値 (他のクエリは触らない)。 */
export function writeScheduleViewParams(params: URLSearchParams, s: ScheduleViewState): void {
  params.set('unit', s.unit);
  params.set('day', typeof s.day === 'number' ? (DAY_KEYS[s.day] ?? 'week') : 'week');
  params.set('view', s.view);
}
