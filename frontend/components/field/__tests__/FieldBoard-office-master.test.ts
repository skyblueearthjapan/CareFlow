/**
 * FieldBoard — 拠点マスタ駆動 (0059) の順序 / 短縮のヘルパー単体テスト。
 *
 * PO決定「コードが事業所を特定しない」: 表示順 (sort_order) と短縮バッジ (short_label) は
 * offices マスタから引く。マスタ未設定 (NULL) はフォールバック
 * (sort_order NULL → 名前順末尾、short_label NULL → 名前先頭 1 文字)。
 */
import { describe, it, expect } from 'vitest';

import { officeRank, officeShort, buildCourseToken, collectDayCourses } from '../FieldBoard';
import type { BoardCell } from '@/lib/schemas/v2/board';

describe('officeRank — sort_order 駆動 + フォールバック', () => {
  it('マスタ値 (sort_order) を優先する', () => {
    const m = new Map<string, number | null>([
      ['稲毛', 1],
      ['都賀', 2],
    ]);
    expect(officeRank('稲毛', m)).toBe(1);
    expect(officeRank('都賀', m)).toBe(2);
  });

  it('sort_order NULL / 未登録は末尾 (100) に回す', () => {
    const m = new Map<string, number | null>([
      ['稲毛', 1],
      ['幕張', null],
    ]);
    expect(officeRank('幕張', m)).toBe(100); // NULL → 末尾
    expect(officeRank('未登録', m)).toBe(100); // map 外 → 末尾
  });
});

describe('officeShort — short_label 駆動 + フォールバック', () => {
  it('マスタ値 (short_label) を優先する', () => {
    const m = new Map<string, string | null>([
      ['稲毛', '稲'],
      ['都賀', '津'], // ※ 先頭 1 文字 (都) ではなくマスタ値 (津) が正
    ]);
    expect(officeShort('稲毛', m)).toBe('稲');
    expect(officeShort('都賀', m)).toBe('津');
  });

  it('short_label NULL / 未登録は名前の先頭 1 文字にフォールバックする', () => {
    const m = new Map<string, string | null>([['稲毛', null]]);
    expect(officeShort('稲毛', m)).toBe('稲'); // NULL → 先頭 1 文字
    expect(officeShort('幕張', m)).toBe('幕'); // map 外 → 先頭 1 文字
    expect(officeShort('', m)).toBeNull(); // 名前が空 → null
  });
});

describe('buildCourseToken — 拠点付きトークン (マスタ short_label)', () => {
  const m = new Map<string, string | null>([
    ['稲毛', '稲'],
    ['都賀', '津'],
  ]);

  it('裸コードに短縮名を前置する', () => {
    expect(buildCourseToken('稲毛', 'A', m)).toBe('稲A');
    expect(buildCourseToken('都賀', 'M', m)).toBe('津M');
  });

  it('既に短縮名始まりのコードは二重付与しない', () => {
    expect(buildCourseToken('稲毛', '稲A', m)).toBe('稲A');
  });

  it('short_label 未設定でも先頭 1 文字フォールバックでトークン化する', () => {
    const empty = new Map<string, string | null>();
    expect(buildCourseToken('稲毛', 'A', empty)).toBe('稲A');
  });
});

describe('collectDayCourses — sort_order 順で並べる', () => {
  function cell(officeId: string, weekday: number, codes: string[]): BoardCell {
    return {
      office_id: officeId,
      weekday,
      closed: false,
      courses: codes.map((code) => ({
        course_id: `${officeId}-${code}`,
        course_code: code,
        course_label: code,
        staff_name: null,
        capacity: { filled: 0, remaining: 6, max: 6 },
        visits: [],
      })),
    } as unknown as BoardCell;
  }

  it('sort_order の小さい拠点を先頭にする (マスタ駆動)', () => {
    const cells = [cell('o-tsuga', 0, ['A']), cell('o-inage', 0, ['A'])];
    const nameById = new Map<string, string>([
      ['o-inage', '稲毛'],
      ['o-tsuga', '都賀'],
    ]);
    const sortByName = new Map<string, number | null>([
      ['稲毛', 1],
      ['都賀', 2],
    ]);
    const out = collectDayCourses(cells, 0, nameById, sortByName);
    expect(out.map((d) => d.officeName)).toEqual(['稲毛', '都賀']);
  });

  it('sort_order NULL の拠点は名前順で末尾に回る', () => {
    const cells = [cell('o-x', 0, ['A']), cell('o-inage', 0, ['A'])];
    const nameById = new Map<string, string>([
      ['o-inage', '稲毛'],
      ['o-x', '幕張'],
    ]);
    const sortByName = new Map<string, number | null>([
      ['稲毛', 1],
      ['幕張', null],
    ]);
    const out = collectDayCourses(cells, 0, nameById, sortByName);
    expect(out.map((d) => d.officeName)).toEqual(['稲毛', '幕張']);
  });
});
