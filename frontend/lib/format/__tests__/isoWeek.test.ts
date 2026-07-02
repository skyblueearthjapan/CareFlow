/**
 * isoWeek ユーティリティ 単体テスト.
 *
 * 主眼: previousIsoWeek の年跨ぎ / 53 週年の正しさ (Schedule Advisor Phase 1
 * の「当週 vs 前週」比較で前週を誤ると delta が破綻するため施錠する).
 */
import { describe, it, expect } from 'vitest';

import { isoWeekFromDate, isoWeekFromLocalDate, mondayOfIsoWeek, previousIsoWeek } from '../isoWeek';

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

describe('isoWeekFromLocalDate', () => {
  // ローカル日付ベース (d.getFullYear/getMonth/getDate) で計算する版。
  // 旧 toIsoYearWeek ローカル実装と完全同一のロジックを施錠するテーブルテスト。
  const cases: Array<[string, Date, { isoYear: number; isoWeek: number }]> = [
    // 通常週
    ['2026-07-01 (Wed, week 27)', new Date(2026, 6, 1), { isoYear: 2026, isoWeek: 27 }],
    ['2026-01-05 (Mon, week 2)',  new Date(2026, 0, 5), { isoYear: 2026, isoWeek: 2 }],
    // 年跨ぎ: 2026-01-01 (Thu) は 2026-W01
    ['2026-01-01 (Thu, week 1)', new Date(2026, 0, 1), { isoYear: 2026, isoWeek: 1 }],
    // 年跨ぎ: 2025-12-29 (Mon) は 2026-W01
    ['2025-12-29 (Mon, belongs to 2026-W01)', new Date(2025, 11, 29), { isoYear: 2026, isoWeek: 1 }],
    // 2020 は 53 週年
    ['2020-12-28 (Mon, week 53 of 2020)', new Date(2020, 11, 28), { isoYear: 2020, isoWeek: 53 }],
    ['2021-01-03 (Sun, still week 53 of 2020)', new Date(2021, 0, 3), { isoYear: 2020, isoWeek: 53 }],
    ['2021-01-04 (Mon, week 1 of 2021)', new Date(2021, 0, 4), { isoYear: 2021, isoWeek: 1 }],
    // 2015 は 53 週年
    ['2015-12-28 (Mon, week 53 of 2015)', new Date(2015, 11, 28), { isoYear: 2015, isoWeek: 53 }],
  ];

  it.each(cases)('%s', (_label, date, expected) => {
    expect(isoWeekFromLocalDate(date)).toEqual(expected);
  });

  it('isoWeekFromDate(UTC midnight) と同一結果になる (UTC+0 環境のみ保証)', () => {
    // UTC ±0 のCI環境では両関数が一致する。タイムゾーン依存の差異を可視化するため記録。
    const d = new Date(Date.UTC(2026, 6, 1)); // 2026-07-01 UTC
    // 環境が UTC の場合は一致する
    if (d.getFullYear() === d.getUTCFullYear()) {
      expect(isoWeekFromLocalDate(d)).toEqual(isoWeekFromDate(d));
    }
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
