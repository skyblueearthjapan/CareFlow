/**
 * Wave 27 Phase B-1: useWeekStaffEvents / buildStaffEventsMap 単体テスト.
 *
 * カバーするシナリオ:
 *   1. buildStaffEventsMap — staffIds と events[] が対応する Map を返す
 *   2. buildStaffEventsMap — events が空配列の staff は空 Map エントリになる
 *   3. buildStaffEventsMap — staffIds 順と data 順が一致する
 */
import { describe, it, expect } from 'vitest';

import { buildStaffEventsMap } from '../staff-events';
import type { EventRead } from '@/lib/schemas/staff-events';

const makeEvent = (overrides: Partial<EventRead>): EventRead => ({
  id: 'event-1',
  date: '2026-05-04',
  title: '研修',
  start_time: '10:00',
  end_time: '12:00',
  type: '研修',
  ...overrides,
});

describe('buildStaffEventsMap (Wave 27 Phase B-1)', () => {
  it('1. staffIds と events[] が対応する Map を返す', () => {
    const staffIds = ['staff-a', 'staff-b'];
    const ev1 = makeEvent({ id: 'ev-1' });
    const ev2 = makeEvent({ id: 'ev-2', type: 'イベント' });
    const result = buildStaffEventsMap(staffIds, [[ev1], [ev2]]);

    expect(result.get('staff-a')).toEqual([ev1]);
    expect(result.get('staff-b')).toEqual([ev2]);
  });

  it('2. events が空配列の staff は空 Map エントリになる', () => {
    const staffIds = ['staff-x'];
    const result = buildStaffEventsMap(staffIds, [[]]);
    expect(result.get('staff-x')).toEqual([]);
  });

  it('3. staffIds 順と data 順が一致する', () => {
    const staffIds = ['s1', 's2', 's3'];
    const ev = makeEvent({});
    const result = buildStaffEventsMap(staffIds, [[], [ev], []]);
    expect(result.get('s1')).toEqual([]);
    expect(result.get('s2')).toEqual([ev]);
    expect(result.get('s3')).toEqual([]);
  });

  it('4. staffIds にない key は Map に含まれない', () => {
    const staffIds = ['s1'];
    const result = buildStaffEventsMap(staffIds, [[makeEvent({})]]);
    expect(result.has('s-unknown')).toBe(false);
    expect(result.size).toBe(1);
  });
});
