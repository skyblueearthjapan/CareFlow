/**
 * NG スタッフ (patient-ng-staff-design.md §4-2) の zod 契約テスト.
 *
 * カバーするシナリオ:
 *   1. review_items の reason に 'ng_staff' を受け入れる
 *   2. also_violates の parse / 省略時 []
 *   3. unresolved_ng_warnings / secondary_constraint_warnings の parse
 *   4. 旧レスポンス (新フィールド無し) の後方互換 = [] にフォールバック
 *   5. パース不能な値でも .catch([]) でセクション全滅しない
 *   6. apply-staff-review リクエストに reason / also_violates を載せられる (任意)
 */
import { describe, it, expect } from 'vitest';

import {
  applyStaffReviewRequestSchema,
  assignStaffOnlyResponseSchema,
  reviewItemSchema,
  secondaryConstraintWarningSchema,
  unresolvedNgWarningSchema,
} from '../assign_staff_only';

const baseResponse = {
  iso_year: 2026,
  iso_week: 33,
  courses_assigned: 5,
  message: 'ok',
};

const ngReviewItem = {
  course_id: '00000000-0000-0000-0000-0000000000c1',
  office_name: '稲毛拠点',
  course_code: 'D',
  weekday: 4,
  reason: 'ng_staff',
  candidate_staff_id: '00000000-0000-0000-0000-0000000000c2',
  candidate_staff_name: 'NG候補 花子',
  candidate_staff_sex: 'female',
  visits: [
    {
      patient_id: '00000000-0000-0000-0000-0000000000c3',
      patient_name: 'NG指定患者',
      start_time: '11:00:00',
      sex_restriction: null,
      is_cause: true,
    },
  ],
};

const validUnresolvedNg = {
  course_id: '00000000-0000-0000-0000-0000000000d1',
  course_code: 'C',
  weekday: 1,
  office_name: '都賀拠点',
  current_staff_name: 'NG該当 太郎',
  patient_names: ['山田様', '佐藤様'],
  reason_text: 'NGスタッフ以外の候補が見つかりません — 手動で調整してください',
};

const validSecondary = {
  course_id: '00000000-0000-0000-0000-0000000000e1',
  course_code: 'B',
  office_name: '稲毛拠点',
  weekday: 2,
  staff_id: '00000000-0000-0000-0000-0000000000e2',
  staff_name: '二人目 次郎',
  patient_id: '00000000-0000-0000-0000-0000000000e3',
  patient_name: '高橋',
  kind: 'ng_staff',
};

describe('reviewItemSchema — NG スタッフ対応', () => {
  it('1. reason=ng_staff を parse できる', () => {
    const r = reviewItemSchema.parse(ngReviewItem);
    expect(r.reason).toBe('ng_staff');
    expect(r.candidate_staff_name).toBe('NG候補 花子');
  });

  it('2. also_violates は省略時 [] にデフォルト (旧 BE 後方互換)', () => {
    const r = reviewItemSchema.parse(ngReviewItem);
    expect(r.also_violates).toEqual([]);
  });

  it('3. also_violates を明示すると保持される (性別 + NG の同時該当)', () => {
    const r = reviewItemSchema.parse({
      ...ngReviewItem,
      reason: 'gender',
      also_violates: ['ng_staff'],
    });
    expect(r.also_violates).toEqual(['ng_staff']);
  });

  it('4. reason が未知の値なら ZodError (consecutive/gender/ng_staff のみ)', () => {
    expect(() => reviewItemSchema.parse({ ...ngReviewItem, reason: 'other' })).toThrow();
  });
});

