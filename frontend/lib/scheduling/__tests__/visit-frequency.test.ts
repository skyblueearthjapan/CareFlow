import { describe, it, expect } from 'vitest';

import {
  formatMonthDayDow,
  formatVisitWeeks,
  frequencyStatusOf,
  frequencyWarningText,
  offWeekLabel,
  parseVisitWeeks,
  snapToMonday,
} from '@/lib/scheduling/visit-frequency';
import { getDesiredWeeklyVisitCount } from '@/lib/scheduling/preferred-visits';

describe('visit-frequency の小さな道具', () => {
  it('snapToMonday: どの日を選んでもその週の月曜にそろえる', () => {
    expect(snapToMonday('2026-11-02')).toBe('2026-11-02'); // 月
    expect(snapToMonday('2026-11-05')).toBe('2026-11-02'); // 木
    expect(snapToMonday('2026-11-08')).toBe('2026-11-02'); // 日
    expect(snapToMonday('2027-01-01')).toBe('2026-12-28'); // 年またぎ
    expect(snapToMonday('')).toBeNull();
    expect(snapToMonday('2026-02-30')).toBeNull();
  });

  it('formatMonthDayDow: 11/5（木）', () => {
    expect(formatMonthDayDow('2026-11-05')).toBe('11/5（木）');
    expect(formatMonthDayDow('2026-11-02')).toBe('11/2（月）');
  });

  it('parseVisitWeeks / formatVisitWeeks: "1,3" と数の並びを行き来する', () => {
    expect(parseVisitWeeks('3,1')).toEqual([1, 3]);
    expect(parseVisitWeeks('１、３')).toEqual([1, 3]);
    expect(parseVisitWeeks('1,6,x')).toEqual([1]);
    expect(parseVisitWeeks(null)).toEqual([]);
    expect(formatVisitWeeks([3, 1, 3])).toBe('1,3');
    expect(formatVisitWeeks([])).toBeNull();
  });

  it('frequencyStatusOf: 設定が足りないと分かる (backend と同じ決まり)', () => {
    expect(
      frequencyStatusOf({ visit_frequency: 'biweekly', biweekly_anchor: null, visit_weeks: null }),
    ).toBe('missing_anchor');
    expect(
      frequencyStatusOf({ visit_frequency: 'monthly', biweekly_anchor: null, visit_weeks: '' }),
    ).toBe('missing_weeks');
    expect(
      frequencyStatusOf({
        visit_frequency: 'biweekly',
        biweekly_anchor: '2026-11-02',
        visit_weeks: null,
      }),
    ).toBe('ok');
    expect(
      frequencyStatusOf({ visit_frequency: null, biweekly_anchor: null, visit_weeks: null }),
    ).toBe('ok');
    expect(frequencyWarningText('missing_anchor')).toContain('起点の週が未設定');
    expect(frequencyWarningText('ok')).toBeNull();
  });

  it('offWeekLabel: 隔週・第何週の言い方', () => {
    expect(offWeekLabel({ visit_frequency: 'biweekly', visit_weeks: null })).toBe(
      '隔週・お休みの週',
    );
    expect(offWeekLabel({ visit_frequency: 'monthly', visit_weeks: '1,3' })).toBe(
      '第1・3週のみ・お休みの週',
    );
  });
});

describe('getDesiredWeeklyVisitCount — お休みの週', () => {
  it('お休みの週は希望 0 (足りないと数えない)', () => {
    const p = { weekly_pattern: { frequency_per_week: 2, visit_frequency: 'biweekly' } };
    expect(getDesiredWeeklyVisitCount(p)).toBe(2);
    expect(getDesiredWeeklyVisitCount(p, { offWeek: true })).toBe(0);
    expect(getDesiredWeeklyVisitCount(p, { offWeek: false })).toBe(2);
  });
});
