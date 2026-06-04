/**
 * Phase G-84 空き枠への直接配置 — 純関数 単体テスト.
 *
 * computePlacementWarnings の赤/黄境界 + buildPlacementMeta /
 * buildPatientVisitAddPayload / buildPatientCreatePlacementPayload の
 * payload 構造を検証する。
 */
import { describe, it, expect } from 'vitest';

import {
  computePlacementWarnings,
  buildPlacementMeta,
  buildPatientVisitAddPayload,
  buildPatientCreatePlacementPayload,
  type SlotPlacementContext,
  type PlacementExtras,
  type KarteInput,
  type DesiredSchedule,
  type ProposedVisit,
} from '@/lib/field/patientCreate';

function ctx(over: Partial<SlotPlacementContext> = {}): SlotPlacementContext {
  return {
    officeId: 'office-1',
    officeName: '稲毛',
    courseId: 'course-1',
    courseCode: '稲A',
    courseLabel: '稲A',
    weekday: 0,
    gapStartMin: 10 * 60, // 10:00
    gapEndMin: 12 * 60, // 12:00
    prevVisit: { patientId: 'p-prev', patientName: '前 太郎', endTime: '09:45' },
    nextVisit: { patientName: '後 花子', startTime: '12:00' },
    capacityRemaining: 3,
    existingVisits: [{ startTime: '09:00', endTime: '09:45' }],
    ...over,
  };
}

describe('computePlacementWarnings', () => {
  it('警告なしの基本ケースは空配列', () => {
    const w = computePlacementWarnings({ ctx: ctx(), startTime: '10:30', durationMin: 35 });
    expect(w).toEqual([]);
  });

  it('🔴 capacity_full: capacityRemaining<=0 で赤', () => {
    const w = computePlacementWarnings({
      ctx: ctx({ capacityRemaining: 0 }),
      startTime: '10:30',
      durationMin: 35,
    });
    expect(w.some((x) => x.level === 'red' && x.code === 'capacity_full')).toBe(true);
  });

  it('🔴 time_overlap: 既存 visit と重なると赤 (端点接触は重複としない)', () => {
    // 既存 10:00-10:45 と重なる配置 10:30。
    const over = computePlacementWarnings({
      ctx: ctx({ existingVisits: [{ startTime: '10:00', endTime: '10:45' }] }),
      startTime: '10:30',
      durationMin: 35,
    });
    expect(over.some((x) => x.code === 'time_overlap')).toBe(true);

    // 端点接触 (既存 10:00-10:30, 配置 10:30-) は重複としない。
    const touch = computePlacementWarnings({
      ctx: ctx({ existingVisits: [{ startTime: '10:00', endTime: '10:30' }] }),
      startTime: '10:30',
      durationMin: 35,
    });
    expect(touch.some((x) => x.code === 'time_overlap')).toBe(false);
  });

  it('🟡 travel_shortage: start < prevEnd + travel で黄、満たせば出ない (境界)', () => {
    // prevEnd 09:45 + travel 30 = 10:15。10:10 開始は不足。
    const shortage = computePlacementWarnings({
      ctx: ctx(),
      startTime: '10:10',
      durationMin: 35,
      travelTotalMin: 30,
    });
    expect(shortage.some((x) => x.code === 'travel_shortage')).toBe(true);

    // 10:15 開始ちょうどは不足ではない (< のみ判定)。
    const ok = computePlacementWarnings({
      ctx: ctx(),
      startTime: '10:15',
      durationMin: 35,
      travelTotalMin: 30,
    });
    expect(ok.some((x) => x.code === 'travel_shortage')).toBe(false);
  });

  it('travelTotalMin 未指定 / prevVisit 無しでは travel_shortage を判定しない', () => {
    expect(
      computePlacementWarnings({ ctx: ctx(), startTime: '10:00', durationMin: 35 }).some(
        (x) => x.code === 'travel_shortage',
      ),
    ).toBe(false);
    expect(
      computePlacementWarnings({
        ctx: ctx({ prevVisit: null }),
        startTime: '10:00',
        durationMin: 35,
        travelTotalMin: 60,
      }).some((x) => x.code === 'travel_shortage'),
    ).toBe(false);
  });

  it('🟡 sex_restriction / multi_staff を黄で出す', () => {
    const w = computePlacementWarnings({
      ctx: ctx(),
      startTime: '10:30',
      durationMin: 35,
      sexRestriction: 'female_only',
      requiresMultipleStaff: true,
    });
    expect(w.some((x) => x.code === 'sex_restriction')).toBe(true);
    expect(w.some((x) => x.code === 'multi_staff')).toBe(true);
    expect(w.every((x) => x.level === 'yellow')).toBe(true);
  });

  it('🟡 outside_hours: 営業枠外 / 昼休み跨ぎを黄で出す', () => {
    // 11:50 開始 + 35分 = 12:25 は AM 枠 (〜12:00) を超え昼休みに跨ぐ。
    const w = computePlacementWarnings({
      ctx: ctx({ gapEndMin: 13 * 60 }),
      startTime: '11:50',
      durationMin: 35,
    });
    expect(w.some((x) => x.code === 'outside_hours')).toBe(true);
  });

  it('赤 → 黄 の順で返す (UI で赤帯を上に出す)', () => {
    const w = computePlacementWarnings({
      ctx: ctx({ capacityRemaining: 0 }),
      startTime: '10:30',
      durationMin: 35,
      requiresMultipleStaff: true,
    });
    expect(w[0]!.level).toBe('red');
    expect(w[w.length - 1]!.level).toBe('yellow');
  });
});

