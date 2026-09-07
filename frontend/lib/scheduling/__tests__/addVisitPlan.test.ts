/**
 * addVisitPlan — 「＋訪問（任意日付の訪問追加）」の純粋ロジック。
 *
 * 観点 (`docs/plans/add-visit-anywhere-design.md` §9):
 *   - 複数日付の ISO 週分割 (= `propose-slots` の呼び出し単位)
 *   - `time_type='固定'` の提案リクエスト組み立て
 *   - slots → 日付の振り分け (score 降順)
 *   - (b) の動かす元の既定 (PO 決定 9: 同曜日 → 最も近い日付)
 *   - (a) は単一日付のみ有効
 *   - 所要時間 5 分刻み 15〜120 + 基本時間
 *   - 除外理由の優先度と日本語ラベル
 */
import { describe, expect, it } from 'vitest';

import {
  assignSourceVisits,
  buildProposeRequest,
  canChoosePatternScope,
  durationOptions,
  excludedReasonLabel,
  formatDateLabel,
  groupDatesByIsoWeek,
  isMCourseCode,
  isMovableSourceVisit,
  isoWeekOfDate,
  mapSlotsToDates,
  pickDefaultSourceVisit,
  pickExcludedReason,
  weekdayCodeOf,
  weekdayIndexOfCode,
  type VisitLite,
} from '../addVisitPlan';
import type { ProposeSlotItem } from '@/lib/schemas/v2/propose_slots';

const PATIENT = {
  id: '11111111-1111-1111-1111-111111111111',
  lat: 35.6,
  lng: 140.1,
  sex_restriction: 'female',
  requires_multiple_staff: false,
};

const WD_CODES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

function slot(over: Partial<ProposeSlotItem> & { weekday: number }): ProposeSlotItem {
  return {
    office_id: '22222222-2222-2222-2222-222222222222',
    weekday_code: WD_CODES[over.weekday] ?? 'Mon',
    course_code: 'A',
    course_label: '稲毛A',
    start_time: '12:00',
    end_time: '12:35',
    score: 1,
    reasons: [],
    warnings: [],
    is_pair: false,
    mini_schedule: [],
    is_efficiency_alternative: false,
    overcapacity: false,
    event_conflicts: [],
    ...over,
  };
}

function visit(over: Partial<VisitLite> & { id: string; visit_date: string }): VisitLite {
  return {
    start_time: '09:30',
    end_time: '10:05',
    primary_staff_id: null,
    week_pinned: false,
    status: 'planned',
    source: 'auto',
    ...over,
  };
}

describe('isoWeekOfDate', () => {
  it('ISO 週と曜日 (0=月..6=日) を返す', () => {
    expect(isoWeekOfDate('2026-09-14')).toEqual({ isoYear: 2026, isoWeek: 38, weekday: 0 });
    expect(isoWeekOfDate('2026-09-19')).toEqual({ isoYear: 2026, isoWeek: 38, weekday: 5 });
    expect(isoWeekOfDate('2026-09-22')).toEqual({ isoYear: 2026, isoWeek: 39, weekday: 1 });
  });

  it('年跨ぎ (2026-12-28 は 2026 W53)', () => {
    expect(isoWeekOfDate('2026-12-28')).toEqual({ isoYear: 2026, isoWeek: 53, weekday: 0 });
  });
});

describe('groupDatesByIsoWeek', () => {
  it('週ごとにまとめ・重複除去・昇順', () => {
    const groups = groupDatesByIsoWeek([
      '2026-09-22',
      '2026-09-17',
      '2026-09-14',
      '2026-09-14', // 重複
    ]);
    expect(groups).toEqual([
      { isoYear: 2026, isoWeek: 38, dates: ['2026-09-14', '2026-09-17'], weekdays: [0, 3] },
      { isoYear: 2026, isoWeek: 39, dates: ['2026-09-22'], weekdays: [1] },
    ]);
  });

  it('空配列は空', () => {
    expect(groupDatesByIsoWeek([])).toEqual([]);
  });

  it('伊藤様のケース (6 日付) は 4 週に分かれる', () => {
    const groups = groupDatesByIsoWeek([
      '2026-09-14',
      '2026-09-17',
      '2026-09-19',
      '2026-09-22',
      '2026-09-25',
      '2026-09-28',
    ]);
    expect(groups.map((g) => `${g.isoYear}/${g.isoWeek}:${g.dates.length}`)).toEqual([
      '2026/38:3',
      '2026/39:2',
      '2026/40:1',
    ]);
  });
});

describe('weekdayCodeOf / weekdayIndexOfCode', () => {
  it('0=Mon..6=Sun で往復する', () => {
    expect(weekdayCodeOf(0)).toBe('Mon');
    expect(weekdayCodeOf(5)).toBe('Sat');
    expect(weekdayIndexOfCode('Sat')).toBe(5);
    expect(weekdayIndexOfCode('XXX')).toBe(-1);
  });
});

describe('formatDateLabel', () => {
  it('M/d(曜) 表記', () => {
    expect(formatDateLabel('2026-09-14')).toBe('9/14(月)');
    expect(formatDateLabel('2026-09-19')).toBe('9/19(土)');
  });
});

