/**
 * poolOverviewResponseSchema — 寛容パーステスト (Stage P-2).
 *
 * 契約:
 *   - items 欠落 → [] にフォールバック
 *   - best_slot 欠落 → null
 *   - best_delta_minutes 欠落 → null
 *   - candidate_count 欠落 → 0
 *   - top_excluded_reason 未知値 → そのまま文字列として保持
 */
import { describe, it, expect } from 'vitest';

import { poolOverviewResponseSchema, poolOverviewItemSchema } from '../poolOverview';

describe('poolOverviewResponseSchema — 寛容パース', () => {
  it('完全なレスポンスが通る', () => {
    const raw = {
      items: [
        {
          patient_id: 'p1',
          best_slot: {
            weekday: 0,
            course_code: 'A',
            office_id: 'o1',
            start_time: '09:00',
            end_time: '09:30',
          },
          best_delta_minutes: 5.5,
          candidate_count: 3,
          top_excluded_reason: null,
        },
      ],
    };
    const result = poolOverviewResponseSchema.safeParse(raw);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.items).toHaveLength(1);
      expect(result.data.items[0]!.patient_id).toBe('p1');
      expect(result.data.items[0]!.best_delta_minutes).toBe(5.5);
    }
  });

  it('items フィールド欠落 → [] にフォールバック', () => {
    const result = poolOverviewResponseSchema.safeParse({});
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.items).toEqual([]);
    }
  });

  it('items が null → パース失敗 (null は許容しない)', () => {
    // items は z.array().default([]) なので undefined → [] だが null は型エラー
    const result = poolOverviewResponseSchema.safeParse({ items: null });
    expect(result.success).toBe(false);
  });
});

describe('poolOverviewItemSchema — 寛容パース', () => {
  const PATIENT_ID = 'patient-abc';

  it('必須フィールドのみ → 欠落フィールドはデフォルト補完', () => {
    const result = poolOverviewItemSchema.safeParse({ patient_id: PATIENT_ID });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.best_slot).toBeNull();
      expect(result.data.best_delta_minutes).toBeNull();
      expect(result.data.candidate_count).toBe(0);
      expect(result.data.top_excluded_reason).toBeNull();
    }
  });

  it('best_delta_minutes が 0 → そのまま保持 (null 扱いしない)', () => {
    const result = poolOverviewItemSchema.safeParse({
      patient_id: PATIENT_ID,
      best_delta_minutes: 0,
      candidate_count: 2,
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.best_delta_minutes).toBe(0);
    }
  });

  it('top_excluded_reason に未知の文字列 → そのまま文字列として保持', () => {
    const result = poolOverviewItemSchema.safeParse({
      patient_id: PATIENT_ID,
      candidate_count: 0,
      top_excluded_reason: 'future_unknown_reason',
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.top_excluded_reason).toBe('future_unknown_reason');
    }
  });

  it('best_slot が欠落 (undefined) → null にフォールバック', () => {
    const result = poolOverviewItemSchema.safeParse({
      patient_id: PATIENT_ID,
      candidate_count: 1,
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.best_slot).toBeNull();
    }
  });

  it('candidate_count が欠落 → 0 にフォールバック', () => {
    const result = poolOverviewItemSchema.safeParse({ patient_id: PATIENT_ID });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.candidate_count).toBe(0);
    }
  });

  it('全フィールドが揃った items 配列をラップしたレスポンスが通る', () => {
    const result = poolOverviewResponseSchema.safeParse({
      items: [
        { patient_id: 'p1', candidate_count: 0, top_excluded_reason: 'capacity_full' },
        { patient_id: 'p2', best_delta_minutes: 10, candidate_count: 2 },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.items).toHaveLength(2);
      expect(result.data.items[0]!.top_excluded_reason).toBe('capacity_full');
      expect(result.data.items[1]!.best_delta_minutes).toBe(10);
    }
  });
});
