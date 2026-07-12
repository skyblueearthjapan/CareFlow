/**
 * 新人同行による「2人目 (slot1)」充足判定の単体テスト。
 *
 * カバレッジ:
 *   - buildAccompanimentLinkIndex: visit / course / template+weekday の索引化
 *   - isVisitAccompanied: visit 直リンク / コースリンク / template+weekday / リンクなし
 *   - isSecondStaffFulfilledByAccompaniment: 全充足 / 部分充足 / 配置0件
 *   - augmentAssignedSlotsWithAccompaniment: slot1 上乗せ / 未充足は据置 / 参照同一性
 */
import { describe, it, expect } from 'vitest';

import {
  augmentAssignedSlotsWithAccompaniment,
  buildAccompanimentLinkIndex,
  isEmptyAccompanimentLinkIndex,
  isSecondStaffFulfilledByAccompaniment,
  isVisitAccompanied,
  planSecondStaffToggle,
  type FulfillmentVisit,
} from '../accompanimentFulfillment';
import type { TraineeAccompanimentItem } from '@/lib/schemas/trainee_accompaniment';
import type { SlotIndex } from '@/lib/schemas/v2/patient_fixed_visit';

const UUID = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;

function visitLink(visitId: string): TraineeAccompanimentItem {
  return {
    id: UUID(900),
    trainee_staff_id: UUID(1),
    trainee_staff_name: '新人A',
    target_type: 'visit',
    source: 'manual',
    visit: { id: visitId, date: '2026-07-13' },
  } as TraineeAccompanimentItem;
}

function courseLink(
  courseId: string | null,
  templateId: string | null,
  weekday: number,
): TraineeAccompanimentItem {
  return {
    id: UUID(901),
    trainee_staff_id: UUID(1),
    trainee_staff_name: '新人A',
    target_type: 'course',
    source: 'manual',
    course: {
      id: (courseId ?? UUID(999)) as string,
      code: 'INAGE-A',
      weekday,
      template_id: templateId,
    },
  } as TraineeAccompanimentItem;
}

function visit(over: Partial<FulfillmentVisit> = {}): FulfillmentVisit {
  return {
    id: 'v1',
    courseId: 'c1',
    courseTemplateId: 't1',
    weekday: 0,
    ...over,
  };
}

describe('buildAccompanimentLinkIndex', () => {
  it('visit / course(id) / course(template+weekday) を種別ごとに索引化する', () => {
    const idx = buildAccompanimentLinkIndex([
      visitLink('v-1'),
      courseLink('c-1', 't-1', 2),
    ]);
    expect(idx.visitIds.has('v-1')).toBe(true);
    expect(idx.courseIds.has('c-1')).toBe(true);
    expect(idx.courseTemplateWeekdays.has('t-1:2')).toBe(true);
  });

  it('null / undefined / 空配列 → 空索引', () => {
    expect(isEmptyAccompanimentLinkIndex(buildAccompanimentLinkIndex(null))).toBe(true);
    expect(isEmptyAccompanimentLinkIndex(buildAccompanimentLinkIndex(undefined))).toBe(true);
    expect(isEmptyAccompanimentLinkIndex(buildAccompanimentLinkIndex([]))).toBe(true);
  });

  it('course.id が無いリンクでも template+weekday はフォールバック索引に入る', () => {
    const idx = buildAccompanimentLinkIndex([courseLink(null, 't-9', 4)]);
    expect(idx.courseTemplateWeekdays.has('t-9:4')).toBe(true);
  });
});

describe('isVisitAccompanied', () => {
  it('visit 直リンク一致 → true', () => {
    const idx = buildAccompanimentLinkIndex([visitLink('v1')]);
    expect(isVisitAccompanied(visit({ id: 'v1' }), idx)).toBe(true);
  });

  it('コースリンク (course_id 一致) → true', () => {
    const idx = buildAccompanimentLinkIndex([courseLink('c1', 't1', 0)]);
    // course_id 一致だけで成立 (template は敢えて別値でも良い)。
    expect(isVisitAccompanied(visit({ id: 'other', courseId: 'c1' }), idx)).toBe(true);
  });

  it('course_id が無くても template+weekday 一致 → true (フォールバック)', () => {
    const idx = buildAccompanimentLinkIndex([courseLink(null, 't1', 3)]);
    expect(
      isVisitAccompanied(visit({ id: 'x', courseId: null, courseTemplateId: 't1', weekday: 3 }), idx),
    ).toBe(true);
  });

  it('リンクなし → false', () => {
    const idx = buildAccompanimentLinkIndex([visitLink('other'), courseLink('cX', 'tX', 1)]);
    expect(isVisitAccompanied(visit({ id: 'v1', courseId: 'c1', courseTemplateId: 't1', weekday: 0 }), idx)).toBe(
      false,
    );
  });

  it('weekday が null なら template フォールバックは効かない', () => {
    const idx = buildAccompanimentLinkIndex([courseLink(null, 't1', 0)]);
    expect(
      isVisitAccompanied(visit({ id: 'x', courseId: null, courseTemplateId: 't1', weekday: null }), idx),
    ).toBe(false);
  });
});

