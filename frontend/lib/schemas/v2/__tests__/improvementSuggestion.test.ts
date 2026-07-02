/**
 * improvementSuggestion zod schema — 契約 1:1 / 寛容パース (warnings 系) の単体テスト.
 */
import { describe, it, expect } from 'vitest';

import {
  applySwapRequestSchema,
  applySwapResponseSchema,
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

  it('swap: kind=swap + swap_counterpart をパースする (既定 null)', () => {
    const parsed = improvementSuggestionsResponseSchema.parse({
      patient_id: '22222222-2222-4222-8222-222222222222',
      iso_year: 2026,
      iso_week: 27,
      suggestions: [
        {
          kind: 'swap',
          target_weekday: 0,
          current: CURRENT,
          candidate: CANDIDATE,
          delta: { travel_minutes_saved: 12, travel_km_saved: 1.4 },
          changes: { changes: [], unchanged: [] },
          swap_counterpart: {
            patient_id: '33333333-3333-4333-8333-333333333333',
            patient_name: '佐藤 花子',
            current_weekday: 0,
            current_start_time: '10:00',
            new_weekday: 0,
            new_start_time: '09:00',
            requires_patient_confirmation: true,
          },
        },
      ],
    });
    expect(parsed.suggestions[0]?.kind).toBe('swap');
    expect(parsed.suggestions[0]?.swap_counterpart?.patient_name).toBe('佐藤 花子');
    // move カードは swap_counterpart 未指定 → 既定 null (後方互換).
    const move = improvementSuggestionsResponseSchema.parse({
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
        },
      ],
    });
    expect(move.suggestions[0]?.swap_counterpart).toBeNull();
  });

  it('寛容化: 未知 kind の要素は静かに除外し、既知要素だけ残す (セクション全滅の恒久解消)', () => {
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
        },
        // 将来 BE が返しうる未知 kind — 除外されるべき.
        {
          kind: 'future_kind',
          target_weekday: 1,
          current: CURRENT,
          candidate: CANDIDATE,
          delta: { travel_minutes_saved: 5, travel_km_saved: 0.5 },
          changes: { changes: [], unchanged: [] },
        },
      ],
    });
    expect(parsed.suggestions).toHaveLength(1);
    expect(parsed.suggestions[0]?.kind).toBe('time_change');
  });

  it('寛容化: 既知 kind でも必須フィールド欠損の破損要素は除外し、健全な要素は残す (レビューMINOR)', () => {
    const parsed = improvementSuggestionsResponseSchema.parse({
      patient_id: '22222222-2222-4222-8222-222222222222',
      iso_year: 2026,
      iso_week: 27,
      suggestions: [
        // 破損: 既知 kind だが current 欠損 → 静かに除外.
        {
          kind: 'time_change',
          target_weekday: 1,
          candidate: CANDIDATE,
          delta: { travel_minutes_saved: 5, travel_km_saved: 0.5 },
          changes: { changes: [], unchanged: [] },
        },
        // 健全な要素は生き残る.
        {
          kind: 'day_change',
          target_weekday: 0,
          current: CURRENT,
          candidate: CANDIDATE,
          delta: { travel_minutes_saved: 18, travel_km_saved: 2.1 },
          changes: { changes: [], unchanged: [] },
        },
      ],
    });
    expect(parsed.suggestions).toHaveLength(1);
    expect(parsed.suggestions[0]?.kind).toBe('day_change');
  });
});

describe('applySwapRequestSchema / applySwapResponseSchema', () => {
  it('request: a_new/b_new と iso 週をパースする (course_template_id 省略可)', () => {
    const parsed = applySwapRequestSchema.parse({
      patient_a_id: '22222222-2222-4222-8222-222222222222',
      patient_b_id: '33333333-3333-4333-8333-333333333333',
      a_new: {
        weekday: 0,
        start_time: '14:00',
        course_template_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      },
      b_new: { weekday: 0, start_time: '09:00' },
      iso_year: 2026,
      iso_week: 27,
    });
    expect(parsed.a_new.course_template_id).toBe('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
    expect(parsed.b_new.course_template_id).toBeUndefined();
  });

  it('response: 寛容パース (warnings drift は空配列に落とす)', () => {
    const parsed = applySwapResponseSchema.parse({ applied: true, warnings: 'oops' });
    expect(parsed.applied).toBe(true);
    expect(parsed.warnings).toEqual([]);
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
