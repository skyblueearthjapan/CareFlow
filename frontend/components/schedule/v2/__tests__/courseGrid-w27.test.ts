/**
 * Wave 27 Phase B-2/B-3: courseGrid event helpers 単体テスト.
 *
 * カバーするシナリオ:
 *   1. getStaffEventsForWeekday — 月曜(0) に月曜日付の event を返す
 *   2. getStaffEventsForWeekday — 別曜日の event は返さない
 *   3. getStaffEventsForWeekday — staffId が Map にない場合は空配列
 *   4. hasEventConflict — visit slot が event 時間帯内なら event を返す
 *   5. hasEventConflict — visit slot が event 時間帯外なら null を返す
 *   6. hasEventConflict — visit slot が event 終了時刻ちょうどなら null (exclusive end)
 *   7. hasEventConflict — events が空なら null を返す
 */
import { describe, it, expect } from 'vitest';

import { getStaffEventsForWeekday, hasEventConflict } from '../courseGrid';
import type { EventRead } from '@/lib/schemas/staff-events';

const makeEvent = (overrides: Partial<EventRead>): EventRead => ({
  id: 'event-1',
  date: '2026-05-04', // 月曜 (getDay=1 → weekday=0)
  title: '研修',
  start_time: '10:00',
  end_time: '12:00',
  type: '研修',
  ...overrides,
});

describe('getStaffEventsForWeekday (Wave 27 Phase B-2)', () => {
  it('1. 月曜(weekday=0) に月曜日付の event を返す', () => {
    const ev = makeEvent({ date: '2026-05-04' }); // 月曜
    const m = new Map([['staff-1', [ev]]]);
    const result = getStaffEventsForWeekday('staff-1', 0, m);
    expect(result).toHaveLength(1);
    expect(result[0]).toBe(ev);
  });

  it('2. 火曜(weekday=1) を指定すると月曜の event は返さない', () => {
    const ev = makeEvent({ date: '2026-05-04' }); // 月曜
    const m = new Map([['staff-1', [ev]]]);
    const result = getStaffEventsForWeekday('staff-1', 1, m);
    expect(result).toHaveLength(0);
  });

  it('3. staffId が Map にない場合は空配列を返す', () => {
    const m = new Map<string, EventRead[]>();
    const result = getStaffEventsForWeekday('unknown', 0, m);
    expect(result).toEqual([]);
  });
});

describe('hasEventConflict (Wave 27 Phase B-3)', () => {
  it('4. visit slot が event 時間帯内なら event を返す', () => {
    const ev = makeEvent({ start_time: '10:00', end_time: '12:00' });
    const result = hasEventConflict('10:30', [ev]);
    expect(result).toBe(ev);
  });

  it('5. visit slot が event 時間帯前なら null を返す', () => {
    const ev = makeEvent({ start_time: '10:00', end_time: '12:00' });
    const result = hasEventConflict('09:45', [ev]);
    expect(result).toBeNull();
  });

  it('6. visit slot が event 終了時刻ちょうどなら null (exclusive end)', () => {
    const ev = makeEvent({ start_time: '10:00', end_time: '12:00' });
    const result = hasEventConflict('12:00', [ev]);
    expect(result).toBeNull();
  });

  it('7. events が空なら null を返す', () => {
    const result = hasEventConflict('10:00', []);
    expect(result).toBeNull();
  });

  it('8. 複数 event のうち最初の重複を返す', () => {
    const ev1 = makeEvent({ id: 'ev-1', start_time: '10:00', end_time: '11:00' });
    const ev2 = makeEvent({ id: 'ev-2', start_time: '10:00', end_time: '12:00' });
    const result = hasEventConflict('10:15', [ev1, ev2]);
    expect(result?.id).toBe('ev-1');
  });
});
