/**
 * Phase G-21 T4 reviewer C3 / M2 — weeklyPatternToDayRows merge 挙動テスト.
 *
 * 旧実装は `weeklyPatternToDayRows(pattern)` で常に empty rows から再生成し、
 * 既存の `is_pinned` / `course_template_id` / `course_template_id_2` /
 * `sub_office_id` を破壊していた. 新実装は `weeklyPatternToDayRows(pattern, current)`
 * で current を base に merge し、 preferred_weekdays に含まれる曜日のみ
 * start_time / duration_min を上書きする (= 既存設定を保持).
 */
import { describe, it, expect } from 'vitest';

import { weeklyPatternToDayRows } from '../PatientFixedVisitsPanel';

// minimal shape for test fixture (internal types not exported).
type DayRowLike = {
  enabled: boolean;
  start_time: string;
  duration_min: number;
  course_template_id: string | null;
  course_template_id_2: string | null;
  sub_office_id: string | null;
  is_pinned: boolean;
};

function makeRow(over: Partial<DayRowLike> = {}): DayRowLike {
  return {
    enabled: false,
    start_time: '09:00',
    duration_min: 30,
    course_template_id: null,
    course_template_id_2: null,
    sub_office_id: null,
    is_pinned: false,
    ...over,
  };
}

describe('weeklyPatternToDayRows merge behavior (reviewer C3 / M2)', () => {
  const pattern = {
    frequency_per_week: 2,
    visit_frequency: null,
    visit_weeks: null,
    preferred_weekdays: ['Mon', 'Wed'] as const,
    service_minutes: 60,
    time_type: '固定' as const,
    preferred_start: '10:00',
    preferred_end: null,
    ng_weekdays: null,
  };

  it('current 未指定: 旧挙動と同等 (empty base から preferred 曜日のみ ON)', () => {
    const rows = weeklyPatternToDayRows(pattern) as Record<number, DayRowLike>;
    expect(rows[0]?.enabled).toBe(true); // Mon
    expect(rows[2]?.enabled).toBe(true); // Wed
    expect(rows[1]?.enabled).toBe(false); // Tue
    expect(rows[0]?.start_time).toBe('10:00');
    expect(rows[0]?.duration_min).toBe(60);
    // 新規 row は is_pinned=false
    expect(rows[0]?.is_pinned).toBe(false);
  });

  it('C3: current の is_pinned=true は merge 後も保持される', () => {
    const current: Record<number, DayRowLike> = {
      0: makeRow({ enabled: true, is_pinned: true, course_template_id: 'tpl-A' }),
      1: makeRow(),
      2: makeRow({ enabled: true, is_pinned: true }),
      3: makeRow(),
      4: makeRow(),
      5: makeRow(),
      6: makeRow(),
    };
    const merged = weeklyPatternToDayRows(pattern, current) as Record<number, DayRowLike>;
    // Mon (preferred): start_time が上書きされるが is_pinned / course_template_id は維持
    expect(merged[0]?.enabled).toBe(true);
    expect(merged[0]?.is_pinned).toBe(true);
    expect(merged[0]?.course_template_id).toBe('tpl-A');
    expect(merged[0]?.start_time).toBe('10:00'); // pattern.preferred_start で上書き
    expect(merged[0]?.duration_min).toBe(60);
    // Wed (preferred): is_pinned 維持
    expect(merged[2]?.is_pinned).toBe(true);
  });

  it('M2: preferred_weekdays に無い曜日 (Tue) の既存設定は破壊されない', () => {
    const current: Record<number, DayRowLike> = {
      0: makeRow(),
      1: makeRow({
        enabled: true,
        start_time: '14:30',
        duration_min: 45,
        is_pinned: true,
        course_template_id: 'tpl-B',
        sub_office_id: 'office-sub',
      }),
      2: makeRow(),
      3: makeRow(),
      4: makeRow(),
      5: makeRow(),
      6: makeRow(),
    };
    const merged = weeklyPatternToDayRows(pattern, current) as Record<number, DayRowLike>;
    // Tue は pattern に含まれないので既存値そのまま
    expect(merged[1]?.enabled).toBe(true);
    expect(merged[1]?.start_time).toBe('14:30');
    expect(merged[1]?.duration_min).toBe(45);
    expect(merged[1]?.is_pinned).toBe(true);
    expect(merged[1]?.course_template_id).toBe('tpl-B');
    expect(merged[1]?.sub_office_id).toBe('office-sub');
  });

  it('preferred_weekdays に含まれるが既存 course_template_id=null の場合は null を保持', () => {
    const current: Record<number, DayRowLike> = {
      0: makeRow(),
      1: makeRow(),
      2: makeRow(),
      3: makeRow(),
      4: makeRow(),
      5: makeRow(),
      6: makeRow(),
    };
    const merged = weeklyPatternToDayRows(pattern, current) as Record<number, DayRowLike>;
    expect(merged[0]?.course_template_id).toBeNull();
    expect(merged[0]?.course_template_id_2).toBeNull();
  });

  it('pattern が null の場合: current をそのまま返す (no-op)', () => {
    const current: Record<number, DayRowLike> = {
      0: makeRow({ enabled: true, is_pinned: true }),
      1: makeRow(),
      2: makeRow(),
      3: makeRow(),
      4: makeRow(),
      5: makeRow(),
      6: makeRow(),
    };
    const merged = weeklyPatternToDayRows(null, current) as Record<number, DayRowLike>;
    expect(merged[0]?.is_pinned).toBe(true);
    expect(merged[0]?.enabled).toBe(true);
  });
});
