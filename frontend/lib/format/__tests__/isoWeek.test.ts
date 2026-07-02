/**
 * isoWeek ユーティリティ 単体テスト.
 *
 * 主眼: previousIsoWeek の年跨ぎ / 53 週年の正しさ (Schedule Advisor Phase 1
 * の「当週 vs 前週」比較で前週を誤ると delta が破綻するため施錠する).
 */
import { describe, it, expect } from 'vitest';

import { isoWeekFromDate, mondayOfIsoWeek, previousIsoWeek } from '../isoWeek';

describe('previousIsoWeek', () => {
  it('週中の前週は単純に -1 週', () => {
    expect(previousIsoWeek(2026, 27)).toEqual({ isoYear: 2026, isoWeek: 26 });
  });

  it('週1 の前週は前年最終週 (52 週年: 2025)', () => {
    expect(previousIsoWeek(2026, 1)).toEqual({ isoYear: 2025, isoWeek: 52 });
  });

  it('週1 の前週が 53 週年に落ちる (2020 は 53 週)', () => {
    expect(previousIsoWeek(2021, 1)).toEqual({ isoYear: 2020, isoWeek: 53 });
  });

  it('別の 53 週年ケース (2015 は 53 週)', () => {
    expect(previousIsoWeek(2016, 1)).toEqual({ isoYear: 2015, isoWeek: 53 });
  });
});

describe('mondayOfIsoWeek / isoWeekFromDate ラウンドトリップ', () => {
  it('(isoYear, isoWeek) → 月曜 Date → (isoYear, isoWeek) が一致する', () => {
    for (const [y, w] of [
      [2026, 27],
      [2026, 1],
      [2025, 52],
      [2020, 53],
    ] as const) {
      const monday = mondayOfIsoWeek(y, w);
      expect(monday.getUTCDay()).toBe(1); // 月曜
      expect(isoWeekFromDate(monday)).toEqual({ isoYear: y, isoWeek: w });
    }
  });
});