describe('isSecondStaffFulfilledByAccompaniment', () => {
  it('配置済み全訪問が同行つき → true', () => {
    const idx = buildAccompanimentLinkIndex([visitLink('v1'), courseLink('c2', 't2', 1)]);
    const placed = [
      visit({ id: 'v1', courseId: 'cX' }),
      visit({ id: 'v2', courseId: 'c2', courseTemplateId: 't2', weekday: 1 }),
    ];
    expect(isSecondStaffFulfilledByAccompaniment(placed, idx)).toBe(true);
  });

  it('一部の訪問が同行なし (部分充足) → false', () => {
    const idx = buildAccompanimentLinkIndex([visitLink('v1')]);
    const placed = [visit({ id: 'v1' }), visit({ id: 'v2', courseId: 'c9', courseTemplateId: 't9', weekday: 5 })];
    expect(isSecondStaffFulfilledByAccompaniment(placed, idx)).toBe(false);
  });

  it('配置済み訪問が 0 件 → false (充足しようがない)', () => {
    const idx = buildAccompanimentLinkIndex([visitLink('v1')]);
    expect(isSecondStaffFulfilledByAccompaniment([], idx)).toBe(false);
  });
});

describe('augmentAssignedSlotsWithAccompaniment', () => {
  const S = (...xs: SlotIndex[]) => new Set<SlotIndex>(xs);

  it('②カード条件 (has0 && !has1) + 全訪問同行つき → slot1 を上乗せ', () => {
    const base = new Map<string, Set<SlotIndex>>([['p1', S(0)]]);
    const placed = new Map<string, FulfillmentVisit[]>([
      ['p1', [visit({ id: 'v1', courseId: 'c1' })]],
    ]);
    const idx = buildAccompanimentLinkIndex([visitLink('v1')]);
    const out = augmentAssignedSlotsWithAccompaniment(base, ['p1'], placed, idx);
    expect(out.get('p1')).toEqual(S(0, 1));
    // 元マップは破壊しない。
    expect(base.get('p1')).toEqual(S(0));
  });

  it('部分充足 → 据置 (slot1 は足さない・参照も同一)', () => {
    const base = new Map<string, Set<SlotIndex>>([['p1', S(0)]]);
    const placed = new Map<string, FulfillmentVisit[]>([
      ['p1', [visit({ id: 'v1' }), visit({ id: 'v2', courseId: 'c9', courseTemplateId: 't9', weekday: 2 })]],
    ]);
    const idx = buildAccompanimentLinkIndex([visitLink('v1')]);
    const out = augmentAssignedSlotsWithAccompaniment(base, ['p1'], placed, idx);
    expect(out).toBe(base);
  });

  it('既に両 slot 埋まり (has1) の患者は触らない', () => {
    const base = new Map<string, Set<SlotIndex>>([['p1', S(0, 1)]]);
    const placed = new Map<string, FulfillmentVisit[]>([['p1', [visit({ id: 'v1' })]]]);
    const idx = buildAccompanimentLinkIndex([visitLink('v1')]);
    const out = augmentAssignedSlotsWithAccompaniment(base, ['p1'], placed, idx);
    expect(out).toBe(base);
  });

  it('空索引 (保存済みリンク無し) → base をそのまま返す', () => {
    const base = new Map<string, Set<SlotIndex>>([['p1', S(0)]]);
    const placed = new Map<string, FulfillmentVisit[]>([['p1', [visit({ id: 'v1' })]]]);
    const out = augmentAssignedSlotsWithAccompaniment(base, ['p1'], placed, buildAccompanimentLinkIndex([]));
    expect(out).toBe(base);
  });
});

describe('planSecondStaffToggle (モード中クリックのトグル計画)', () => {
  it('未選択が混在 → 全選択に倒す (未選択のみトグル)', () => {
    const selected = new Set<string>(['v1']);
    const { toggleIds, allSelected } = planSecondStaffToggle(['v1', 'v2', 'v3'], (id) =>
      selected.has(id),
    );
    expect(allSelected).toBe(false);
    expect(toggleIds).toEqual(['v2', 'v3']);
  });

  it('全選択済み → 全解除に倒す (全件トグル)', () => {
    const selected = new Set<string>(['v1', 'v2']);
    const { toggleIds, allSelected } = planSecondStaffToggle(['v1', 'v2'], (id) => selected.has(id));
    expect(allSelected).toBe(true);
    expect(toggleIds).toEqual(['v1', 'v2']);
  });

  it('対象なし → 何もトグルしない・allSelected=false', () => {
    const { toggleIds, allSelected } = planSecondStaffToggle([], () => false);
    expect(allSelected).toBe(false);
    expect(toggleIds).toEqual([]);
  });
});
