/**
 * scheduleHealth zod schema 単体テスト (Schedule Advisor Phase 1).
 *
 * BE 契約 (凍結済) 準拠を施錠する:
 *   - 正常レスポンス (office → weekday → course + totals) を parse できる.
 *   - offices 空 (訪問データなし週) を parse できる.
 *   - 必須の数値フィールド欠落時に安全側 (default 0) で埋まる.
 */
import { describe, it, expect } from 'vitest';

import { scheduleHealthResponseSchema } from '../v2/scheduleHealth';

const FULL_RESPONSE = {
  iso_year: 2026,
  iso_week: 27,
  offices: [
    {
      office_id: 'office-1',
      office_name: '本部',
      weekdays: [
        {
          weekday: 0,
          courses: [
            {
              course_code: 'A',
              staff_name: '田中',
              visit_count: 5,
              patient_count: 5,
              service_minutes: 175,
              travel_minutes: 42,
              travel_km: 14.0,
              buffer_minutes: 32,
              gap_minutes: 18,
            },
          ],
          totals: {
            visit_count: 5,
            service_minutes: 175,
            travel_minutes: 42,
            travel_km: 14.0,
            buffer_minutes: 32,
            gap_minutes: 18,
          },
        },
      ],
      week_totals: {
        visit_count: 5,
        service_minutes: 175,
        travel_minutes: 42,
        travel_km: 14.0,
        buffer_minutes: 32,
        gap_minutes: 18,
      },
    },
  ],
};

describe('scheduleHealthResponseSchema', () => {
  it('正常レスポンスを parse できる', () => {
    const result = scheduleHealthResponseSchema.safeParse(FULL_RESPONSE);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.offices[0].weekdays[0].courses[0].travel_minutes).toBe(42);
      expect(result.data.offices[0].week_totals.gap_minutes).toBe(18);
    }
  });

  it('offices 空 (訪問データなし週) を parse できる', () => {
    const result = scheduleHealthResponseSchema.safeParse({
      iso_year: 2026,
      iso_week: 27,
      offices: [],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.offices).toEqual([]);
    }
  });

  it('course の数値欠落は default 0 で埋まる', () => {
    const result = scheduleHealthResponseSchema.safeParse({
      iso_year: 2026,
      iso_week: 27,
      offices: [
        {
          office_id: 'o1',
          office_name: '本部',
          weekdays: [
            {
              weekday: 1,
              courses: [{ course_code: 'B' }],
              totals: {},
            },
          ],
          week_totals: {},
        },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      const course = result.data.offices[0].weekdays[0].courses[0];
      expect(course.travel_minutes).toBe(0);
      expect(course.staff_name).toBeNull();
      expect(result.data.offices[0].week_totals.travel_km).toBe(0);
    }
  });
});
