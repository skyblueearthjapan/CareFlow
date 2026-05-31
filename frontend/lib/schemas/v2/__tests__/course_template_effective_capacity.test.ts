/**
 * effectiveCapacity / courseCodeIndex — スタッフ数連動のコース「休」/定員判定.
 *
 * 週ビューのコース表示を auto-schedule (auto_allocator_v2 Stage 4) と統一する:
 *   A-E コース: courseCodeIndex(A=0..E=4) < min(staffCount, codesMax) なら開講 (定員6),
 *              それ以外は「休」(定員0).
 *   M系 (M, M2..): 静的 capacity_<曜日> を使う (staffCount に依らない).
 */
import { describe, it, expect } from 'vitest';

import {
  COURSE_CODES_MAX,
  courseCodeIndex,
  effectiveCapacity,
  type CourseTemplateRead,
} from '../course_template';

function makeTpl(label: string, caps: Partial<CourseTemplateRead> = {}): CourseTemplateRead {
  return {
    id: `tpl-${label}`,
    office_id: 'o1',
    label,
    capacity_mon: 4,
    capacity_tue: 4,
    capacity_wed: 4,
    capacity_thu: 4,
    capacity_fri: 4,
    capacity_sat: 4,
    capacity_sun: 0,
    notes: null,
    created_at: '',
    updated_at: '',
    deleted_at: null,
    ...caps,
  } as CourseTemplateRead;
}

describe('courseCodeIndex', () => {
  it('A-E は index 0-4 を返す', () => {
    expect(courseCodeIndex('A')).toBe(0);
    expect(courseCodeIndex('B')).toBe(1);
    expect(courseCodeIndex('C')).toBe(2);
    expect(courseCodeIndex('D')).toBe(3);
    expect(courseCodeIndex('E')).toBe(4);
  });

  it('小文字 / 前後空白も正規化する', () => {
    expect(courseCodeIndex(' d ')).toBe(3);
    expect(courseCodeIndex('e')).toBe(4);
  });

  it('legacy 多文字ラベル (Aコース) は先頭文字で判定', () => {
    expect(courseCodeIndex('Aコース')).toBe(0);
  });

  it('M / M2 / 空 / null は null (A-E 対象外)', () => {
    expect(courseCodeIndex('M')).toBeNull();
    expect(courseCodeIndex('M2')).toBeNull();
    expect(courseCodeIndex('')).toBeNull();
    expect(courseCodeIndex(null)).toBeNull();
    expect(courseCodeIndex(undefined)).toBeNull();
    expect(courseCodeIndex('F')).toBeNull();
  });
});

describe('effectiveCapacity (A-E スタッフ数連動)', () => {
  it('4 名出勤 → D 開講 (定員6), 3 名 → D 休 (定員0)', () => {
    const tplD = makeTpl('D');
    // index(D)=3. min(4,5)=4, 3<4 → 開講.
    expect(effectiveCapacity(tplD, 0, 4)).toBe(6);
    // min(3,5)=3, 3<3=false → 休.
    expect(effectiveCapacity(tplD, 0, 3)).toBe(0);
  });

  it('A-C は 3 名でも開講, E は 4 名でも休 / 5 名で開講', () => {
    expect(effectiveCapacity(makeTpl('A'), 0, 3)).toBe(6);
    expect(effectiveCapacity(makeTpl('B'), 0, 3)).toBe(6);
    expect(effectiveCapacity(makeTpl('C'), 0, 3)).toBe(6);
    expect(effectiveCapacity(makeTpl('E'), 0, 4)).toBe(0);
    expect(effectiveCapacity(makeTpl('E'), 0, 5)).toBe(6);
  });

  it('codesMax で A-E 上限を絞る (6 名でも E まで)', () => {
    // staffCount=6 でも min(6, COURSE_CODES_MAX=5)=5 → E (index4<5) 開講.
    expect(effectiveCapacity(makeTpl('E'), 0, 6, COURSE_CODES_MAX)).toBe(6);
    // codesMax=4 を渡すと E (index4<4=false) は休.
    expect(effectiveCapacity(makeTpl('E'), 0, 6, 4)).toBe(0);
  });

  it('staffCount=0 なら A-E は全て休', () => {
    for (const label of ['A', 'B', 'C', 'D', 'E']) {
      expect(effectiveCapacity(makeTpl(label), 0, 0)).toBe(0);
    }
  });

  it('A-E は静的 capacity を無視する (capacity_mon=0 でも staff 十分なら開講)', () => {
    const tplA0 = makeTpl('A', { capacity_mon: 0 });
    expect(effectiveCapacity(tplA0, 0, 5)).toBe(6);
  });
});

describe('effectiveCapacity (M系は静的 capacity)', () => {
  it('M は staffCount に依らず静的 capacity_<曜日> を返す', () => {
    const tplM = makeTpl('M', { capacity_mon: 4, capacity_sat: 0 });
    // staffCount=0 でも capacity_mon=4 → 4.
    expect(effectiveCapacity(tplM, 0, 0)).toBe(4);
    // 土曜は capacity_sat=0 → 休 (staffCount=5 でも).
    expect(effectiveCapacity(tplM, 5, 5)).toBe(0);
  });

  it('M2 も静的扱い', () => {
    const tplM2 = makeTpl('M2', { capacity_tue: 6 });
    expect(effectiveCapacity(tplM2, 1, 0)).toBe(6);
  });
});
