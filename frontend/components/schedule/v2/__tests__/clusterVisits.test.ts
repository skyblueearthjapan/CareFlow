/**
 * clusterVisits — 2026-W20 後期 /schedule UX 改修 (Task E).
 *
 * 連続する同 same_address_group_id の visit を 1 つの「ペア cluster」に
 * 集約する純関数の単体テスト. WeekdayScheduleCard 側のペア囲み描画ロジックの
 * 基礎となる. (CourseWeekOverview 側は別実装だが、同じ原則 = 連続同 group_id を
 * 2 件ペア化する.)
 */
import { describe, it, expect } from 'vitest';

import {
  clusterVisits,
  type ClusterItem,
  type VisitListItem,
} from '@/components/schedule/WeekdayScheduleCard';

function v(
  key: string,
  groupId: string | null,
  overrides: Partial<VisitListItem> = {},
): VisitListItem {
  return {
    key,
    start_time: '09:00',
    patient_name: key,
    same_address_group_id: groupId,
    ...overrides,
  };
}

describe('clusterVisits', () => {
  it('空配列 → 空配列', () => {
    expect(clusterVisits([])).toEqual([]);
  });

  it('group_id なしの単独 visit → single', () => {
    const out = clusterVisits([v('a', null)]);
    expect(out).toHaveLength(1);
    expect(out[0]?.kind).toBe('single');
  });

  it('連続する同 group_id 2 件 → pair 1 つ', () => {
    const a = v('a', 'g1');
    const b = v('b', 'g1');
    const out = clusterVisits([a, b]);
    expect(out).toHaveLength(1);
    expect(out[0]).toEqual<ClusterItem>({
      kind: 'pair',
      groupId: 'g1',
      visits: [a, b],
    });
  });

  it('連続する 2 ペア (異なる group_id) → pair 2 つ', () => {
    const a = v('a', 'g1');
    const b = v('b', 'g1');
    const c = v('c', 'g2');
    const d = v('d', 'g2');
    const out = clusterVisits([a, b, c, d]);
    expect(out).toHaveLength(2);
    expect(out[0]?.kind).toBe('pair');
    expect(out[1]?.kind).toBe('pair');
    if (out[0]?.kind === 'pair') {
      expect(out[0].groupId).toBe('g1');
      expect(out[0].visits.map((x) => x.key)).toEqual(['a', 'b']);
    }
    if (out[1]?.kind === 'pair') {
      expect(out[1].groupId).toBe('g2');
      expect(out[1].visits.map((x) => x.key)).toEqual(['c', 'd']);
    }
  });

  it('単独 → ペア → 単独 が混在しても順序を維持する', () => {
    const a = v('a', null);
    const b = v('b', 'g1');
    const c = v('c', 'g1');
    const d = v('d', null);
    const out = clusterVisits([a, b, c, d]);
    expect(out).toHaveLength(3);
    expect(out[0]?.kind).toBe('single');
    expect(out[1]?.kind).toBe('pair');
    expect(out[2]?.kind).toBe('single');
  });

  it('3 名同 group_id (BE H2 漏れ) → 先頭 2 名のみペア、残りは single', () => {
    const a = v('a', 'g1');
    const b = v('b', 'g1');
    const c = v('c', 'g1');
    const out = clusterVisits([a, b, c]);
    expect(out).toHaveLength(2);
    expect(out[0]?.kind).toBe('pair');
    expect(out[1]?.kind).toBe('single');
    if (out[0]?.kind === 'pair') {
      expect(out[0].visits.map((x) => x.key)).toEqual(['a', 'b']);
    }
    if (out[1]?.kind === 'single') {
      expect(out[1].visit.key).toBe('c');
    }
  });

  it('同 group_id でも非連続 (間に別の visit) → 両方 single', () => {
    const a = v('a', 'g1');
    const b = v('b', 'g2');
    const c = v('c', 'g1');
    const out = clusterVisits([a, b, c]);
    expect(out).toHaveLength(3);
    // 全部 single (連続性が無いと pair にならない).
    expect(out.every((x) => x.kind === 'single')).toBe(true);
  });

  it('group_id が空文字列の場合は null 同様に single 扱い', () => {
    const a = v('a', '');
    const b = v('b', '');
    const out = clusterVisits([a, b]);
    expect(out).toHaveLength(2);
    expect(out[0]?.kind).toBe('single');
    expect(out[1]?.kind).toBe('single');
  });

  it('null と undefined を混ぜても single 扱い', () => {
    const a = v('a', null);
    const b: VisitListItem = {
      key: 'b',
      start_time: '09:30',
      patient_name: 'b',
      // same_address_group_id 未指定 (undefined).
    };
    const out = clusterVisits([a, b]);
    expect(out).toHaveLength(2);
    expect(out[0]?.kind).toBe('single');
    expect(out[1]?.kind).toBe('single');
  });
});
