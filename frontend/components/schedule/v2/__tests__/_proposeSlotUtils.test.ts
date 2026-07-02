/**
 * _proposeSlotUtils — propose-slots 採用ユーティリティ単体テスト.
 * 課題1: 同住所ペア枠 (is_pair) の duration_min は 90分占有でなく実サービス時間を使う.
 */
import { describe, it, expect } from 'vitest';

import {
  buildCourseTemplateIdResolver,
  existingFixedVisitToItem,
  mergeAdoptedIntoNormalFixedVisits,
  proposedSlotToFixedVisitItem,
  slotDurationMin,
} from '../_proposeSlotUtils';
import type { ProposeSlotItem } from '@/lib/schemas/v2/propose_slots';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';

const OFFICE = '11111111-1111-4111-8111-111111111111';

function slot(over: Partial<ProposeSlotItem> = {}): ProposeSlotItem {
  return {
    office_id: OFFICE,
    office_name: '稲毛',
    weekday: 1,
    weekday_code: 'Tue',
    course_code: 'C',
    course_label: '稲C',
    staff_name: null,
    start_time: '16:00:00',
    end_time: '17:30:00', // 90分 (= ペア占有)
    score: 80,
    reasons: [],
    warnings: [],
    is_pair: false,
    pair_partner: null,
    mini_schedule: [],
    ...over,
  };
}

const noResolve = () => null;

describe('proposedSlotToFixedVisitItem', () => {
  it('非ペア枠は枠の長さ (end-start) を duration_min に使う', () => {
    const item = proposedSlotToFixedVisitItem(slot({ is_pair: false }), noResolve, 35);
    expect(item.duration_min).toBe(90); // 16:00-17:30
  });

  it('同住所ペア枠 (is_pair) は実サービス時間 (serviceFallback) を使う (90分占有でない)', () => {
    const item = proposedSlotToFixedVisitItem(slot({ is_pair: true }), noResolve, 35);
    expect(item.duration_min).toBe(35);
  });

  it('course_template_id は解決できれば付与、 できなければ省略', () => {
    const resolve = (oid: string, code: string) =>
      oid === OFFICE && code === 'C' ? 'tpl-c' : null;
    expect(proposedSlotToFixedVisitItem(slot(), resolve, 35).course_template_id).toBe('tpl-c');
    expect(proposedSlotToFixedVisitItem(slot(), noResolve, 35).course_template_id).toBeUndefined();
  });
});

describe('slotDurationMin', () => {
  it('end<=start は null', () => {
    expect(slotDurationMin(slot({ start_time: '10:00', end_time: '10:00' }))).toBeNull();
  });
});

describe('mergeAdoptedIntoNormalFixedVisits', () => {
  it('他曜日/他slotは保持し、採用曜日の slot0 を置換', () => {
    const existing: PatientFixedVisitV2Read[] = [
      { weekday: 0, start_time: '09:00:00', duration_min: 30, slot_index: 0 },
      { weekday: 1, start_time: '08:00:00', duration_min: 30, slot_index: 1 },
      { weekday: 1, start_time: '08:00:00', duration_min: 30, slot_index: 0 },
    ] as unknown as PatientFixedVisitV2Read[];
    const adopted = proposedSlotToFixedVisitItem(slot({ weekday: 1 }), noResolve, 35);
    const items = mergeAdoptedIntoNormalFixedVisits(existing, [adopted]);
    expect(items.find((i) => i.weekday === 0 && i.slot_index === 0)).toBeTruthy(); // 月 保持
    expect(items.find((i) => i.weekday === 1 && i.slot_index === 1)).toBeTruthy(); // 火slot1 保持
    const tue0 = items.filter((i) => i.weekday === 1 && i.slot_index === 0);
    expect(tue0).toHaveLength(1);
    expect(tue0[0]!.start_time).toBe('16:00:00'); // 置換
  });

  it('非採用曜日の movability がマージを通して保持される (§1.3 レビューLOW)', () => {
    const existing = [
      {
        weekday: 0,
        start_time: '09:00:00',
        duration_min: 30,
        slot_index: 0,
        movability: 'time_flexible',
      },
    ] as unknown as PatientFixedVisitV2Read[];
    const adopted = proposedSlotToFixedVisitItem(slot({ weekday: 1 }), noResolve, 35);
    const items = mergeAdoptedIntoNormalFixedVisits(existing, [adopted]);
    expect(items.find((i) => i.weekday === 0)?.movability).toBe('time_flexible');
  });
});

describe('existingFixedVisitToItem (P2-A movability 運搬)', () => {
  it('movability を運搬する (§1.3 運搬の必須事項)', () => {
    const v = {
      weekday: 2,
      start_time: '10:00:00',
      duration_min: 30,
      slot_index: 0,
      movability: 'time_flexible',
    } as unknown as PatientFixedVisitV2Read;
    const item = existingFixedVisitToItem(v);
    expect(item.movability).toBe('time_flexible');
  });

  it('locked / unknown も運搬する', () => {
    const locked = existingFixedVisitToItem({
      weekday: 0,
      start_time: '09:00:00',
      duration_min: 30,
      slot_index: 0,
      movability: 'locked',
    } as unknown as PatientFixedVisitV2Read);
    expect(locked.movability).toBe('locked');

    const unknown = existingFixedVisitToItem({
      weekday: 1,
      start_time: '09:00:00',
      duration_min: 30,
      slot_index: 0,
      movability: 'unknown',
    } as unknown as PatientFixedVisitV2Read);
    expect(unknown.movability).toBe('unknown');
  });
});

describe('buildCourseTemplateIdResolver', () => {
  it('(office, label大文字) で解決、 deleted_at は除外', () => {
    const resolve = buildCourseTemplateIdResolver([
      [
        { id: 'a', office_id: OFFICE, label: 'C', deleted_at: null },
        { id: 'b', office_id: OFFICE, label: 'D', deleted_at: '2026-01-01' },
      ] as never,
    ]);
    expect(resolve(OFFICE, 'c')).toBe('a');
    expect(resolve(OFFICE, 'D')).toBeNull(); // deleted
  });
});
