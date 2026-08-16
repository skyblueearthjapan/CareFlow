/**
 * 新人同行 zod schemas (§6) の単体テスト。
 */
import { describe, it, expect } from 'vitest';

import {
  accompanimentStaffName,
  formatAccompanimentConflict,
  parseAccompanimentConflictDetail,
  parseOverlapDetail,
  traineeAccompanimentsPutSchema,
  traineeAccompanimentsResponseSchema,
  traineeCourseGuardResponseSchema,
  visitAccompaniments,
} from '../trainee_accompaniment';

const UUID = '00000000-0000-0000-0000-000000000001';

describe('traineeAccompanimentsResponseSchema', () => {
  it('コース/個別リンク混在の items をパースできる', () => {
    const parsed = traineeAccompanimentsResponseSchema.parse({
      items: [
        {
          id: UUID,
          trainee_staff_id: UUID,
          trainee_staff_name: '髙梨',
          target_type: 'course',
          source: 'default',
          course: { id: UUID, weekday: 0, code: 'A', office_id: UUID, template_id: UUID },
          visit: null,
        },
        {
          id: '00000000-0000-0000-0000-000000000002',
          trainee_staff_id: UUID,
          trainee_staff_name: '髙梨',
          target_type: 'visit',
          source: 'manual',
          course: null,
          visit: { id: UUID, date: '2026-07-14', start: '10:00', patient_name: '山田' },
        },
      ],
    });
    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[0]!.course?.code).toBe('A');
    expect(parsed.items[1]!.visit?.patient_name).toBe('山田');
  });
});

