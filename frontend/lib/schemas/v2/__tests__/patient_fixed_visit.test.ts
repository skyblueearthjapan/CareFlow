/**
 * patientFixedVisitsBulkPutSchema vitest unit tests (W37 Phase 3-A).
 *
 * W37 Phase 1: (weekday, slot_index) のペアで重複検出する。
 *
 * カバーするケース:
 *   1. 同 weekday × 異 slot_index (0/1) → 通る
 *   2. 同 weekday × 同 slot_index (0,0) → エラー
 *   3. 異 weekday × slot_index 重複なし → 通る
 *   4. slot_index を省略 → default 0 として扱われる
 *   5. items 上限 14 件 (7 曜日 × slot 0/1)
 */
import { describe, it, expect } from 'vitest';

import {
  patientFixedVisitsBulkPutSchema,
  patientFixedVisitV2BaseSchema,
} from '../patient_fixed_visit';

const COURSE_A = 'aaaaaaaa-0000-0000-0000-000000000001';
const COURSE_B = 'bbbbbbbb-0000-0000-0000-000000000002';

describe('patientFixedVisitV2BaseSchema (W37 Phase 1)', () => {
  it('slot_index がデフォルト 0 になる (省略可)', () => {
    const parsed = patientFixedVisitV2BaseSchema.parse({
      weekday: 0,
      start_time: '09:00',
      duration_min: 30,
    });
    expect(parsed.slot_index).toBe(0);
  });

  it('slot_index=1 を許容する', () => {
    const parsed = patientFixedVisitV2BaseSchema.parse({
      weekday: 0,
      start_time: '09:00',
      duration_min: 30,
      slot_index: 1,
    });
    expect(parsed.slot_index).toBe(1);
  });

  it('slot_index=2 はエラー (範囲外)', () => {
    const result = patientFixedVisitV2BaseSchema.safeParse({
      weekday: 0,
      start_time: '09:00',
      duration_min: 30,
      slot_index: 2,
    });
    expect(result.success).toBe(false);
  });
});

describe('patientFixedVisitsBulkPutSchema (W37 Phase 1)', () => {
  it('1. 同 weekday × 異 slot_index (0/1) → 通る', () => {
    const result = patientFixedVisitsBulkPutSchema.safeParse({
      mode: 'normal',
      items: [
        {
          weekday: 0,
          start_time: '09:00',
          duration_min: 30,
          course_template_id: COURSE_A,
          slot_index: 0,
        },
        {
          weekday: 0,
          start_time: '09:00',
          duration_min: 30,
          course_template_id: COURSE_B,
          slot_index: 1,
        },
      ],
    });
    expect(result.success).toBe(true);
  });

  it('2. 同 weekday × 同 slot_index (0,0) → エラー', () => {
    const result = patientFixedVisitsBulkPutSchema.safeParse({
      mode: 'normal',
      items: [
        { weekday: 0, start_time: '09:00', duration_min: 30, slot_index: 0 },
        { weekday: 0, start_time: '14:00', duration_min: 60, slot_index: 0 },
      ],
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.message.includes('同じ曜日・スロットが重複'))).toBe(
        true,
      );
    }
  });

  it('3. 異 weekday × slot_index 重複なし → 通る', () => {
    const result = patientFixedVisitsBulkPutSchema.safeParse({
      mode: 'normal',
      items: [
        { weekday: 0, start_time: '09:00', duration_min: 30, slot_index: 0 },
        { weekday: 1, start_time: '09:00', duration_min: 30, slot_index: 0 },
        { weekday: 2, start_time: '09:00', duration_min: 30, slot_index: 1 },
      ],
    });
    expect(result.success).toBe(true);
  });

  it('4. slot_index を省略 → default 0 として扱われる + 重複検出される', () => {
    const result = patientFixedVisitsBulkPutSchema.safeParse({
      mode: 'normal',
      items: [
        { weekday: 0, start_time: '09:00', duration_min: 30 }, // slot_index default = 0
        { weekday: 0, start_time: '14:00', duration_min: 60, slot_index: 0 },
      ],
    });
    expect(result.success).toBe(false);
  });

  it('5. items 上限 14 件 (7 曜日 × slot 0/1) は通り、15 件は弾かれる', () => {
    const items14 = Array.from({ length: 7 }, (_, wd) => [
      { weekday: wd, start_time: '09:00', duration_min: 30, slot_index: 0 },
      { weekday: wd, start_time: '09:00', duration_min: 30, slot_index: 1 },
    ]).flat();
    const ok = patientFixedVisitsBulkPutSchema.safeParse({ mode: 'normal', items: items14 });
    expect(ok.success).toBe(true);

    const items15 = [
      ...items14,
      // 15 件目はもう slot を割り当てる場所がないので max 制約で弾かれる
      { weekday: 0, start_time: '09:00', duration_min: 30, slot_index: 0 },
    ];
    const ng = patientFixedVisitsBulkPutSchema.safeParse({ mode: 'normal', items: items15 });
    expect(ng.success).toBe(false);
  });
});
