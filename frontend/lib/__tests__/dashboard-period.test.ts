import { describe, expect, it } from 'vitest';

import { diff1, hhmm, presetRange, todayJst } from '@/lib/dashboard-period';

describe('presetRange (今日まで・週は月曜はじまり)', () => {
  const today = '2026-10-01'; // 木曜

  it('今週 = 月曜〜今日 / 先週 = 前の月〜日', () => {
    expect(presetRange('this_week', today)).toEqual({ from: '2026-09-28', to: '2026-10-01' });
    expect(presetRange('last_week', today)).toEqual({ from: '2026-09-21', to: '2026-09-27' });
  });

  it('今月 = 1 日〜今日 / 先月 = 前の月の 1 日〜末日', () => {
    expect(presetRange('this_month', today)).toEqual({ from: '2026-10-01', to: '2026-10-01' });
    expect(presetRange('last_month', today)).toEqual({ from: '2026-09-01', to: '2026-09-30' });
    expect(presetRange('last_month', '2026-01-15')).toEqual({
      from: '2025-12-01',
      to: '2025-12-31',
    });
  });

  it('日曜は前の月曜からの週', () => {
    expect(presetRange('this_week', '2026-10-04')).toEqual({
      from: '2026-09-28',
      to: '2026-10-04',
    });
  });
});

describe('表示の小道具', () => {
  it('hhmm / diff1', () => {
    expect(hhmm(75)).toBe('1:15');
    expect(hhmm(null)).toBe('—');
    expect(diff1(5.5, 5)).toBe('+0.5');
    expect(diff1(4.5, 5)).toBe('-0.5');
    expect(diff1(5, 5)).toBe('±0.0');
    expect(diff1(null, 5)).toBeNull();
  });

  it('todayJst は JST の日付', () => {
    expect(todayJst(new Date('2026-09-30T15:30:00Z'))).toBe('2026-10-01');
  });
});
