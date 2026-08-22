/**
 * reconcileMarkers — 差分項目 → ゴーストマーカーの純関数 (週空間 Phase E)。
 *
 * ① edit    : before/after 両側が出る (同日)
 * ② date_change: **before は before.date、after は after.date** を使う
 *    (date_iso を先に見ると before まで after の日付になる — レビュー M3)
 * ③ add     : after だけ / delete: before だけ
 * ④ staffIdByName で CSV 氏名 → staff_id を解決する (盤面セルの索引に必要)
 * ⑤ toMarkersByCell が before/after 両方のセルに置く
 * ⑥ resolveDayInWeek / weekdayOfIso / fmtMd
 */
import { describe, it, expect } from 'vitest';

import {
  correctionItemToMarker,
  eventChangeToMarker,
  fmtMd,
  resolveDayInWeek,
  toMarkersByCell,
  unsentEventToMarker,
  weekdayOfIso,
} from '../reconcileMarkers';
import type { CockpitCorrectionItem } from '@/lib/schemas/v2/cockpit';

const WEEK_START = '2026-08-17'; // 月
const STAFF_A = '00000000-0000-4000-8000-0000000000a1';
const STAFF_B = '00000000-0000-4000-8000-0000000000b2';
const staffIdByName = new Map([
  ['川名', STAFF_A],
  ['髙梨', STAFF_B],
]);

function item(
  action: string,
  before: Record<string, string> | null,
  after: Record<string, string> | null,
  dateIso?: string | null,
): CockpitCorrectionItem {
  return {
    id: '00000000-0000-4000-8000-00000000a001',
    sheet_id: '00000000-0000-4000-8000-00000000beef',
    patient_id: null,
    visit_id: null,
    action,
    before,
    after,
    include: true,
    comment: null,
    created_at: '',
    updated_at: '',
    date_iso: dateIso ?? null,
  } as CockpitCorrectionItem;
}

const BASE = {
  user_name: '田中 様',
  service_type: '身体介護',
};

