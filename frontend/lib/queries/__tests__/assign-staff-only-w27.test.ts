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

import {
  applyStaffReviewRequestSchema,
  applyStaffReviewResponseSchema,
  assignStaffOnlyResponseSchema,
  assignWarningSchema,
  autoCommittedNoticeSchema,
  reviewItemSchema,
} from '../assign_staff_only';

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

describe('reviewItemSchema (Phase G-91 確認レビューフロー)', () => {
  const validReviewItem = {
    course_id: '00000000-0000-0000-0000-0000000000a1',
    office_name: '都賀拠点',
    course_code: 'A',
    weekday: 0,
    reason: 'gender',
    candidate_staff_id: '00000000-0000-0000-0000-0000000000a2',
    candidate_staff_name: '山田太郎',
    candidate_staff_sex: 'male',
    visits: [
      {
        patient_id: '00000000-0000-0000-0000-0000000000a3',
        patient_name: '女性のみ患者',
        start_time: '09:00:00',
        sex_restriction: 'female_only',
        is_cause: true,
      },
    ],
  };

  it('7. 正しい gender review item を parse できる', () => {
    const r = reviewItemSchema.parse(validReviewItem);
    expect(r.reason).toBe('gender');
    expect(r.candidate_staff_name).toBe('山田太郎');
    expect(r.visits[0]?.is_cause).toBe(true);
  });

  it('8. reason が consecutive/gender 以外なら ZodError', () => {
    expect(() => reviewItemSchema.parse({ ...validReviewItem, reason: 'other' })).toThrow();
  });

  it('9. review_items は省略時 [] にデフォルト', () => {
    const data = assignStaffOnlyResponseSchema.parse(baseResponse);
    expect(data.review_items).toEqual([]);
  });

  it('10. response に review_items を含めて parse できる', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      review_items: [validReviewItem],
    });
    expect(data.review_items).toHaveLength(1);
    expect(data.review_items[0]?.course_code).toBe('A');
  });

  it('11. linked_course_ids は省略時 [] にデフォルト', () => {
    const r = reviewItemSchema.parse(validReviewItem);
    expect(r.linked_course_ids).toEqual([]);
  });
});

describe('autoCommittedNoticeSchema / auto_committed_notices (Wave N-2)', () => {
  const validNotice = {
    course_id: 'ffffffff-ffff-ffff-ffff-ffffffffffff',
    course_code: 'A',
    weekday: 0,
    office_name: '都賀拠点',
    staff_name: '田中スタッフ',
    cause_patient_names: ['患者A', '患者B'],
    reason_kind: 'single_staff',
    reason_text: 'この曜日に都賀拠点で勤務できるスタッフが田中スタッフ 1 名のため、連続担当は避けられません',
  };

  it('15. 正しい auto_committed_notice を parse できる', () => {
    const n = autoCommittedNoticeSchema.parse(validNotice);
    expect(n.course_code).toBe('A');
    expect(n.reason_kind).toBe('single_staff');
    expect(n.cause_patient_names).toHaveLength(2);
  });

  it('16. office_name が null でも parse 成功 (nullable)', () => {
    const n = autoCommittedNoticeSchema.parse({ ...validNotice, office_name: null });
    expect(n.office_name).toBeNull();
  });

  it('17. office_name が欠落でも parse 成功 (optional)', () => {
    const withoutOffice = {
      course_id: validNotice.course_id,
      course_code: validNotice.course_code,
      weekday: validNotice.weekday,
      staff_name: validNotice.staff_name,
      cause_patient_names: validNotice.cause_patient_names,
      reason_kind: validNotice.reason_kind,
      reason_text: validNotice.reason_text,
      // office_name を意図的に省略
    };
    const n = autoCommittedNoticeSchema.parse(withoutOffice);
    expect(n.office_name).toBeUndefined();
  });

  it('18. reason_kind が未知の値でも parse 成功 (z.string() で寛容)', () => {
    const n = autoCommittedNoticeSchema.parse({ ...validNotice, reason_kind: 'future_kind' });
    expect(n.reason_kind).toBe('future_kind');
  });

  it('19. auto_committed_notices は旧 BE レスポンス (フィールド欠落) で [] にフォールバック', () => {
    const data = assignStaffOnlyResponseSchema.parse(baseResponse);
    expect(data.auto_committed_notices).toEqual([]);
  });

  it('20. auto_committed_notices を含むレスポンスを parse できる', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      auto_committed_notices: [validNotice],
    });
    expect(data.auto_committed_notices).toHaveLength(1);
    expect(data.auto_committed_notices[0]?.staff_name).toBe('田中スタッフ');
  });

  it('21. auto_committed_notices がパース不能な値でも [] にフォールバック (.catch([]))', () => {
    const data = assignStaffOnlyResponseSchema.parse({
      ...baseResponse,
      auto_committed_notices: 'invalid',
    });
    expect(data.auto_committed_notices).toEqual([]);
  });
});

describe('applyStaffReview schemas (Phase G-91 修正1)', () => {
  it('12. 正しい apply request を parse できる', () => {
    const req = applyStaffReviewRequestSchema.parse({
      iso_year: 2026,
      iso_week: 19,
      items: [
        {
          course_id: '00000000-0000-0000-0000-0000000000b1',
          staff_id: '00000000-0000-0000-0000-0000000000b2',
        },
      ],
    });
    expect(req.items).toHaveLength(1);
    expect(req.items[0]?.staff_id).toBe('00000000-0000-0000-0000-0000000000b2');
  });

  it('13. items 空配列は ZodError (min 1)', () => {
    expect(() =>
      applyStaffReviewRequestSchema.parse({ iso_year: 2026, iso_week: 19, items: [] }),
    ).toThrow();
  });

  it('14. apply response (部分失敗) を parse できる', () => {
    const res = applyStaffReviewResponseSchema.parse({
      applied_count: 1,
      results: [
        { course_id: '00000000-0000-0000-0000-0000000000b1', ok: true },
        { course_id: '00000000-0000-0000-0000-0000000000b3', ok: false, error: 'not found' },
      ],
      message: 'Applied staff to 1 courses',
    });
    expect(res.applied_count).toBe(1);
    expect(res.results.filter((r) => !r.ok)).toHaveLength(1);
  });
});
