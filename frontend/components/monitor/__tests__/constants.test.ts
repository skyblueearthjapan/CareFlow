/** 実効状態の派生ヘルパのユニットテスト。 */
import { describe, it, expect } from 'vitest';

import {
  displayStatus,
  formatDistance,
  groupVisits,
  isAlert,
  isLongInprogress,
  minutesToPct,
} from '../constants';
import { makeRow, makeVisit } from './fixtures';

describe('displayStatus', () => {
  it('missing/future/awaiting は phase をそのまま反映', () => {
    expect(displayStatus({ phase: 'missing', alert_level: 'missing' })).toBe('missing');
    expect(displayStatus({ phase: 'future', alert_level: 'none' })).toBe('future');
    expect(displayStatus({ phase: 'awaiting', alert_level: 'none' })).toBe('awaiting');
  });

  it('alert_level が mismatch/review を優先する', () => {
    expect(displayStatus({ phase: 'done', alert_level: 'mismatch' })).toBe('mismatch');
    expect(displayStatus({ phase: 'inprogress', alert_level: 'review' })).toBe('review');
  });

  it('inprogress + none は inprogress、done + none は match', () => {
    expect(displayStatus({ phase: 'inprogress', alert_level: 'none' })).toBe('inprogress');
    expect(displayStatus({ phase: 'done', alert_level: 'none' })).toBe('match');
  });
});

describe('isAlert', () => {
  it('missing/mismatch/review のみ true', () => {
    expect(isAlert({ alert_level: 'missing' })).toBe(true);
    expect(isAlert({ alert_level: 'mismatch' })).toBe(true);
    expect(isAlert({ alert_level: 'review' })).toBe(true);
    expect(isAlert({ alert_level: 'none' })).toBe(false);
  });
});

describe('formatDistance', () => {
  it('null は —、<1km は m、>=1km は km', () => {
    expect(formatDistance(null)).toBe('—');
    expect(formatDistance(123)).toBe('120m');
    expect(formatDistance(1200)).toBe('1.2km');
  });
});

describe('isLongInprogress', () => {
  it('inprogress・退出なし・滞在 > 240 分のみ true (境界)', () => {
    expect(isLongInprogress({ phase: 'inprogress', departure: null, stay_minutes: 241 })).toBe(
      true,
    );
    expect(isLongInprogress({ phase: 'inprogress', departure: null, stay_minutes: 240 })).toBe(
      false,
    );
    // 退出済みは対象外。
    expect(
      isLongInprogress({
        phase: 'inprogress',
        departure: {
          kind: 'departure',
          scanned_at: 'x',
          match_status: 'match',
          is_override: false,
        },
        stay_minutes: 300,
      }),
    ).toBe(false);
    // done は対象外。
    expect(isLongInprogress({ phase: 'done', departure: null, stay_minutes: 300 })).toBe(false);
  });
});

describe('minutesToPct', () => {
  it('範囲外は 0..100 にクランプ (8–19h 軸)', () => {
    expect(minutesToPct(7 * 60)).toBe(0); // 7:00 < 8:00 → 0
    expect(minutesToPct(20 * 60)).toBe(100); // 20:00 > 19:00 → 100
    expect(minutesToPct(8 * 60)).toBe(0);
    expect(minutesToPct(19 * 60)).toBe(100);
  });
});

describe('groupVisits (2 名体制の重複排除)', () => {
  it('同一 visit_group_id の 2 行を 1 件に集約し worst(alert) を代表にする', () => {
    const gid = '11111111-1111-1111-1111-111111111111';
    const reviewMember = makeVisit({
      visit_group_id: gid,
      patient_name: '田所 様',
      alert_level: 'review',
      phase: 'inprogress',
    });
    const missingMember = makeVisit({
      visit_group_id: gid,
      patient_name: '田所 様',
      alert_level: 'missing',
      phase: 'missing',
    });
    const rows = [
      makeRow({ staff_name: 'スタッフA', visits: [reviewMember] }),
      makeRow({ staff_name: 'スタッフB', visits: [missingMember] }),
    ];

    const groups = groupVisits(rows);
    expect(groups).toHaveLength(1);
    const g = groups[0];
    expect(g.isPair).toBe(true);
    expect(g.worstAlertLevel).toBe('missing'); // worst が代表
    expect(g.representative).toBe(missingMember);
    expect(g.staffNames).toEqual(['スタッフA', 'スタッフB']);
  });

  it('visit_group_id が null の訪問は visit.id 単位 (集約しない)', () => {
    const rows = [makeRow({ visits: [makeVisit(), makeVisit()] })];
    expect(groupVisits(rows)).toHaveLength(2);
  });
});
