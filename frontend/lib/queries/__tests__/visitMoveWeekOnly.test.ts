/**
 * visitMoveWeekOnly zod schema — Wave U-2「この週だけ移動」の契約テスト.
 */
import { describe, it, expect } from 'vitest';

import {
  visitMoveWeekOnlyRequestSchema,
  visitMoveWeekOnlyResponseSchema,
} from '../visitMoveWeekOnly';

const PATIENT_ID = '22222222-2222-4222-8222-222222222222';
const TPL_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

describe('visitMoveWeekOnlyRequestSchema', () => {
  const base = {
    iso_year: 2026,
    iso_week: 27,
    patient_id: PATIENT_ID,
    old_weekday: 0,
    old_start_time: '09:00',
    new_weekday: 2,
    new_start_time: '10:30',
  };

  it('必須フィールドをパースする (new_course_template_id 省略可)', () => {
    const parsed = visitMoveWeekOnlyRequestSchema.parse(base);
    expect(parsed.patient_id).toBe(PATIENT_ID);
    expect(parsed.old_weekday).toBe(0);
    expect(parsed.new_start_time).toBe('10:30');
    expect(parsed.new_course_template_id).toBeUndefined();
  });

  it('new_course_template_id を指定できる', () => {
    const parsed = visitMoveWeekOnlyRequestSchema.parse({
      ...base,
      new_course_template_id: TPL_ID,
    });
    expect(parsed.new_course_template_id).toBe(TPL_ID);
  });

  it('不正な時刻はエラー', () => {
    expect(
      visitMoveWeekOnlyRequestSchema.safeParse({ ...base, new_start_time: '99:99' }).success,
    ).toBe(false);
  });
});

describe('visitMoveWeekOnlyResponseSchema', () => {
  it('visits_moved をパースする', () => {
    expect(visitMoveWeekOnlyResponseSchema.parse({ visits_moved: 2 }).visits_moved).toBe(2);
  });

  it('寛容: visits_moved 欠落は 0', () => {
    expect(visitMoveWeekOnlyResponseSchema.parse({}).visits_moved).toBe(0);
  });
});