describe('parseOverlapDetail', () => {
  it('422 の { detail: { message, overlaps } } を取り出す', () => {
    const detail = parseOverlapDetail({
      detail: {
        message: '時間が重複しています',
        overlaps: [
          {
            date: '2026-07-14',
            a: {
              visit_id: UUID,
              patient_name: '山田',
              start: '10:00',
              end: '11:00',
              course_code: 'A',
            },
            b: {
              visit_id: '00000000-0000-0000-0000-000000000002',
              patient_name: '佐藤',
              start: '10:00',
              end: '11:00',
              course_code: 'C',
            },
          },
        ],
      },
    });
    expect(detail).not.toBeNull();
    expect(detail!.overlaps).toHaveLength(1);
    expect(detail!.overlaps[0]!.a.patient_name).toBe('山田');
  });

  it('形が違えば null (安全な劣化)', () => {
    expect(parseOverlapDetail({ detail: 'nope' })).toBeNull();
    expect(parseOverlapDetail(null)).toBeNull();
    expect(parseOverlapDetail({ foo: 1 })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 一般化 (general-accompaniment-design.md)
// ---------------------------------------------------------------------------

describe('kind / staff_name の追随', () => {
  it('kind と staff_name を持つ items をパースし、表示名は新旧フォールバックする', () => {
    const parsed = traineeAccompanimentsResponseSchema.parse({
      items: [
        {
          id: UUID,
          trainee_staff_id: UUID,
          staff_name: '熊澤',
          kind: 'support',
          target_type: 'visit',
          source: 'manual',
          visit: { id: UUID, date: '2026-08-18' },
        },
      ],
    });
    expect(parsed.items[0]!.kind).toBe('support');
    expect(accompanimentStaffName(parsed.items[0]!)).toBe('熊澤');
  });

  it('旧デプロイ (kind/staff_name なし) でも壊れず trainee_staff_name に落ちる', () => {
    const parsed = traineeAccompanimentsResponseSchema.parse({
      items: [
        {
          id: UUID,
          trainee_staff_id: UUID,
          trainee_staff_name: '髙梨',
          target_type: 'visit',
          source: 'manual',
          visit: { id: UUID, date: '2026-08-18' },
        },
      ],
    });
    expect(parsed.items[0]!.kind ?? null).toBeNull();
    expect(accompanimentStaffName(parsed.items[0]!)).toBe('髙梨');
  });
});

describe('parseAccompanimentConflictDetail / formatAccompanimentConflict (確定#1)', () => {
  const body = {
    detail: {
      code: 'accompaniment_overlap',
      conflicts: [
        {
          date: '2026-08-18',
          weekday: 1,
          start: '10:00',
          end: '10:35',
          patient_name: '山田 太郎',
          course_label: '稲毛A',
          reason: 'own_duty',
        },
      ],
    },
  };

  it('構造化 detail をパースできる', () => {
    const detail = parseAccompanimentConflictDetail(body);
    expect(detail).not.toBeNull();
    expect(detail!.conflicts).toHaveLength(1);
    expect(detail!.conflicts[0]!.reason).toBe('own_duty');
  });

  it('own_duty は「ご自身の担当と重なるため登録できません」と言語化する', () => {
    const msg = formatAccompanimentConflict(parseAccompanimentConflictDetail(body)!.conflicts[0]!);
    expect(msg).toBe(
      '8月18日(火) 10:00〜10:35 は 山田 太郎様（稲毛A・ご自身の担当）と重なるため登録できません',
    );
  });

  it('accompaniment は「別の同行と重なる」文言になる', () => {
    const msg = formatAccompanimentConflict({
      date: '2026-08-19',
      weekday: 2,
      start: '13:00:00',
      end: '13:35:00',
      patient_name: '佐藤 花子',
      course_label: '稲毛B',
      reason: 'accompaniment',
    });
    expect(msg).toBe(
      '8月19日(水) 13:00〜13:35 は 佐藤 花子様（稲毛B）の別の同行と重なるため登録できません',
    );
  });

  it('weekday 欠落時は日付から曜日を導く / 未知の reason でも文にする', () => {
    // 2026-08-18 は火曜。
    const msg = formatAccompanimentConflict({
      date: '2026-08-18',
      start: '09:00',
      end: '09:35',
      patient_name: '鈴木 一郎',
      course_label: null,
      reason: 'unknown_reason',
    });
    expect(msg).toContain('8月18日(火)');
    expect(msg).toContain('鈴木 一郎様と重なるため登録できません');
  });

  it('形が違えば null (旧形 422 と取り違えない)', () => {
    expect(parseAccompanimentConflictDetail({ detail: { message: 'x', overlaps: [] } })).toBeNull();
    expect(parseAccompanimentConflictDetail(null)).toBeNull();
  });
});

describe('visitAccompaniments (確定#5 複数名 + 旧単数フォールバック)', () => {
  it('accompaniments[] があれば全員返す', () => {
    expect(
      visitAccompaniments({
        accompaniments: [
          { staff_id: 'a', staff_name: '髙梨', kind: 'trainee' },
          { staff_id: 'b', staff_name: '熊澤', kind: 'support' },
        ],
        accompaniment: { staff_id: 'a', staff_name: '髙梨' },
      }).map((a) => a.staff_name),
    ).toEqual(['髙梨', '熊澤']);
  });

  it('accompaniments が無い旧レスポンスは単数へフォールバック', () => {
    expect(
      visitAccompaniments({ accompaniment: { staff_id: 'a', staff_name: '髙梨' } }),
    ).toHaveLength(1);
    expect(visitAccompaniments({ accompaniments: [], accompaniment: null })).toEqual([]);
    expect(visitAccompaniments({})).toEqual([]);
  });
});

describe('traineeCourseGuardResponseSchema (applicable の追随)', () => {
  const base = { trainee_staff_id: UUID, count: 0, courses: [] };

  it('applicable つきのレスポンスを受理する', () => {
    const parsed = traineeCourseGuardResponseSchema.parse({ ...base, applicable: false });
    expect(parsed.applicable).toBe(false);
    // 表示分岐は従来どおり count を見る (applicable は受理のみ)。
    expect(parsed.count).toBe(0);
  });

  it('applicable が無い旧デプロイのレスポンスも受理する', () => {
    expect(traineeCourseGuardResponseSchema.parse(base).applicable).toBeUndefined();
  });
});

describe('PUT ボディのキー (staff_id へ移行)', () => {
  it('staff_id を受理し、旧 trainee_staff_id だけのボディは弾く', () => {
    expect(
      traineeAccompanimentsPutSchema.parse({
        staff_id: UUID,
        iso_year: 2026,
        iso_week: 34,
        course_ids: [],
        visit_ids: [],
      }).staff_id,
    ).toBe(UUID);
    expect(
      traineeAccompanimentsPutSchema.safeParse({
        trainee_staff_id: UUID,
        iso_year: 2026,
        iso_week: 34,
        course_ids: [],
        visit_ids: [],
      }).success,
    ).toBe(false);
  });
});