describe('correctionItemToMarker', () => {
  it('edit は before/after 両側が同じ日で出る', () => {
    const m = correctionItemToMarker(
      item(
        'edit',
        { ...BASE, date: '18', start_time: '09:00', end_time: '10:00', staff1: '川名' },
        { ...BASE, date: '18', start_time: '09:30', end_time: '10:30', staff1: '髙梨' },
        '2026-08-18',
      ),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(m).not.toBeNull();
    expect(m!.action).toBe('update');
    expect(m!.kind).toBe('visit');
    expect(m!.patient_name).toBe('田中 様');
    expect(m!.course_label).toBe('身体介護');
    expect(m!.before).toEqual({
      staff_id: STAFF_A,
      staff_name: '川名',
      date: '2026-08-18',
      start: '09:00',
      end: '10:00',
      course_label: '身体介護',
    });
    expect(m!.after).toEqual({
      staff_id: STAFF_B,
      staff_name: '髙梨',
      date: '2026-08-18',
      start: '09:30',
      end: '10:30',
      course_label: '身体介護',
    });
    // 既存 ReconcileMarker 互換フィールド
    expect(m!.beforeStart).toBe('09:00');
    expect(m!.start).toBe('09:30');
  });

  it('date_change は before/after それぞれの日付を使う (date_iso に引きずられない)', () => {
    const m = correctionItemToMarker(
      item(
        'date_change',
        { ...BASE, date: '18', start_time: '09:00', end_time: '10:00', staff1: '川名' },
        { ...BASE, date: '20', start_time: '09:00', end_time: '10:00', staff1: '川名' },
        '2026-08-20',
      ),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(m!.action).toBe('update');
    expect(m!.before?.date).toBe('2026-08-18');
    expect(m!.after?.date).toBe('2026-08-20');
  });

  it('コースは before/after それぞれの値を持つ (差分カードで左右を比べる)', () => {
    const m = correctionItemToMarker(
      item(
        'edit',
        { ...BASE, service_type: '身体介護', date: '18', start_time: '09:00', end_time: '10:00' },
        { ...BASE, service_type: '生活援助', date: '18', start_time: '09:00', end_time: '10:00' },
        '2026-08-18',
      ),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(m!.before?.course_label).toBe('身体介護');
    expect(m!.after?.course_label).toBe('生活援助');
  });

  it('add は after だけ / delete は before だけ', () => {
    const add = correctionItemToMarker(
      item('add', null, {
        ...BASE,
        date: '19',
        start_time: '13:00',
        end_time: '14:00',
        staff1: '川名',
      }),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(add!.action).toBe('add');
    expect(add!.before).toBeUndefined();
    expect(add!.after?.date).toBe('2026-08-19');

    const del = correctionItemToMarker(
      item(
        'delete',
        { ...BASE, date: '21', start_time: '16:00', end_time: '16:45', staff1: '髙梨' },
        null,
      ),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(del!.action).toBe('delete');
    expect(del!.after).toBeUndefined();
    expect(del!.before?.date).toBe('2026-08-21');
  });

  it('氏名が引けないときは staff_id=null (氏名だけ残す)', () => {
    const m = correctionItemToMarker(
      item('add', null, {
        ...BASE,
        date: '19',
        start_time: '13:00',
        end_time: '14:00',
        staff1: '知らない人',
      }),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(m!.after?.staff_id).toBeNull();
    expect(m!.after?.staff_name).toBe('知らない人');
  });

  it('週内に解決できない日付は null (盤面に置き場が無い)', () => {
    const m = correctionItemToMarker(
      item('add', null, { ...BASE, date: '99', start_time: '13:00', end_time: '14:00' }),
      { weekStartIso: WEEK_START, staffIdByName },
    );
    expect(m).toBeNull();
  });
});

describe('toMarkersByCell', () => {
  it('before/after 両方のセルにマーカーを置く', () => {
    const m = correctionItemToMarker(
      item(
        'date_change',
        { ...BASE, date: '18', start_time: '09:00', end_time: '10:00', staff1: '川名' },
        { ...BASE, date: '20', start_time: '09:00', end_time: '10:00', staff1: '髙梨' },
        '2026-08-20',
      ),
      { weekStartIso: WEEK_START, staffIdByName },
    )!;
    const map = toMarkersByCell([m]);
    expect(map.get(`${STAFF_A}:1`)).toHaveLength(1); // 火 (before)
    expect(map.get(`${STAFF_B}:3`)).toHaveLength(1); // 木 (after)
  });

  it('staff_id が引けないマーカーはセルに置かない', () => {
    const m = correctionItemToMarker(
      item('add', null, { ...BASE, date: '19', start_time: '13:00', end_time: '14:00' }),
      { weekStartIso: WEEK_START },
    )!;
    expect(toMarkersByCell([m]).size).toBe(0);
  });
});

describe('eventChangeToMarker / unsentEventToMarker', () => {
  it('イベント update は beforeStart を before 側に写す', () => {
    const m = eventChangeToMarker({
      action: 'update',
      externalId: '111:22:2026-08-18',
      staffId: STAFF_A,
      staffName: '川名',
      date: '2026-08-18',
      start: '09:00',
      end: '09:30',
      title: '朝会',
      beforeStart: '08:30',
      beforeEnd: '09:00',
    });
    expect(m.kind).toBe('event');
    expect(m.before).toEqual({
      staff_id: STAFF_A,
      staff_name: '川名',
      date: '2026-08-18',
      start: '08:30',
      end: '09:00',
    });
    // イベントはコースを持たない
    expect(m.before?.course_label).toBeUndefined();
    expect(m.after?.start).toBe('09:00');
  });

  it('未送信イベントは kind で before/after を出し分ける', () => {
    const base = {
      id: '00000000-0000-4000-8000-0000000000e1',
      staff_id: STAFF_A,
      staff_name: '川名',
      date: '2026-08-18',
      start_time: '13:00',
      end_time: '14:00',
      title: '打合せ',
    };
    expect(unsentEventToMarker({ ...base, kind: 'add' }).before).toBeUndefined();
    expect(unsentEventToMarker({ ...base, kind: 'add' }).after?.date).toBe('2026-08-18');
    expect(unsentEventToMarker({ ...base, kind: 'delete' }).after).toBeUndefined();
    expect(unsentEventToMarker({ ...base, kind: 'delete' }).before?.date).toBe('2026-08-18');
  });
});

describe('日付ヘルパ', () => {
  it('resolveDayInWeek は週内の「日」を実日付にする', () => {
    expect(resolveDayInWeek('17', WEEK_START)).toBe('2026-08-17');
    expect(resolveDayInWeek('23', WEEK_START)).toBe('2026-08-23');
    expect(resolveDayInWeek('24', WEEK_START)).toBeNull();
    expect(resolveDayInWeek('', WEEK_START)).toBeNull();
  });

  it('月跨ぎの週でも解決できる', () => {
    // 2026-08-31(月) 〜 2026-09-06(日)
    expect(resolveDayInWeek('31', '2026-08-31')).toBe('2026-08-31');
    expect(resolveDayInWeek('2', '2026-08-31')).toBe('2026-09-02');
  });

  it('weekdayOfIso は 0=月 / fmtMd は M/d(曜)', () => {
    expect(weekdayOfIso('2026-08-17')).toBe(0);
    expect(weekdayOfIso('2026-08-22')).toBe(5);
    expect(fmtMd('2026-08-17')).toBe('8/17(月)');
  });
});
