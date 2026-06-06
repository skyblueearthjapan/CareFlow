/**
 * schedulingSettings zod schemas 単体テスト — Phase G-88 Step4。
 *
 * GET/PUT レスポンス ({ values, is_default }) の parse と、PUT payload
 * (部分更新 / null=既定リセット) の型検証を固定する。
 */
import { describe, it, expect } from 'vitest';

import {
  schedulingSettingsResponseSchema,
  schedulingSettingsUpdateSchema,
  timeStringSchema,
  SCHEDULING_SETTINGS_RESET_ALL,
  SCHEDULING_RANGES,
} from '../schedulingSettings';

const validResponse = {
  values: {
    visit_buffer_min: 8,
    travel_speed_kmh: 20,
    lunch_duration_min: 60,
    lunch_window_start: '11:30:00',
    lunch_window_end: '13:30:00',
    business_start: '09:30:00',
    business_end: '18:00:00',
    max_patients_per_course: 6,
  },
  is_default: {
    visit_buffer_min: true,
    travel_speed_kmh: true,
    lunch_duration_min: true,
    lunch_window_start: true,
    lunch_window_end: true,
    business_start: true,
    business_end: true,
    max_patients_per_course: true,
  },
};

describe('schedulingSettingsResponseSchema', () => {
  it('正しい GET/PUT レスポンスを parse できる', () => {
    const r = schedulingSettingsResponseSchema.parse(validResponse);
    expect(r.values.visit_buffer_min).toBe(8);
    expect(r.is_default.travel_speed_kmh).toBe(true);
  });

  it('travel_speed_kmh は float も許容する', () => {
    const r = schedulingSettingsResponseSchema.parse({
      ...validResponse,
      values: { ...validResponse.values, travel_speed_kmh: 22.5 },
    });
    expect(r.values.travel_speed_kmh).toBe(22.5);
  });

  it('時刻が "HH:MM:SS" 以外なら parse 失敗', () => {
    const bad = {
      ...validResponse,
      values: { ...validResponse.values, business_start: '09:30' },
    };
    expect(schedulingSettingsResponseSchema.safeParse(bad).success).toBe(false);
  });

  it('values の項目欠落は parse 失敗', () => {
    const rest = { ...validResponse.values } as Partial<typeof validResponse.values>;
    delete rest.visit_buffer_min;
    const bad = { ...validResponse, values: rest };
    expect(schedulingSettingsResponseSchema.safeParse(bad).success).toBe(false);
  });
});

describe('timeStringSchema', () => {
  it.each(['00:00:00', '09:30:00', '23:59:59'])('%s は valid', (t) => {
    expect(timeStringSchema.safeParse(t).success).toBe(true);
  });

  it.each(['9:30:00', '24:00:00', '09:60:00', '09:30'])('%s は invalid', (t) => {
    expect(timeStringSchema.safeParse(t).success).toBe(false);
  });
});

describe('schedulingSettingsUpdateSchema (部分更新)', () => {
  it('空オブジェクト (全項目 omit = 不変) は valid', () => {
    expect(schedulingSettingsUpdateSchema.safeParse({}).success).toBe(true);
  });

  it('一部フィールドのみの更新は valid', () => {
    const r = schedulingSettingsUpdateSchema.parse({ visit_buffer_min: 10 });
    expect(r.visit_buffer_min).toBe(10);
  });

  it('明示 null (= 既定リセット) は valid', () => {
    const r = schedulingSettingsUpdateSchema.parse({ travel_speed_kmh: null });
    expect(r.travel_speed_kmh).toBeNull();
  });

  it('RESET_ALL は全 8 項目を null にする', () => {
    const r = schedulingSettingsUpdateSchema.parse(SCHEDULING_SETTINGS_RESET_ALL);
    expect(Object.values(r).every((x) => x === null)).toBe(true);
    expect(Object.keys(r)).toHaveLength(8);
  });
});

describe('SCHEDULING_RANGES (BE 契約と一致)', () => {
  it('範囲が仕様どおり', () => {
    expect(SCHEDULING_RANGES.visit_buffer_min).toMatchObject({ min: 0, max: 30 });
    expect(SCHEDULING_RANGES.travel_speed_kmh).toMatchObject({ min: 10, max: 40 });
    expect(SCHEDULING_RANGES.lunch_duration_min).toMatchObject({ min: 30, max: 90, step: 5 });
    expect(SCHEDULING_RANGES.max_patients_per_course).toMatchObject({ min: 1, max: 10 });
  });
});
