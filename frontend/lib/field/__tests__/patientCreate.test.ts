/**
 * patientCreate 純関数 (StageB) — 単体テスト.
 *
 * buildProposedVisits / buildPatientCreatePayload / slotDurationMin の
 * payload 構造・proposed_visits 生成を検証する。
 */
import { describe, it, expect } from 'vitest';

import {
  buildProposedVisits,
  buildPatientCreatePayload,
  slotDurationMin,
  type KarteInput,
  type DesiredSchedule,
} from '@/lib/field/patientCreate';
import type { ProposeSlotItem } from '@/lib/schemas/v2/propose_slots';
import type { WeekdayKey } from '@/lib/schemas/patient';

function slot(over: Partial<ProposeSlotItem>): ProposeSlotItem {
  return {
    office_id: '11111111-1111-1111-1111-111111111111',
    office_name: '稲毛',
    weekday: 0,
    weekday_code: 'Mon',
    course_code: 'A',
    course_label: '稲A',
    staff_name: null,
    start_time: '10:00',
    end_time: '11:00',
    score: 1,
    reasons: [],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    ...over,
  } as ProposeSlotItem;
}

describe('slotDurationMin', () => {
  it('start〜end の差分を分で返す', () => {
    expect(slotDurationMin(slot({ start_time: '10:00', end_time: '11:00' }))).toBe(60);
    expect(slotDurationMin(slot({ start_time: '09:15', end_time: '10:00' }))).toBe(45);
  });
  it('パース不能 / 逆転は null', () => {
    expect(slotDurationMin(slot({ start_time: '11:00', end_time: '10:00' }))).toBeNull();
    expect(slotDurationMin(slot({ start_time: 'x', end_time: 'y' }))).toBeNull();
  });
});

describe('buildProposedVisits', () => {
  it('曜日ごとに 1 枠を weekday 昇順で積む / duration は枠から算出', () => {
    const adopted = new Map<WeekdayKey, ProposeSlotItem>();
    adopted.set(
      'Fri' as WeekdayKey,
      slot({
        weekday: 4,
        weekday_code: 'Fri',
        start_time: '14:00',
        end_time: '15:00',
        course_code: 'B',
      }),
    );
    adopted.set(
      'Mon' as WeekdayKey,
      slot({
        weekday: 0,
        weekday_code: 'Mon',
        start_time: '10:00',
        end_time: '11:00',
        course_code: 'A',
      }),
    );
    const pv = buildProposedVisits(adopted, 35);
    expect(pv).toHaveLength(2);
    // weekday 昇順 (Mon=0 → Fri=4)。
    expect(pv[0]).toEqual({ weekday: 0, start_time: '10:00', duration_min: 60, course_code: 'A' });
    expect(pv[1]).toEqual({ weekday: 4, start_time: '14:00', duration_min: 60, course_code: 'B' });
  });

  it('duration が算出できない枠は fallbackMinutes を使う', () => {
    const adopted = new Map<WeekdayKey, ProposeSlotItem>();
    adopted.set(
      'Mon' as WeekdayKey,
      slot({ weekday: 0, weekday_code: 'Mon', start_time: '10:00', end_time: '' }),
    );
    const pv = buildProposedVisits(adopted, 45);
    expect(pv[0]!.duration_min).toBe(45);
  });
});

describe('buildPatientCreatePayload', () => {
  const karte: KarteInput = {
    name: '青柳 あい',
    code: 'P-1042',
    kana: 'アオヤギ アイ',
    sex: 'female',
    insurance: 'medical',
    address: '千葉市稲毛区小仲台6-2-1',
    lat: 35.63,
    lng: 140.11,
    sex_restriction: 'female_only',
    requires_multiple_staff: true,
  };
  const schedule: DesiredSchedule = {
    frequency_per_week: 2,
    visit_frequency: 'every',
    preferred_weekdays: ['Mon', 'Fri'] as WeekdayKey[],
    service_minutes: 60,
    time_type: '時間帯',
    preferred_start: '10:00',
    preferred_end: '11:00',
  };

  it('カルテ項目 + weekly_pattern + proposed_visits を含む payload を作る', () => {
    const pv = [{ weekday: 0, start_time: '10:00', duration_min: 60, course_code: 'A' }];
    const p = buildPatientCreatePayload(karte, schedule, pv);
    expect(p.code).toBe('P-1042');
    expect(p.name).toBe('青柳 あい');
    expect(p.patient_name).toBe('青柳 あい'); // ApprovePanel ヘッドライン用。
    expect(p.kana).toBe('アオヤギ アイ');
    expect(p.sex).toBe('female');
    expect(p.insurance).toBe('medical');
    expect(p.address).toBe('千葉市稲毛区小仲台6-2-1');
    expect(p.lat).toBe(35.63);
    expect(p.lng).toBe(140.11);
    expect(p.sex_restriction).toBe('female_only');
    expect(p.requires_multiple_staff).toBe(true);
    expect(p.status).toBe('active');
    expect(p.proposed_visits).toBe(pv);

    const wp = p.weekly_pattern as Record<string, unknown>;
    expect(wp.frequency_per_week).toBe(2);
    expect(wp.visit_frequency).toBe('every');
    expect(wp.preferred_weekdays).toEqual(['Mon', 'Fri']);
    expect(wp.service_minutes).toBe(60);
    expect(wp.time_type).toBe('時間帯');
    expect(wp.preferred_start).toBe('10:00');
    expect(wp.preferred_end).toBe('11:00');
  });

  it('空の任意項目は payload から省略する', () => {
    const minimal: KarteInput = {
      name: '田中 太郎',
      code: 'P-9',
      kana: '',
      sex: '',
      insurance: '',
      address: '',
      lat: null,
      lng: null,
      sex_restriction: '',
      requires_multiple_staff: false,
    };
    const p = buildPatientCreatePayload(minimal, schedule, []);
    expect(p.name).toBe('田中 太郎');
    expect('kana' in p).toBe(false);
    expect('sex' in p).toBe(false);
    expect('insurance' in p).toBe(false);
    expect('address' in p).toBe(false);
    expect('lat' in p).toBe(false);
    expect('lng' in p).toBe(false);
    expect('sex_restriction' in p).toBe(false);
  });
});
