/**
 * autoScheduleV2 zod schema 単体テスト (Wave 4 Phase C).
 *
 * BE 側 ``backend/app/schemas/v2/auto_schedule_v2.py`` (V2WarningOut /
 * UnassignedPatient) と FE スキーマが厳密一致していることを確認する.
 *
 * カバーするケース:
 *   v2WarningSchema:
 *     1. category 必須 (省略時は ZodError)
 *     2. 新 type ``care_alarm_deviation`` を受理
 *     3. 既存 type (travel_time_shortage 等) も引き続き受理
 *     4. 6 種の category enum すべて受理
 *     5. 未知の category は ZodError
 *   unassignedPatientSchema:
 *     6. 新 reason ``care_alarm_exceeded`` を受理
 *     7. 既存 reason (course_capacity 等) も引き続き受理
 *   v2WarningCategorySchema:
 *     8. 6 種すべて parse 成功 / 未知値は reject
 */
import { describe, it, expect } from 'vitest';

import {
  unassignedPatientSchema,
  v2WarningCategorySchema,
  v2WarningSchema,
  type V2WarningCategory,
} from '../autoScheduleV2';

const PATIENT_ID = '00000000-0000-4000-8000-000000000001';

function baseWarning() {
  return {
    type: 'travel_time_shortage' as const,
    message: '希望時刻に間に合わない',
    weekday: 1,
    actionable: false,
    patient_id: PATIENT_ID,
    patient_name: '山田 太郎',
    visit_id: null,
    current_time: '09:30',
    suggested_time: '10:00',
    time_type: '固定',
    preferred_start: '09:30',
    preferred_end: '10:00',
    affected_patient_ids: [PATIENT_ID],
    category: 'time_deviation' as const,
  };
}

function baseUnassigned() {
  return {
    patient_id: PATIENT_ID,
    patient_name: '佐藤 花子',
    patient_code: null,
    reason: 'course_capacity' as const,
    reason_detail: null,
    dropped_at_stage: null,
  };
}

describe('v2WarningSchema (Wave 4 Phase C)', () => {
  it('1. category が無いと reject される (BE 必須に追従)', () => {
    const { category: _omit, ...withoutCategory } = baseWarning();
    void _omit;
    const result = v2WarningSchema.safeParse(withoutCategory);
    expect(result.success).toBe(false);
  });

  it('2. 新 type ``care_alarm_deviation`` を受理する', () => {
    const result = v2WarningSchema.safeParse({
      ...baseWarning(),
      type: 'care_alarm_deviation',
      category: 'time_deviation',
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.type).toBe('care_alarm_deviation');
      expect(result.data.category).toBe('time_deviation');
    }
  });

  it('3. 既存 type (travel_time_shortage / same_address_consolidation / general) も受理', () => {
    for (const t of [
      'travel_time_shortage',
      'same_address_consolidation',
      'course_capacity',
      'course_long_distance',
      'course_count',
      'acceptance_blocked',
      'two_staff_shortage',
      'diff_add_conflict',
      'data_health_staff_shifts_missing',
      'auto_time_shift_for_conflict',
      'general',
    ] as const) {
      const result = v2WarningSchema.safeParse({ ...baseWarning(), type: t });
      expect(result.success, `type=${t} should parse`).toBe(true);
    }
  });

  it('4. 6 種の category enum すべてを受理する', () => {
    const all: readonly V2WarningCategory[] = [
      'time_deviation',
      'capacity',
      'acceptance',
      'data_quality',
      'placement_info',
      'conflict',
    ];
    for (const cat of all) {
      const result = v2WarningSchema.safeParse({ ...baseWarning(), category: cat });
      expect(result.success, `category=${cat} should parse`).toBe(true);
    }
  });

  it('5. 未知の category は ZodError', () => {
    const result = v2WarningSchema.safeParse({ ...baseWarning(), category: 'unknown_cat' });
    expect(result.success).toBe(false);
  });
});

describe('unassignedPatientSchema (Wave 4 Phase C)', () => {
  it('6. 新 reason ``care_alarm_exceeded`` を受理する', () => {
    const result = unassignedPatientSchema.safeParse({
      ...baseUnassigned(),
      reason: 'care_alarm_exceeded',
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.reason).toBe('care_alarm_exceeded');
    }
  });

  it('7. 既存 reason も引き続き受理', () => {
    for (const r of [
      'no_coordinates',
      'no_primary_office',
      'no_weekly_pattern',
      'acceptance_calendar',
      'course_capacity',
      'course_overflow',
      'manager_short',
      'same_address_split',
      'fixed_time_conflict',
      'lunch_break',
      'unknown',
    ] as const) {
      const result = unassignedPatientSchema.safeParse({ ...baseUnassigned(), reason: r });
      expect(result.success, `reason=${r} should parse`).toBe(true);
    }
  });
});

describe('v2WarningCategorySchema (Wave 4 Phase C)', () => {
  it('8. 6 種すべて parse 成功 / 未知値は reject', () => {
    for (const cat of [
      'time_deviation',
      'capacity',
      'acceptance',
      'data_quality',
      'placement_info',
      'conflict',
    ] as const) {
      expect(v2WarningCategorySchema.safeParse(cat).success).toBe(true);
    }
    expect(v2WarningCategorySchema.safeParse('foo').success).toBe(false);
    expect(v2WarningCategorySchema.safeParse(null).success).toBe(false);
  });
});