describe('buildPlacementMeta', () => {
  it('枠座標 + prev/next を placement オブジェクトにまとめる', () => {
    const meta = buildPlacementMeta(ctx(), '10:30', 35);
    expect(meta).toEqual({
      office_id: 'office-1',
      office_name: '稲毛',
      // board は週次 Course.id しか返さず CourseTemplate.id を解決できないため常に null。
      // コース解決は proposed_visits の course_code トークンで行う (ctx.courseId は載せない)。
      course_template_id: null,
      course_code: '稲A',
      course_label: '稲A',
      weekday: 0,
      start_time: '10:30',
      duration_min: 35,
      prev_visit: { patient_name: '前 太郎', end_time: '09:45' },
      next_visit: { patient_name: '後 花子', start_time: '12:00' },
    });
  });

  it('ctx.courseId (週次 Course.id) を course_template_id に投入しない', () => {
    const meta = buildPlacementMeta(ctx({ courseId: 'weekly-course-999' }), '10:30', 35);
    expect(meta.course_template_id).toBeNull();
  });

  it('prev/next 無しは null', () => {
    const meta = buildPlacementMeta(ctx({ prevVisit: null, nextVisit: null }), '10:30', 35);
    expect(meta.prev_visit).toBeNull();
    expect(meta.next_visit).toBeNull();
  });
});

const extras = (over: Partial<PlacementExtras> = {}): PlacementExtras => ({
  placement: buildPlacementMeta(ctx(), '10:30', 35),
  warnings: [],
  overrideReason: null,
  ...over,
});

const visit: ProposedVisit = {
  weekday: 0,
  start_time: '10:30',
  duration_min: 35,
  course_code: '稲A',
};

describe('buildPatientVisitAddPayload', () => {
  it('既存患者 + 1 枠 + placement/warnings/override_reason を含む', () => {
    const p = buildPatientVisitAddPayload('pid-1', '田中 太郎', visit, extras());
    expect(p.patient_id).toBe('pid-1');
    expect(p.patient_name).toBe('田中 太郎');
    expect(p.proposed_visits).toEqual([visit]);
    expect(p.placement).toBeTruthy();
    expect(p.warnings).toEqual([]);
    expect(p.override_reason).toBeNull();
  });

  it('赤警告の強行理由を override_reason に載せる (trim)', () => {
    const p = buildPatientVisitAddPayload(
      'pid-1',
      '田中 太郎',
      visit,
      extras({
        warnings: [{ level: 'red', code: 'capacity_full', message: '満員' }],
        overrideReason: '  急患対応のため  ',
      }),
    );
    expect(p.override_reason).toBe('急患対応のため');
    expect((p.warnings as unknown[]).length).toBe(1);
  });
});

describe('buildPatientCreatePlacementPayload', () => {
  const karte: KarteInput = {
    name: '青柳 あい',
    code: 'P-1042',
    kana: '',
    sex: '',
    insurance: '',
    address: '千葉市稲毛区小仲台6-2-1',
    lat: null,
    lng: null,
    sex_restriction: '',
    requires_multiple_staff: false,
    primary_office_id: 'office-1',
  };
  const schedule: DesiredSchedule = {
    frequency_per_week: 1,
    visit_frequency: 'every',
    preferred_weekdays: ['Mon'],
    service_minutes: 35,
    time_type: '固定',
    preferred_start: '10:30',
    preferred_end: null,
  };

  it('patient_create payload に placement/warnings/override_reason を足す', () => {
    const p = buildPatientCreatePlacementPayload(karte, schedule, [visit], extras());
    expect(p.code).toBe('P-1042');
    expect(p.name).toBe('青柳 あい');
    expect(p.proposed_visits).toEqual([visit]);
    expect(p.placement).toBeTruthy();
    expect(p.warnings).toEqual([]);
    expect('override_reason' in p).toBe(true);
    expect(p.primary_office_id).toBe('office-1');
  });
});
