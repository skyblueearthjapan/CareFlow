/**
 * 新人同行 zod schemas (§6) の単体テスト。
 */
import { describe, it, expect } from 'vitest';

import {
  parseOverlapDetail,
  traineeAccompanimentsResponseSchema,
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
            a: { visit_id: UUID, patient_name: '山田', start: '10:00', end: '11:00', course_code: 'A' },
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
