/**
 * scopeOptimization zod schemas のテスト (範囲最適化 W1).
 *
 * 特に steps の「最長有効プレフィックス」寛容化 (未知 kind の手が現れたら
 * そこで打ち切り、以降を捨てる) を検証する。
 */
import { describe, expect, it } from 'vitest';

import {
  scopeOptimizationApplyRequestSchema,
  scopeOptimizationApplyResponseSchema,
  scopeOptimizationSimulateResponseSchema,
  tolerantStepsArraySchema,
} from '../scopeOptimization';

const OFFICE_ID = '11111111-1111-4111-8111-111111111111';
const PATIENT_ID = '22222222-2222-4222-8222-222222222222';

function makeStep(seq: number, kind: string = 'time_change') {
  return {
    seq,
    patient_id: PATIENT_ID,
    patient_name: 'P-TGT',
    suggestion: {
      kind,
      target_weekday: 0,
      current: {
        office_id: OFFICE_ID,
        weekday: 0,
        weekday_code: 'Mon',
        start_time: '10:30',
        end_time: '11:00',
        course_label: '稲A',
        staff_name: null,
      },
      candidate: {
        office_id: OFFICE_ID,
        office_name: null,
        weekday: 0,
        weekday_code: 'Mon',
        start_time: '12:30',
        end_time: '13:00',
        course_code: 'A',
        course_label: '稲A',
        staff_name: null,
      },
      delta: { travel_minutes_saved: 23, travel_km_saved: 4.9 },
      changes: { changes: ['時刻: 10:30 → 12:30'], unchanged: ['曜日: 月'] },
      staff_warnings: [],
      requires_patient_confirmation: false,
      within_preference: false,
      swap_counterpart: null,
    },
    cumulative_delta_minutes: 23 * seq,
    cumulative_delta_km: 4.9 * seq,
  };
}

describe('tolerantStepsArraySchema', () => {
  it('全手が有効ならそのまま通す', () => {
    const steps = [makeStep(1), makeStep(2), makeStep(3)];
    const parsed = tolerantStepsArraySchema.parse(steps);
    expect(parsed).toHaveLength(3);
    expect(parsed.map((s) => s.seq)).toEqual([1, 2, 3]);
  });

  it('未知 kind の手が現れたらそこで打ち切る (最長有効プレフィックス)', () => {
    const steps = [makeStep(1), makeStep(2, 'unknown_future_kind'), makeStep(3)];
    const parsed = tolerantStepsArraySchema.parse(steps);
    // 手順 3 は有効だが、手順 2 で切れるため含めない (累積とプレフィックス適用の整合).
    expect(parsed).toHaveLength(1);
    expect(parsed[0]!.seq).toBe(1);
  });

  it('先頭が不正なら空配列', () => {
    const parsed = tolerantStepsArraySchema.parse([{ bogus: true }, makeStep(2)]);
    expect(parsed).toHaveLength(0);
  });
});

describe('scopeOptimizationSimulateResponseSchema', () => {
  it('BE 契約どおりのレスポンスをパースできる', () => {
    const parsed = scopeOptimizationSimulateResponseSchema.parse({
      iso_year: 2026,
      iso_week: 27,
      office_id: OFFICE_ID,
      steps: [makeStep(1)],
      before: {
        visit_count: 3,
        travel_minutes: 46,
        travel_km: 9.8,
        buffer_minutes: 16,
        gap_minutes: 0,
      },
      after: {
        visit_count: 3,
        travel_minutes: 23,
        travel_km: 4.9,
        buffer_minutes: 8,
        gap_minutes: 0,
      },
      excluded_summary: {
        pinned: 1,
        locked: 0,
        no_current_visit: 2,
        dismissed: 0,
        confirmation_required_excluded: 0,
        truncated: false,
      },
      state_token: 'abc123',
    });
    expect(parsed.steps).toHaveLength(1);
    expect(parsed.before.travel_minutes).toBe(46);
    expect(parsed.excluded_summary.pinned).toBe(1);
  });

  it('excluded_summary 省略 (旧 BE) でも既定値で通す', () => {
    const parsed = scopeOptimizationSimulateResponseSchema.parse({
      iso_year: 2026,
      iso_week: 27,
      office_id: OFFICE_ID,
      steps: [],
      before: {},
      after: {},
      state_token: 'abc123',
    });
    expect(parsed.excluded_summary.truncated).toBe(false);
    expect(parsed.before.visit_count).toBe(0);
  });
});

describe('scopeOptimizationApplyRequestSchema (W2)', () => {
  it('simulate の steps をそのまま載せてパースできる', () => {
    const parsed = scopeOptimizationApplyRequestSchema.parse({
      iso_year: 2026,
      iso_week: 27,
      scope: { office_id: OFFICE_ID, weekdays: [0], course_codes: ['A'] },
      state_token: 'abc123',
      steps: [makeStep(1), makeStep(2)],
    });
    expect(parsed.steps).toHaveLength(2);
    expect(parsed.state_token).toBe('abc123');
  });

  it('steps 空は拒否 (min 1)', () => {
    const res = scopeOptimizationApplyRequestSchema.safeParse({
      iso_year: 2026,
      iso_week: 27,
      scope: { office_id: OFFICE_ID },
      state_token: 'abc123',
      steps: [],
    });
    expect(res.success).toBe(false);
  });
});

describe('scopeOptimizationApplyResponseSchema (W2)', () => {
  it('warnings は寛容パース (.catch)', () => {
    const parsed = scopeOptimizationApplyResponseSchema.parse({
      applied_count: 3,
      warnings: [123, 'ok'], // 不正混入 → catch で空配列
    });
    expect(parsed.applied_count).toBe(3);
    expect(parsed.warnings).toEqual([]);
  });
});
