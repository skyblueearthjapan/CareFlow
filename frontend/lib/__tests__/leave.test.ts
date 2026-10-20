/**
 * lib/leave — バックエンドの app/core/leave.py と同じ値・同じ判定であること
 * (backend/tests/test_core_leave.py と同じ表)。
 */
import { describe, expect, it } from 'vitest';

import {
  AM_OFF_UNTIL,
  FULL_DAY_OFF,
  HALF_DAY_OFF,
  LEAVE_TYPES,
  PM_OFF_FROM,
  classifyKaipokeLeave,
  isLeaveEvent,
  leaveWindow,
} from '@/lib/leave';

describe('leave constants', () => {
  it('バックエンドと同じ値', () => {
    expect([...FULL_DAY_OFF].sort()).toEqual(['off', 'paid_off']);
    expect([...HALF_DAY_OFF].sort()).toEqual(['am_off', 'pm_off']);
    expect([...LEAVE_TYPES].sort()).toEqual(['am_off', 'off', 'paid_off', 'pm_off']);
    expect(LEAVE_TYPES.has('custom_time')).toBe(false);
    expect(AM_OFF_UNTIL).toBe('13:00');
    expect(PM_OFF_FROM).toBe('12:30');
  });
});

describe('classifyKaipokeLeave (勤務 9:00-18:00)', () => {
  const cases: [string, string, string, string | null][] = [
    ['休み', '9:00', '18:00', 'off'],
    ['休み', '9:00', '13:00', 'am_off'],
    ['休み', '12:30', '17:30', 'pm_off'],
    ['休み', '12:30', '18:00', 'pm_off'],
    ['休み', '9:30', '17:00', 'off'],
    ['休み', '9:31', '18:00', 'unknown'],
    ['休み', '9:00', '16:59', 'unknown'],
    ['休み', '9:00', '12:00', 'am_off'],
    ['休み', '9:00', '14:00', 'am_off'],
    ['休み', '9:00', '14:01', 'unknown'],
    ['休み', '12:00', '18:00', 'pm_off'],
    ['休み', '13:30', '17:00', 'pm_off'],
    ['休み', '13:31', '18:00', 'unknown'],
    ['休み', '11:59', '18:00', 'unknown'],
    ['休み', '10:00', '11:00', 'unknown'],
    ['夏季休暇', '9:00', '18:00', 'off'],
    ['夏季休暇', '10:00', '11:00', 'unknown'],
    ['PM休み', '12:30', '17:30', 'pm_off'],
    ['ＰＭ休み', '12:30', '17:30', 'pm_off'],
    ['午後休', '12:30', '18:00', 'pm_off'],
    ['AM休み', '9:00', '13:00', 'am_off'],
    ['午前休', '9:00', '13:00', 'am_off'],
    ['有給', '9:00', '18:00', 'paid_off'],
    ['有休', '9:00', '18:00', 'paid_off'],
    ['有給休暇', '9:00', '13:00', 'paid_off'],
    ['年休', '9:00', '18:00', 'paid_off'],
    ['代休', '9:00', '18:00', 'off'],
    ['欠勤', '9:00', '18:00', 'off'],
    ['病欠', '9:00', '18:00', 'off'],
    ['忌引', '9:00', '18:00', 'off'],
    ['公休', '9:00', '18:00', 'off'],
    ['休憩', '12:00', '13:00', null],
    ['休日出勤', '9:00', '18:00', null],
    ['休日当番', '9:00', '18:00', null],
    ['休薬の確認', '10:00', '10:30', null],
    ['朝会', '8:45', '9:00', null],
    ['', '9:00', '18:00', null],
    ['会議', '10:00', '11:00', null],
  ];
  it.each(cases)('%s %s-%s → %s', (subject, start, end, expected) => {
    expect(classifyKaipokeLeave(subject, start, end, '09:00', '18:00')).toBe(expected);
  });

  it('時刻や勤務時間が無いとき', () => {
    expect(classifyKaipokeLeave(null)).toBeNull();
    expect(classifyKaipokeLeave('休み')).toBe('off');
    expect(classifyKaipokeLeave('夏季休暇')).toBe('off');
    expect(classifyKaipokeLeave('PM休み')).toBe('pm_off');
    expect(classifyKaipokeLeave('休み', '9:30', '18:00')).toBe('off');
    expect(classifyKaipokeLeave('休み', '9:00', '13:00')).toBe('am_off');
    expect(classifyKaipokeLeave('休み', '12:30', '17:30')).toBe('pm_off');
    expect(classifyKaipokeLeave('休み', '10:01', '18:00')).toBe('unknown');
    expect(classifyKaipokeLeave('休み', '8:00', '16:00', null, null, ['08:00', '17:00'])).toBe(
      'off',
    );
  });

  it('休み 8:30-17:30 (勤務 8:30-17:30) は終日', () => {
    expect(classifyKaipokeLeave('休み', '8:30', '17:30', '8:30', '17:30')).toBe('off');
  });

  it('AM を PM より先に見る', () => {
    expect(classifyKaipokeLeave('AM休み・PM出勤', '9:00', '13:00', '09:00', '18:00')).toBe(
      'am_off',
    );
  });
});

describe('isLeaveEvent', () => {
  it.each([
    ['休み', true],
    ['有給', true],
    ['夏季休暇', true],
    ['休み（通院）', true],
    ['休日出勤 休み', true],
    ['休憩', false],
    ['休日当番', false],
    ['朝会', false],
  ] as const)('%s → %s', (title, expected) => {
    expect(isLeaveEvent(title)).toBe(expected);
  });
  it('null は休みではない', () => {
    expect(isLeaveEvent(null)).toBe(false);
  });
});

describe('leaveWindow', () => {
  const cases: [string | null, [string, string], [string, string] | null][] = [
    ['off', ['9:00', '18:00'], null],
    ['paid_off', ['9:00', '18:00'], null],
    ['am_off', ['9:00', '18:00'], ['13:00', '18:00']],
    ['pm_off', ['9:00', '18:00'], ['09:00', '12:30']],
    ['am_off', ['9:00', '13:00'], null],
    ['pm_off', ['12:30', '18:00'], null],
    ['am_off', ['9:00', '13:01'], ['13:00', '13:01']],
    ['pm_off', ['12:29', '18:00'], ['12:29', '12:30']],
    ['am_off', ['14:00', '18:00'], ['14:00', '18:00']],
    ['pm_off', ['9:00', '12:00'], ['09:00', '12:00']],
    ['custom_time', ['9:00', '18:00'], ['09:00', '18:00']],
    [null, ['09:00:00', '18:00:00'], ['09:00', '18:00']],
  ];
  it.each(cases)('%s %j → %j', (type, [s, e], expected) => {
    expect(leaveWindow(type, s, e)).toEqual(expected);
  });
});
