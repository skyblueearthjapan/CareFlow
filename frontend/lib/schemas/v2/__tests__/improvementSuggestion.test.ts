/**
 * improvementSuggestion zod schema — 契約 1:1 / 寛容パース (warnings 系) の単体テスト.
 */
import { describe, it, expect } from 'vitest';

import {
  improvementDismissResponseSchema,
  improvementSuggestionsResponseSchema,
} from '../improvementSuggestion';

const CURRENT = {
  office_id: '11111111-1111-4111-8111-111111111111',
  weekday: 0,
  weekday_code: 'Mon',
  start_time: '09:00',
  end_time: '09:30',
  course_label: '稲A',
  staff_name: '山田',
};

const CANDIDATE = {
  office_id: '11111111-1111-4111-8111-111111111111',
  office_name: '稲毛',
  weekday: 0,
  weekday_code: 'Mon',
  start_time: '10:00',
  end_time: '10:30',
  course_code: 'B',
  course_label: '稲B',
  staff_name: '佐藤',
};

describe('improvementSuggestionsResponseSchema', () => {
  it('契約 1:1: 完全なレスポンスをパースする', () => {
    const parsed = improvementSuggestionsResponseSchema.parse({
      patient_id: '22222222-2222-4222-8222-222222222222',
      iso_year: 2026,
      iso_week: 27,
      suggestions: [
        {
          kind: 'time_change',
          target_weekday: 0,
          current: CURRENT,
          candidate: CANDIDATE,
          delta: { travel_minutes_saved: 18, travel_km_saved: 2.1 },
          changes: { changes: ['開始時刻が変わります'], unchanged: ['曜日は同じ'] },
          staff_warnings: ['staff_absent'],
          feasibility_basis: 'pfv',
          requires_patient_confirmation: true,
        },
      ],
      filtered_summary: {
        pinned: 1,
        locked: 0,
        no_current_visit: 0,
        dismissed: 2,
        below_threshold: 3,
        day_restricted: 0,
      },
    });
    expect(parsed.suggestions).toHaveLength(1);
    expect(parsed.suggestions[0]?.delta.travel_minutes_saved).toBe(18);
    expect(parsed.suggestions[0]?.requires_patient_confirmation).toBe(true);
    expect(parsed.filtered_summary.below_threshold).toBe(3);
  });

  it('default: filtered_summary / suggestions が欠けても既定値で埋める', () => {
    const parsed = improvementSuggestionsResponseSchema.parse({
      patient_id: '22222222-2222-4222-8222-222222222222',
      iso_year: 2026,
      iso_week: 27,
    });
    expect(parsed.suggestions).toEqual([]);
    expect(parsed.filtered_summary.pinned).toBe(0);
  });

  it('寛容パース: staff_warnings が配列でない drift でも空配列に落として採用フローを止めない', () => {
    const parsed = improvementSuggestionsResponseSchema.parse({
      patient_id: '22222222-2222-4222-8222-222222222222',
      iso_year: 2026,
      iso_week: 27,
      suggestions: [
        {
          kind: 'time_change',
          target_weekday: 0,
          current: CURRENT,
          candidate: CANDIDATE,
          delta: { travel_minutes_saved: 18, travel_km_saved: 2.1 },
          changes: { changes: [], unchanged: [] },
          // 不正 (本来は string[]) — .catch([]) で空に落ちる.
          staff_warnings: 'oops',
        },
      ],
    });
    expect(parsed.suggestions[0]?.staff_warnings).toEqual([]);
    // feasibility_basis 未指定 → default 'pfv'.
    expect(parsed.suggestions[0]?.feasibility_basis).toBe('pfv');
    expect(parsed.suggestions[0]?.requires_patient_confirmation).toBe(false);
  });
});

describe('improvementDismissResponseSchema', () => {
  it('movability 昇格レスポンスをパースする', () => {
    const parsed = improvementDismissResponseSchema.parse({
      dismissal_id: '33333333-3333-4333-8333-333333333333',
      movability_updated: true,
      new_movability: 'locked',
    });
    expect(parsed.movability_updated).toBe(true);
    expect(parsed.new_movability).toBe('locked');
  });

  it('default: movability 未更新 (null) をパースする', () => {
    const parsed = improvementDismissResponseSchema.parse({
      dismissal_id: '33333333-3333-4333-8333-333333333333',
    });
    expect(parsed.movability_updated).toBe(false);
    expect(parsed.new_movability).toBeNull();
  });
});