describe('buildProposeRequest', () => {
  it("time_type='固定' + 曜日コード + 拠点 + limit50 / include_overcapacity", () => {
    const req = buildProposeRequest({
      patient: PATIENT,
      isoYear: 2026,
      isoWeek: 38,
      weekdays: [3, 0, 0],
      startHM: '12:00',
      minutes: 35,
      officeIds: ['22222222-2222-2222-2222-222222222222'],
    });
    expect(req).toMatchObject({
      time_type: '固定',
      preferred_start: '12:00',
      preferred_weekdays: ['Mon', 'Thu'], // 重複除去・昇順
      service_minutes: 35,
      iso_year: 2026,
      iso_week: 38,
      office_ids: ['22222222-2222-2222-2222-222222222222'],
      existing_patient_id: PATIENT.id,
      limit: 50,
      include_overcapacity: true,
      requires_multiple_staff: false,
      sex_restriction: 'female',
    });
  });
});

describe('mapSlotsToDates', () => {
  it('曜日で日付へ振り分け、BE のランキング順をそのまま保つ (score で並べ替えない)', () => {
    const group = { dates: ['2026-09-14', '2026-09-17', '2026-09-19'] };
    const map = mapSlotsToDates(
      [
        slot({ weekday: 0, course_label: '稲毛A', score: 0.5 }),
        slot({ weekday: 0, course_label: '稲毛C', score: 0.9 }),
        slot({ weekday: 3, course_label: '稲毛B', score: 0.2 }),
      ],
      group,
    );
    expect(map.get('2026-09-14')?.map((s) => s.course_label)).toEqual(['稲毛A', '稲毛C']);
    expect(map.get('2026-09-17')?.map((s) => s.course_label)).toEqual(['稲毛B']);
    // 候補 0 件の日もキーは存在する (空配列)
    expect(map.get('2026-09-19')).toEqual([]);
  });

  it('定員超だけは後ろへ回す (安定分割・通常候補どうしの順は不変)', () => {
    const map = mapSlotsToDates(
      [
        slot({ weekday: 0, course_label: '超過A', overcapacity: true, score: 9 }),
        slot({ weekday: 0, course_label: '通常A' }),
        slot({ weekday: 0, course_label: '通常B' }),
        slot({ weekday: 0, course_label: '超過B', overcapacity: true }),
      ],
      { dates: ['2026-09-14'] },
    );
    expect(map.get('2026-09-14')?.map((s) => s.course_label)).toEqual([
      '通常A',
      '通常B',
      '超過A',
      '超過B',
    ]);
  });

  it('weekday_code を正とする (weekday が食い違っても code で振り分ける)', () => {
    const map = mapSlotsToDates(
      [slot({ weekday: 9, weekday_code: 'Thu', course_label: '稲毛B' })],
      { dates: ['2026-09-17'] },
    );
    expect(map.get('2026-09-17')?.map((s) => s.course_label)).toEqual(['稲毛B']);
  });
});

describe('pickExcludedReason', () => {
  it('BE と同じ優先度で代表理由を選ぶ', () => {
    const code = pickExcludedReason(
      [
        { reason: 'no_gap', count: 3, weekday: 5 },
        { reason: 'capacity_full', count: 1, weekday: 5 },
        { reason: 'capacity_full', count: 9, weekday: 0 },
      ],
      5,
    );
    expect(code).toBe('capacity_full');
  });

  it('その曜日の記載が無ければ null', () => {
    expect(pickExcludedReason([{ reason: 'no_gap', count: 1, weekday: 0 }], 5)).toBeNull();
  });
});

describe('pickDefaultSourceVisit (PO 決定 9)', () => {
  it('同じ曜日を最優先する', () => {
    const picked = pickDefaultSourceVisit(
      [
        visit({ id: 'v-tue', visit_date: '2026-09-15' }), // 火・1 日違い
        visit({ id: 'v-mon', visit_date: '2026-09-21' }), // 月・7 日違い
      ],
      '2026-09-14', // 月
    );
    expect(picked?.id).toBe('v-mon');
  });

  it('同じ曜日が無ければ最も近い日付', () => {
    const picked = pickDefaultSourceVisit(
      [
        visit({ id: 'v-far', visit_date: '2026-09-18' }),
        visit({ id: 'v-near', visit_date: '2026-09-16' }),
      ],
      '2026-09-14',
    );
    expect(picked?.id).toBe('v-near');
  });

  it('距離が同じなら早い日付', () => {
    const picked = pickDefaultSourceVisit(
      [
        visit({ id: 'v-after', visit_date: '2026-09-18' }),
        visit({ id: 'v-before', visit_date: '2026-09-16' }),
      ],
      '2026-09-17',
    );
    expect(picked?.id).toBe('v-before');
  });

  it('候補が無ければ null', () => {
    expect(pickDefaultSourceVisit([], '2026-09-14')).toBeNull();
  });
});

