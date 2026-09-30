/**
 * 「実績の時刻を合わせる」(設計 2026-09-30) の分単位ヘルパー。
 * 既存の `jstHm` / `actualTimeParts` のテストは `actualTime.test.ts`。
 */
import { describe, expect, it } from 'vitest';

import { hmToMinutes, jstMinutes, minutesToHm } from '../actualTime';

describe('hmToMinutes', () => {
  it('"HH:MM" / "HH:MM:SS" を 0 時からの分にする', () => {
    expect(hmToMinutes('00:00')).toBe(0);
    expect(hmToMinutes('13:06')).toBe(786);
    expect(hmToMinutes('13:06:45')).toBe(786);
    expect(hmToMinutes('23:59')).toBe(1439);
  });

  it('読めない値は null', () => {
    expect(hmToMinutes(null)).toBeNull();
    expect(hmToMinutes(undefined)).toBeNull();
    expect(hmToMinutes('')).toBeNull();
    expect(hmToMinutes('abc')).toBeNull();
    expect(hmToMinutes('24:00')).toBeNull();
    expect(hmToMinutes('12:60')).toBeNull();
  });
});

describe('minutesToHm', () => {
  it('0 時からの分を "HH:MM" にする (ゼロ埋め)', () => {
    expect(minutesToHm(0)).toBe('00:00');
    expect(minutesToHm(9 * 60 + 5)).toBe('09:05');
    expect(minutesToHm(786)).toBe('13:06');
    expect(minutesToHm(1439)).toBe('23:59');
  });

  it('hmToMinutes と往復する', () => {
    for (const m of [0, 1, 59, 60, 776, 786, 1439]) {
      expect(hmToMinutes(minutesToHm(m))).toBe(m);
    }
  });
});

describe('jstMinutes', () => {
  it('ISO 8601 を JST の 0 時からの分にする (秒は切り捨て)', () => {
    expect(jstMinutes('2026-09-30T04:06:00Z')).toBe(786);
    expect(jstMinutes('2026-09-30T04:06:59Z')).toBe(786);
    expect(jstMinutes('2026-09-30T13:06:20+09:00')).toBe(786);
  });

  it('UTC では前日でも JST の時刻で数える', () => {
    // UTC 9/29 23:30 = JST 9/30 08:30。
    expect(jstMinutes('2026-09-29T23:30:00Z')).toBe(8 * 60 + 30);
  });

  it('null / 不正な値は null', () => {
    expect(jstMinutes(null)).toBeNull();
    expect(jstMinutes(undefined)).toBeNull();
    expect(jstMinutes('not-a-date')).toBeNull();
  });
});