describe('unresolvedNgWarningSchema / unresolved_ng_warnings', () => {
  it('5. 正しい NG 残留警告を parse できる', () => {
    const w = unresolvedNgWarningSchema.parse(validUnresolvedNg);
    expect(w.current_staff_name).toBe('NG該当 太郎');
    expect(w.patient_names).toHaveLength(2);
  });

  it('6. patient_names 省略時は [] にデフォルト', () => {
    const { patient_names: _omit, ...withoutNames } = validUnresolvedNg;
    const w = unresolvedNgWarningSchema.parse(withoutNames);
    expect(w.patient_names).toEqual([]);
  });

  it('7. office_name が null でも parse 成功', () => {
    const w = unresolvedNgWarningSchema.parse({ ...validUnresolvedNg, office_name: null });
    expect(w.office_name).toBeNull();
  });

  it('8. レスポンスに含めて parse できる', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      unresolved_ng_warnings: [validUnresolvedNg],
    });
    expect(data.unresolved_ng_warnings).toHaveLength(1);
  });

  it('9. 旧 BE レスポンス (フィールド欠落) では [] にフォールバック', () => {
    const data = assignStaffOnlyResponseSchema.parse(baseResponse);
    expect(data.unresolved_ng_warnings).toEqual([]);
  });

  it('10. パース不能な値でも [] にフォールバック (.catch([]))', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      unresolved_ng_warnings: 'invalid',
    });
    expect(data.unresolved_ng_warnings).toEqual([]);
  });
});

describe('secondaryConstraintWarningSchema / secondary_constraint_warnings', () => {
  it('11. kind=ng_staff を parse できる', () => {
    const w = secondaryConstraintWarningSchema.parse(validSecondary);
    expect(w.kind).toBe('ng_staff');
    expect(w.staff_name).toBe('二人目 次郎');
  });

  it('12. kind=gender も parse できる', () => {
    const w = secondaryConstraintWarningSchema.parse({ ...validSecondary, kind: 'gender' });
    expect(w.kind).toBe('gender');
  });

  it('13. kind が未知の値なら ZodError', () => {
    expect(() =>
      secondaryConstraintWarningSchema.parse({ ...validSecondary, kind: 'other' }),
    ).toThrow();
  });

  it('14. レスポンスに含めて parse できる', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      secondary_constraint_warnings: [validSecondary],
    });
    expect(data.secondary_constraint_warnings).toHaveLength(1);
    expect(data.secondary_constraint_warnings[0]?.patient_name).toBe('高橋');
  });

  it('15. 旧 BE レスポンス (フィールド欠落) では [] にフォールバック', () => {
    const data = assignStaffOnlyResponseSchema.parse(baseResponse);
    expect(data.secondary_constraint_warnings).toEqual([]);
  });

  it('16. パース不能な値でも [] にフォールバック (.catch([]))', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      secondary_constraint_warnings: { bad: true },
    });
    expect(data.secondary_constraint_warnings).toEqual([]);
  });
});

describe('applyStaffReviewRequestSchema — reason / also_violates 同送', () => {
  it('17. reason / also_violates を含めて parse でき、値が保持される', () => {
    const req = applyStaffReviewRequestSchema.parse({
      iso_year: 2026,
      iso_week: 33,
      items: [
        {
          course_id: '00000000-0000-0000-0000-0000000000f1',
          staff_id: '00000000-0000-0000-0000-0000000000f2',
          reason: 'ng_staff',
          also_violates: ['gender'],
        },
      ],
    });
    expect(req.items[0]?.reason).toBe('ng_staff');
    expect(req.items[0]?.also_violates).toEqual(['gender']);
  });

  it('18. reason / also_violates 省略でも parse 成功 (後方互換)', () => {
    const req = applyStaffReviewRequestSchema.parse({
      iso_year: 2026,
      iso_week: 33,
      items: [
        {
          course_id: '00000000-0000-0000-0000-0000000000f1',
          staff_id: '00000000-0000-0000-0000-0000000000f2',
        },
      ],
    });
    expect(req.items[0]?.reason).toBeUndefined();
    expect(req.items[0]?.also_violates).toBeUndefined();
  });

  it('19. reason が未知の値なら ZodError', () => {
    expect(() =>
      applyStaffReviewRequestSchema.parse({
        iso_year: 2026,
        iso_week: 33,
        items: [
          {
            course_id: '00000000-0000-0000-0000-0000000000f1',
            staff_id: '00000000-0000-0000-0000-0000000000f2',
            reason: 'other',
          },
        ],
      }),
    ).toThrow();
  });
});