describe('isMCourseCode', () => {
  it('M / M2..M9 を M として扱う', () => {
    expect(isMCourseCode('M')).toBe(true);
    expect(isMCourseCode('m')).toBe(true);
    expect(isMCourseCode('M2')).toBe(true);
    expect(isMCourseCode('A')).toBe(false);
    expect(isMCourseCode('MA')).toBe(false);
    expect(isMCourseCode(null)).toBe(false);
  });
});

describe('isMovableSourceVisit', () => {
  it('planned・青ピンでない・当日より後 のときだけ動かせる', () => {
    const base = visit({ id: 'v1', visit_date: '2026-09-14' });
    expect(isMovableSourceVisit(base, '2026-09-07')).toBe(true);
    expect(isMovableSourceVisit({ ...base, week_pinned: true }, '2026-09-07')).toBe(false);
    expect(isMovableSourceVisit({ ...base, status: 'cancelled' }, '2026-09-07')).toBe(false);
    // 当日以前は動かせない (当日ちょうども不可)
    expect(isMovableSourceVisit(base, '2026-09-14')).toBe(false);
    expect(isMovableSourceVisit(base, '2026-09-20')).toBe(false);
  });
});

describe('assignSourceVisits', () => {
  const pool = [
    visit({ id: 'v-mon', visit_date: '2026-09-14' }),
    visit({ id: 'v-wed', visit_date: '2026-09-16' }),
  ];

  it('1 件の訪問を 2 つの日付に割り当てない (足りない日は null)', () => {
    const only = [visit({ id: 'v-mon', visit_date: '2026-09-14' })];
    const got = assignSourceVisits({
      dates: ['2026-09-14', '2026-09-16', '2026-09-18'],
      candidatesByDate: () => only,
    });
    expect(got.get('2026-09-14')?.id).toBe('v-mon');
    expect(got.get('2026-09-16')).toBeNull();
    expect(got.get('2026-09-18')).toBeNull();
  });

  it('日付ごとに既定規則で埋める (同曜日 → 最も近い日付)', () => {
    const got = assignSourceVisits({
      dates: ['2026-09-16', '2026-09-14'],
      candidatesByDate: () => pool,
    });
    expect(got.get('2026-09-14')?.id).toBe('v-mon');
    expect(got.get('2026-09-16')?.id).toBe('v-wed');
  });

  it('手動指定を先に確保し、他の日付は重複しないものを取る', () => {
    const got = assignSourceVisits({
      dates: ['2026-09-14', '2026-09-16'],
      candidatesByDate: () => pool,
      overrides: { '2026-09-16': 'v-mon' },
    });
    expect(got.get('2026-09-16')?.id).toBe('v-mon');
    expect(got.get('2026-09-14')?.id).toBe('v-wed');
  });

  it('候補に無い手動指定は無視して既定へ戻す', () => {
    const got = assignSourceVisits({
      dates: ['2026-09-14'],
      candidatesByDate: () => pool,
      overrides: { '2026-09-14': 'v-ghost' },
    });
    expect(got.get('2026-09-14')?.id).toBe('v-mon');
  });
});

describe('canChoosePatternScope', () => {
  it('日付が 1 つのときだけ true', () => {
    expect(canChoosePatternScope(['2026-09-14'])).toBe(true);
    expect(canChoosePatternScope(['2026-09-14', '2026-09-14'])).toBe(true);
    expect(canChoosePatternScope(['2026-09-14', '2026-09-17'])).toBe(false);
    expect(canChoosePatternScope([])).toBe(false);
  });
});

describe('durationOptions (PO 決定 12)', () => {
  it('15〜120 の 5 分刻み', () => {
    const opts = durationOptions();
    expect(opts[0]).toBe(15);
    expect(opts[opts.length - 1]).toBe(120);
    expect(opts).toContain(35);
    expect(opts).toHaveLength(22);
  });

  it('刻み外の基本時間は選択肢に加える (昇順)', () => {
    const opts = durationOptions(32);
    expect(opts).toContain(32);
    expect(opts.indexOf(32)).toBe(opts.indexOf(30) + 1);
    expect(opts).toHaveLength(23);
  });

  it('刻み内の基本時間は重複させない / 不正値は無視', () => {
    expect(durationOptions(35)).toHaveLength(22);
    expect(durationOptions(null)).toHaveLength(22);
    expect(durationOptions(0)).toHaveLength(22);
  });
});

describe('excludedReasonLabel', () => {
  it('BE の理由コードを日本語化する', () => {
    expect(excludedReasonLabel('capacity_full')).toBe('定員いっぱい');
    expect(excludedReasonLabel('pair_blocked')).toBe('同住所ペアの枠が取れない');
    expect(excludedReasonLabel('travel_shortage')).toBe('移動時間が足りない');
    expect(excludedReasonLabel('lunch_window')).toBe('昼休みと重なる');
    expect(excludedReasonLabel('no_pair_slot')).toBe('2名体制の相方枠が無い');
    expect(excludedReasonLabel('no_gap')).toBe('空き時間が無い');
  });

  it('未知コードはそのまま返す', () => {
    expect(excludedReasonLabel('brand_new_reason')).toBe('brand_new_reason');
  });
});
