/** 実効状態の派生ヘルパのユニットテスト。 */
import { describe, it, expect } from 'vitest';

import type { VisitWithCoords } from '../constants';
import {
  assignVisitLanes,
  displayStatus,
  formatDistance,
  groupStopsByCoord,
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

  it('しきい値引数 (モニター応答の max_inprogress_min) で境界が動く', () => {
    const v = { phase: 'inprogress' as const, departure: null, stay_minutes: 270 };
    // 既定 240 では 270 は退出忘れ。
    expect(isLongInprogress(v)).toBe(true);
    // 設定 300 (緩め) では 270 は該当しない。
    expect(isLongInprogress(v, 300)).toBe(false);
    // 設定 200 (厳しめ) では該当。
    expect(isLongInprogress(v, 200)).toBe(true);
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

describe('assignVisitLanes', () => {
  it('重なりなし (連続) → 全部 lane=0, laneCount=1', () => {
    const v1 = makeVisit({ start_time: '09:00', end_time: '10:00' });
    const v2 = makeVisit({ start_time: '10:00', end_time: '11:00' }); // v1 終了 = v2 開始: 重ならない
    const map = assignVisitLanes([v1, v2]);
    expect(map.get(v1.visit_id)).toEqual({ lane: 0, laneCount: 1 });
    expect(map.get(v2.visit_id)).toEqual({ lane: 0, laneCount: 1 });
  });

  it('同時刻 2 件 → lane 0/1, laneCount=2', () => {
    const v1 = makeVisit({ start_time: '09:00', end_time: '10:00', patient_name: 'A 様' });
    const v2 = makeVisit({ start_time: '09:00', end_time: '10:00', patient_name: 'B 様' });
    const map = assignVisitLanes([v1, v2]);
    const l1 = map.get(v1.visit_id)!;
    const l2 = map.get(v2.visit_id)!;
    expect(l1.laneCount).toBe(2);
    expect(l2.laneCount).toBe(2);
    expect(new Set([l1.lane, l2.lane])).toEqual(new Set([0, 1]));
  });

  it('3 件同時重なり → 3 レーン', () => {
    const vs = [
      makeVisit({ start_time: '09:00', end_time: '10:00', patient_name: 'A 様' }),
      makeVisit({ start_time: '09:00', end_time: '10:00', patient_name: 'B 様' }),
      makeVisit({ start_time: '09:00', end_time: '10:00', patient_name: 'C 様' }),
    ];
    const map = assignVisitLanes(vs);
    const lanes = vs.map((v) => map.get(v.visit_id)!.lane);
    expect(new Set(lanes)).toEqual(new Set([0, 1, 2]));
    expect(map.get(vs[0]!.visit_id)!.laneCount).toBe(3);
  });

  it('空配列 → 空 Map を返す', () => {
    expect(assignVisitLanes([])).toEqual(new Map());
  });
});

describe('groupStopsByCoord', () => {
  const makeStop = (lat: number, lng: number, overrides = {}) =>
    ({ ...makeVisit(overrides), patient_lat: lat, patient_lng: lng }) as VisitWithCoords;

  it('異なる座標の 2 件 → 2 グループ', () => {
    const stops = [makeStop(35.61, 140.11), makeStop(35.62, 140.12)];
    expect(groupStopsByCoord(stops)).toHaveLength(2);
  });

  it('同座標の 2 件 → 1 グループ・番号 [1,2]', () => {
    const stops = [makeStop(35.61, 140.11), makeStop(35.61, 140.11)];
    const groups = groupStopsByCoord(stops);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.numbers).toEqual([1, 2]);
    expect(groups[0]!.stops).toHaveLength(2);
  });

  it('深刻度: match + mismatch → worstStatus=mismatch', () => {
    const stops = [
      makeStop(35.61, 140.11, { phase: 'done', alert_level: 'none' }),
      makeStop(35.61, 140.11, { phase: 'done', alert_level: 'mismatch' }),
    ];
    expect(groupStopsByCoord(stops)[0]!.worstStatus).toBe('mismatch');
  });

  it('深刻度: missing が最重大 (missing < mismatch < review)', () => {
    const stops = [
      makeStop(35.61, 140.11, { phase: 'done', alert_level: 'review' }),
      makeStop(35.61, 140.11, { phase: 'missing', alert_level: 'missing' }),
      makeStop(35.61, 140.11, { phase: 'done', alert_level: 'mismatch' }),
    ];
    expect(groupStopsByCoord(stops)[0]!.worstStatus).toBe('missing');
  });

  it('空配列 → 空配列', () => {
    expect(groupStopsByCoord([])).toEqual([]);
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
