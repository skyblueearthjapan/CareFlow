/** 実効状態の派生ヘルパのユニットテスト。 */
import { describe, it, expect } from 'vitest';

import { monitorVisitSchema } from '@/lib/schemas/monitor';

import type { VisitWithCoords } from '../constants';
import {
  actualArrivalIso,
  actualDepartureIso,
  adjustmentNotes,
  alertReasonChips,
  assignVisitLanes,
  displayStatus,
  formatDistance,
  groupStopsByCoord,
  groupVisits,
  isAlert,
  isLongInprogress,
  minutesToPct,
  officeTagTone,
  rowMatchesOffice,
  substituteTitle,
  visitOfficeId,
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

// 行 = 職員 (monitor-staff-rows-design-2026-09-30.md)。予定外の専用行 (isUnplannedRow) は廃止。
describe('行 = 職員: 拠点の絞り込みと札の色', () => {
  const INAGE = '00000000-0000-0000-0000-00000000aaaa';
  const TSUGA = '00000000-0000-0000-0000-00000000bbbb';

  it('visitOfficeId: コースの拠点 → 無ければ行の職員の所属', () => {
    const row = makeRow({ office_id: INAGE });
    expect(visitOfficeId(makeVisit({ course_office_id: TSUGA }), row)).toBe(TSUGA);
    expect(visitOfficeId(makeVisit({ course_office_id: null }), row)).toBe(INAGE);
    expect(visitOfficeId(makeVisit(), makeRow({ office_id: null }))).toBeNull();
  });

  it('rowMatchesOffice: その拠点の訪問を 1 件でも持つ人 / 訪問の無い人は所属', () => {
    // 稲毛所属で都賀へ応援 → 都賀でも稲毛でも出る。
    const helper = makeRow({
      office_id: INAGE,
      visits: [makeVisit({ course_office_id: INAGE }), makeVisit({ course_office_id: TSUGA })],
    });
    expect(rowMatchesOffice(helper, TSUGA)).toBe(true);
    expect(rowMatchesOffice(helper, INAGE)).toBe(true);
    // 都賀の訪問だけの稲毛所属 → 稲毛では出ない (所属ではなく訪問の拠点で判定)。
    const away = makeRow({ office_id: INAGE, visits: [makeVisit({ course_office_id: TSUGA })] });
    expect(rowMatchesOffice(away, INAGE)).toBe(false);
    // 訪問なし (イベント・休みだけ) は所属。
    expect(rowMatchesOffice(makeRow({ office_id: INAGE, visits: [] }), INAGE)).toBe(true);
    expect(rowMatchesOffice(makeRow({ office_id: INAGE, visits: [] }), TSUGA)).toBe(false);
  });

  it('officeTagTone: 同じ拠点は同じ色・拠点ごとに別の色・不明は中立色', () => {
    const ids = [INAGE, TSUGA];
    expect(officeTagTone(INAGE, ids)).toEqual(officeTagTone(INAGE, ids));
    expect(officeTagTone(INAGE, ids)).not.toEqual(officeTagTone(TSUGA, ids));
    expect(officeTagTone(null, ids).background).toBe('var(--bg-muted)');
    expect(officeTagTone('00000000-0000-0000-0000-00000000cccc', ids).background).toBe(
      'var(--bg-muted)',
    );
  });
});

describe('代行 / 予定外 (qr-open-checkin-design.md §6)', () => {
  it('alertReasonChips: 予定外 → 代行 の順で返す', () => {
    expect(alertReasonChips(makeVisit())).toEqual([]);
    expect(alertReasonChips(makeVisit({ is_substitute: true }))).toEqual(['代行']);
    expect(alertReasonChips(makeVisit({ is_unplanned: true, is_substitute: true }))).toEqual([
      '予定外',
      '代行',
    ]);
  });

  it('substituteTitle: 予定 / 代行 を並記する (代行者名は substitute_staff_name 由来)', () => {
    // 代行 B → 担当 A 打ち直しでも、代行者は substitute_staff_name (= B)。
    expect(
      substituteTitle(
        makeVisit({
          staff_name: '担当 A',
          actual_staff_name: '担当 A',
          substitute_staff_name: '代行 B',
        }),
      ),
    ).toBe('予定: 担当 A / 代行: 代行 B');
    // 代行者名が無い応答では名前を併記しない。
    expect(
      substituteTitle(makeVisit({ staff_name: '担当 A', substitute_staff_name: null })),
    ).not.toContain('担当 A');
  });
});

// ---------------------------------------------------------------------------
// 実績時刻 (actual-time-adjust-design-2026-09-30 §6-3 / §8-1)
// ---------------------------------------------------------------------------

describe('実績時刻 (actualArrivalIso / actualDepartureIso / adjustmentNotes)', () => {
  /** BE の応答 1 件ぶん (スキーマを通す前の生の形)。 */
  const raw = (over: Record<string, unknown> = {}) => ({
    visit_id: '00000000-0000-4000-8000-000000000001',
    patient_id: '00000000-0000-4000-8000-000000000002',
    start_time: '13:00',
    end_time: '13:35',
    phase: 'done',
    alert_level: 'none',
    arrival: { kind: 'arrival', scanned_at: '2026-09-18T04:06:00Z', match_status: 'match' },
    departure: { kind: 'departure', scanned_at: '2026-09-18T04:31:00Z', match_status: 'match' },
    ...over,
  });

  it('新項目があれば実績時刻 (arrival_at / departure_at) を使う', () => {
    const v = monitorVisitSchema.parse(
      raw({ arrival_at: '2026-09-18T03:56:00Z', departure_at: '2026-09-18T04:31:00Z' }),
    );
    expect(actualArrivalIso(v)).toBe('2026-09-18T03:56:00Z');
    expect(actualDepartureIso(v)).toBe('2026-09-18T04:31:00Z');
  });

  it('新項目の無い応答 (古いバックエンド) は打刻の scanned_at へ落とし、調整なし扱い', () => {
    const v = monitorVisitSchema.parse(raw());
    expect(actualArrivalIso(v)).toBe('2026-09-18T04:06:00Z');
    expect(actualDepartureIso(v)).toBe('2026-09-18T04:31:00Z');
    expect(adjustmentNotes(v)).toEqual([]);
  });

  it('打刻も実績時刻も無ければ null', () => {
    const v = monitorVisitSchema.parse(raw({ arrival: null, departure: null }));
    expect(actualArrivalIso(v)).toBeNull();
    expect(actualDepartureIso(v)).toBeNull();
  });

  it('新項目の形が崩れていてもモニターは落とさない (その項目だけ捨てる)', () => {
    const v = monitorVisitSchema.parse(
      raw({ arrival_at: 123, arrival_adjusted: 'yes', adjustments: [{ reason_label: 'x' }] }),
    );
    expect(v.arrival_at).toBeNull();
    expect(v.adjustments).toBeNull();
    expect(actualArrivalIso(v)).toBe('2026-09-18T04:06:00Z');
  });

  it('調整の内容: 読取時刻・理由 (自由記述つき)・誰がいつ', () => {
    const v = monitorVisitSchema.parse(
      raw({
        arrival_at: '2026-09-18T03:56:00Z',
        arrival_read_at: '2026-09-18T04:06:00Z',
        arrival_adjusted: true,
        adjustments: [
          {
            kind: 'arrival',
            reason_label: 'その他',
            reason_text: '駐車場が遠かった',
            by_name: '川名 幸子',
            created_at: '2026-09-18T04:10:00Z',
          },
        ],
      }),
    );
    expect(adjustmentNotes(v)).toEqual([
      {
        kind: 'arrival',
        label: '到着',
        at: '12:56',
        readAt: '13:06',
        manual: false,
        reason: 'その他・駐車場が遠かった',
        by: '川名 幸子 9/18 13:10',
        text: '到着 12:56（読取 13:06）・その他・駐車場が遠かった・川名 幸子 9/18 13:10',
      },
    ]);
  });

  it('調整の理由コード (reason_code) を読む。無い応答でも落とさない (L-11)', () => {
    const v = monitorVisitSchema.parse(
      raw({
        adjustments: [
          { kind: 'arrival', reason_code: 'intercom_wait', reason_label: 'インターホン待ち' },
          { kind: 'departure', reason_label: '読み取りなし' },
        ],
      }),
    );
    expect(v.adjustments?.[0]?.reason_code).toBe('intercom_wait');
    expect(v.adjustments?.[1]?.reason_code).toBeUndefined();
  });

  it('調整の内容 (adjustments) が無くても、フラグがあれば時刻だけで出す', () => {
    const v = monitorVisitSchema.parse(
      raw({
        departure: null,
        departure_at: '2026-09-18T04:31:00Z',
        departure_manual: true,
      }),
    );
    expect(adjustmentNotes(v).map((n) => n.text)).toEqual(['退出 13:31（手入力・読み取りなし）']);
  });
});
