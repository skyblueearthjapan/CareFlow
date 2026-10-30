/**
 * 患者の保存で weekly_pattern のキーが消えないこと
 * (docs/plans/biweekly-monthly-design-2026-10-30.md §2-4 の再発防止)。
 *
 * PATCH /patients/{id} は weekly_pattern を丸ごと置き換えるため、フォームが
 * 編集しないキー (entries・biweekly_anchor・未知のキー) を送り損ねると消える。
 * 読む (patientReadToFormValues) → 送る (prepareFormPayload → patientUpdateSchema)
 * の往復で確かめる。
 */
import { describe, it, expect } from 'vitest';

import { prepareFormPayload } from '@/lib/queries/patients';
import {
  patientReadToFormValues,
  patientUpdateSchema,
  weeklyPatternToWire,
  type PatientRead,
} from '@/lib/schemas/patient';

const ENTRIES = [{ weekday: 'Tue', time_type: '固定', preferred_start: '10:00', staff_count: 2 }];

function patient(weekly_pattern: Record<string, unknown> | null): PatientRead {
  return {
    id: '00000000-0000-0000-0000-000000000001',
    code: 'P001',
    name: 'テスト患者',
    status: 'active',
    visit_category: 'psychiatric',
    address: '千葉市稲毛区1-1',
    insurance_address: '千葉市稲毛区1-1',
    weekly_pattern,
    special_weekly_pattern: null,
  } as unknown as PatientRead;
}

/** 読んで、そのまま (または edit を当てて) 保存したときに送る weekly_pattern。 */
function roundTrip(
  raw: Record<string, unknown> | null,
  edit?: (v: ReturnType<typeof patientReadToFormValues>) => void,
): Record<string, unknown> {
  const initial = patientReadToFormValues(patient(raw));
  const values = patientReadToFormValues(patient(raw));
  edit?.(values);
  const payload = prepareFormPayload(values, initial);
  const parsed = patientUpdateSchema.parse(payload) as { weekly_pattern?: Record<string, unknown> };
  return parsed.weekly_pattern as Record<string, unknown>;
}

describe('weekly_pattern の保存の往復 (キーを消さない)', () => {
  const RAW = {
    frequency_per_week: 2,
    visit_frequency: 'biweekly',
    biweekly_anchor: '2026-11-02',
    preferred_weekdays: ['Tue', 'Fri'],
    service_minutes: 35,
    time_type: '固定',
    preferred_start: '10:00',
    preferred_end: null,
    entries: ENTRIES,
    some_future_key: { nested: true },
  };

  it('触らずに保存: 元の dict がそのまま送られる (entries・起点・未知のキー)', () => {
    const out = roundTrip(RAW);
    expect(out).toEqual(RAW);
    expect(out).not.toHaveProperty('_source');
  });

  it('別の欄を変えて保存: 変えた欄だけ変わり、ほかのキーは残る', () => {
    const out = roundTrip(RAW, (v) => {
      v.weekly_pattern = { ...v.weekly_pattern, service_minutes: 45 };
    });
    expect(out.service_minutes).toBe(45);
    expect(out.entries).toEqual(ENTRIES);
    expect(out.biweekly_anchor).toBe('2026-11-02');
    expect(out.some_future_key).toEqual({ nested: true });
  });

  it('起点の週を変えると新しい値を送る', () => {
    const out = roundTrip(RAW, (v) => {
      v.weekly_pattern = { ...v.weekly_pattern, biweekly_anchor: '2026-11-09' };
    });
    expect(out.biweekly_anchor).toBe('2026-11-09');
    expect(out.entries).toEqual(ENTRIES);
  });

  it('空だった frequency_per_week・visit_frequency に既定値を書かない', () => {
    const raw = { preferred_weekdays: ['Mon'], entries: ENTRIES };
    const out = roundTrip(raw, (v) => {
      v.weekly_pattern = { ...v.weekly_pattern, preferred_weekdays: ['Mon', 'Thu'] };
    });
    expect(out).not.toHaveProperty('frequency_per_week');
    expect(out).not.toHaveProperty('visit_frequency');
    expect(out).not.toHaveProperty('time_type');
    expect(out.preferred_weekdays).toEqual(['Mon', 'Thu']);
    expect(out.entries).toEqual(ENTRIES);
  });

  it('frequency_per_week を変えたときは書く', () => {
    const out = roundTrip({ preferred_weekdays: ['Mon'] }, (v) => {
      v.weekly_pattern = { ...v.weekly_pattern, frequency_per_week: 2 };
    });
    expect(out.frequency_per_week).toBe(2);
  });

  it('頻度を空 (null) に戻すと null を送る', () => {
    const out = roundTrip(RAW, (v) => {
      v.weekly_pattern = { ...v.weekly_pattern, visit_frequency: null };
    });
    expect(out.visit_frequency).toBeNull();
    expect(out.biweekly_anchor).toBe('2026-11-02');
  });

  it('特別週のコピーにも内部の _source は載らない', () => {
    const initial = patientReadToFormValues(patient(RAW));
    const values = { ...patientReadToFormValues(patient(RAW)), special_week: true };
    const payload = prepareFormPayload(values, initial);
    expect(payload.special_weekly_pattern).not.toHaveProperty('_source');
  });
});

describe('weeklyPatternToWire', () => {
  it('_source が無い (新規作成) ときはフォームの欄をそのまま送る', () => {
    const wp = patientReadToFormValues(patient(null)).weekly_pattern;
    const out = weeklyPatternToWire({ ...wp, _source: undefined });
    expect(out).not.toHaveProperty('_source');
    expect(out.frequency_per_week).toBe(1);
    expect(out.biweekly_anchor).toBeNull();
  });
});

describe('NG 曜日の往復 (2026-10-30・全経路で守る)', () => {
  const RAW = {
    frequency_per_week: 2,
    visit_frequency: 'biweekly',
    biweekly_anchor: '2026-11-02',
    preferred_weekdays: ['Tue', 'Fri'],
    service_minutes: 35,
    time_type: '固定',
    preferred_start: '10:00',
    entries: ENTRIES,
    some_future_key: { nested: true },
  };

  it('読んだ ng_weekdays はそのまま送り、ほかのキーも残す', () => {
    const out = roundTrip({ ...RAW, ng_weekdays: ['Sun'] });
    expect(out.ng_weekdays).toEqual(['Sun']);
    expect(out.entries).toEqual(ENTRIES);
    expect(out.some_future_key).toEqual({ nested: true });
  });

  it('NG 曜日を変えたら変えた値だけを重ねる', () => {
    const out = roundTrip({ ...RAW, ng_weekdays: ['Sun'] }, (v) => {
      v.weekly_pattern = { ...v.weekly_pattern, ng_weekdays: ['Mon', 'Sun'] };
    });
    expect(out.ng_weekdays).toEqual(['Mon', 'Sun']);
    expect(out.biweekly_anchor).toBe('2026-11-02');
    expect(out.entries).toEqual(ENTRIES);
  });

  it('NG 曜日の無い方は、開いて保存しただけでキーを足さない', () => {
    const out = roundTrip(RAW);
    expect(out).not.toHaveProperty('ng_weekdays');
  });
});
