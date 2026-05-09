/**
 * placeAndFixRequestSchema vitest unit tests (W37 Phase 3-A).
 *
 * BE 側 (`backend/app/api/v1/schedule.py::PlaceAndFixRequest._validate_course_templates_shape`)
 * のバリデーション仕様を Zod 側でもミラーしていることを担保する。
 *
 * カバーするケース:
 *   1. 旧形式 (course_template_id, staff_count=1) → OK
 *   2. 新形式 1 件 + staff_count=1 → OK
 *   3. 新形式 2 件 + staff_count=2 (異なる UUID) → OK
 *   4. 新形式 + 旧形式 同時指定 → エラー
 *   5. どちらも未指定 → エラー
 *   6. 新形式 件数 != staff_count → エラー
 *   7. staff_count=2 で同一 UUID → エラー
 *   8. 旧形式 (course_template_id) + staff_count=2 → エラー
 */
import { describe, it, expect } from 'vitest';

import { placeAndFixRequestSchema } from '../place_and_fix';

const BASE = {
  patient_id: '00000000-0000-0000-0000-000000000001',
  iso_year: 2026,
  iso_week: 19,
  weekday: 0,
  start_time: '09:00',
  duration_min: 30,
  fix_pattern: true,
} as const;

const COURSE_A = 'aaaaaaaa-0000-0000-0000-000000000001';
const COURSE_B = 'bbbbbbbb-0000-0000-0000-000000000002';

describe('placeAndFixRequestSchema (W37 Phase 3-A)', () => {
  it('1. 旧形式 (course_template_id) + staff_count=1 → 通る', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_id: COURSE_A,
      staff_count: 1,
    });
    expect(result.success).toBe(true);
  });

  it('2. 新形式 1 件 + staff_count=1 → 通る', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_ids: [COURSE_A],
      staff_count: 1,
    });
    expect(result.success).toBe(true);
  });

  it('3. 新形式 2 件 (異なる UUID) + staff_count=2 → 通る', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_ids: [COURSE_A, COURSE_B],
      staff_count: 2,
    });
    expect(result.success).toBe(true);
  });

  it('4. 旧形式 + 新形式 同時指定 → エラー', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_id: COURSE_A,
      course_template_ids: [COURSE_B],
      staff_count: 1,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.message.includes('同時に指定できません'))).toBe(
        true,
      );
    }
  });

  it('5. どちらも未指定 → エラー', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      staff_count: 1,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(
        result.error.issues.some((i) => i.message.includes('いずれかを指定してください')),
      ).toBe(true);
    }
  });

  it('6. 新形式 件数 != staff_count → エラー', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_ids: [COURSE_A],
      staff_count: 2,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.message.includes('一致しません'))).toBe(true);
    }
  });

  it('7. staff_count=2 で同一 UUID → エラー', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_ids: [COURSE_A, COURSE_A],
      staff_count: 2,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.message.includes('2 つの異なる UUID'))).toBe(true);
    }
  });

  it('8. 旧形式 (course_template_id) + staff_count=2 → エラー', () => {
    const result = placeAndFixRequestSchema.safeParse({
      ...BASE,
      course_template_id: COURSE_A,
      staff_count: 2,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(
        result.error.issues.some(
          (i) => i.message.includes('旧形式') && i.message.includes('staff_count=1 のみ'),
        ),
      ).toBe(true);
    }
  });
});
