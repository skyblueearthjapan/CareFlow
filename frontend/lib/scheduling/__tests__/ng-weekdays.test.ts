import { describe, it, expect } from 'vitest';

import {
  ngWeekdayConflictText,
  ngWeekdayConflicts,
  ngWeekdaysLabel,
  sortWeekdays,
  toggleNgWeekday,
} from '@/lib/scheduling/ng-weekdays';

describe('ng-weekdays', () => {
  it('toggle は月→日の順にそろえ、空になったら null', () => {
    expect(toggleNgWeekday(null, 'Fri')).toEqual(['Fri']);
    expect(toggleNgWeekday(['Fri'], 'Mon')).toEqual(['Mon', 'Fri']);
    expect(toggleNgWeekday(['Mon'], 'Mon')).toBeNull();
    expect(sortWeekdays(['Sun', 'Tue'])).toEqual(['Tue', 'Sun']);
  });

  it('希望曜日との重なりと注意書き', () => {
    expect(ngWeekdayConflicts(['Mon', 'Wed'], ['Wed', 'Sun'])).toEqual(['Wed']);
    expect(ngWeekdayConflicts(['Mon'], null)).toEqual([]);
    expect(ngWeekdayConflictText([])).toBeNull();
    expect(ngWeekdayConflictText(['Mon', 'Wed'])).toContain('月・水');
  });

  it('カルテの表示', () => {
    expect(ngWeekdaysLabel(null)).toBe('なし');
    expect(ngWeekdaysLabel(['Sun', 'Mon'])).toBe('月・日');
  });
});
