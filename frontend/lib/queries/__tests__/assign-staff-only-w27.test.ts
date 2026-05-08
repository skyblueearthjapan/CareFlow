/**
 * Wave 27 Phase B-4/B-5: assignStaffOnlyResponseSchema + warnings テスト.
 *
 * カバーするシナリオ:
 *   1. warnings フィールドがある場合に parse 成功
 *   2. warnings フィールドが省略された場合は [] にデフォルト
 *   3. assignWarningSchema が正しいフィールドを持つことを確認
 *   4. warnings 内の不正なデータは ZodError を投げる
 */
import { describe, it, expect } from 'vitest';

import { assignStaffOnlyResponseSchema, assignWarningSchema } from '../assign_staff_only';

const baseResponse = {
  iso_year: 2026,
  iso_week: 19,
  courses_assigned: 3,
  message: 'ok',
};

const validWarning = {
  staff_id: '00000000-0000-0000-0000-000000000001',
  staff_name: '田中 一郎',
  event_id: '00000000-0000-0000-0000-000000000002',
  event_type: '研修',
  visit_id: '00000000-0000-0000-0000-000000000003',
  visit_start_time: '10:00',
  message: '研修時間帯と重複しています',
};

describe('assignStaffOnlyResponseSchema (Wave 27 Phase B-5)', () => {
  it('1. warnings フィールドがある場合に parse 成功', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      warnings: [validWarning],
    });
    expect(data.warnings).toHaveLength(1);
    expect(data.warnings[0]?.event_type).toBe('研修');
  });

  it('2. warnings フィールドが省略された場合は [] にデフォルト', () => {
    const data = assignStaffOnlyResponseSchema.parse(baseResponse);
    expect(data.warnings).toEqual([]);
  });

  it('3. warnings=[] の明示指定も parse 成功', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      warnings: [],
    });
    expect(data.warnings).toEqual([]);
  });
});

describe('assignWarningSchema (Wave 27 Phase B-5)', () => {
  it('4. 正しいフィールドの warning を parse できる', () => {
    const result = assignWarningSchema.parse(validWarning);
    expect(result.staff_id).toBe('00000000-0000-0000-0000-000000000001');
    expect(result.staff_name).toBe('田中 一郎');
    expect(result.event_type).toBe('研修');
  });

  it('5. staff_name が null でも parse 成功', () => {
    const result = assignWarningSchema.parse({ ...validWarning, staff_name: null });
    expect(result.staff_name).toBeNull();
  });

  it('6. staff_id が UUID 形式でない場合は ZodError', () => {
    expect(() => assignWarningSchema.parse({ ...validWarning, staff_id: 'not-a-uuid' })).toThrow();
  });
});
