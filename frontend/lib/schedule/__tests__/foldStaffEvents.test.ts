/**
 * foldStaffEvents — 職員イベントの二重を表示側で 1 件に畳む
 * (mobile-staff-schedule-design-2026-09-16.md §3 C-2)。
 *
 * 現場の朝会が `manual` と `kaipoke` で二重に入っている (調査 §8) のを、
 * スマホで 1 件に見せるための純関数。
 */
import { describe, it, expect } from 'vitest';

import { foldStaffEvents, type FoldableStaffEvent } from '../foldStaffEvents';

interface TestEvent extends FoldableStaffEvent {
  id: string;
  cancelled_at?: string | null;
}

function ev(over: Partial<TestEvent> & { id: string }): TestEvent {
  return {
    staff_id: 'staff-1',
    date: '2026-09-16',
    start_time: '08:30',
    title: '朝会',
    source: 'manual',
    ...over,
  };
}

describe('foldStaffEvents', () => {
  it('同一スタッフ・同一開始時刻・同一タイトルなら 1 件に畳む', () => {
    const out = foldStaffEvents([ev({ id: 'a' }), ev({ id: 'b' })]);
    expect(out).toHaveLength(1);
  });

  it('kaipoke > manual > fixed の順に残す (並び順に依らない)', () => {
    expect(
      foldStaffEvents([
        ev({ id: 'fixed', source: 'fixed' }),
        ev({ id: 'manual', source: 'manual' }),
        ev({ id: 'kaipoke', source: 'kaipoke' }),
      ])[0]!.id,
    ).toBe('kaipoke');

    expect(
      foldStaffEvents([
        ev({ id: 'kaipoke', source: 'kaipoke' }),
        ev({ id: 'manual', source: 'manual' }),
        ev({ id: 'fixed', source: 'fixed' }),
      ])[0]!.id,
    ).toBe('kaipoke');

    expect(
      foldStaffEvents([ev({ id: 'fixed', source: 'fixed' }), ev({ id: 'manual' })])[0]!.id,
    ).toBe('manual');
  });

  it('タイトルは trim して比べる (前後の空白は同一視)', () => {
    const out = foldStaffEvents([ev({ id: 'a', title: ' 朝会 ' }), ev({ id: 'b' })]);
    expect(out).toHaveLength(1);
  });

  it('スタッフ / 日付 / 開始時刻 / タイトルのどれかが違えば畳まない', () => {
    expect(foldStaffEvents([ev({ id: 'a' }), ev({ id: 'b', staff_id: 'staff-2' })])).toHaveLength(
      2,
    );
    expect(foldStaffEvents([ev({ id: 'a' }), ev({ id: 'b', date: '2026-09-17' })])).toHaveLength(2);
    expect(foldStaffEvents([ev({ id: 'a' }), ev({ id: 'b', start_time: '09:00' })])).toHaveLength(
      2,
    );
    expect(foldStaffEvents([ev({ id: 'a' }), ev({ id: 'b', title: '研修' })])).toHaveLength(2);
  });

  it('生きている行を優先する (取消済みの複製が生きた行を隠さない)', () => {
    // カイポケ側が有効なら、manual が取消でもカイポケが残る (出所でも上位)。
    const kept = foldStaffEvents([
      ev({ id: 'manual', source: 'manual', cancelled_at: '2026-09-15T00:00:00Z' }),
      ev({ id: 'kaipoke', source: 'kaipoke', cancelled_at: null }),
    ])[0]!;
    expect(kept.id).toBe('kaipoke');
    expect(kept.cancelled_at).toBeNull();

    // cancelled kaipoke × live manual → 生きている manual が残る
    // (出所より生死が上位・2026-09-16 MEDIUM-8)。
    const live = foldStaffEvents([
      ev({ id: 'manual', source: 'manual', cancelled_at: null }),
      ev({ id: 'kaipoke', source: 'kaipoke', cancelled_at: '2026-09-15T00:00:00Z' }),
    ])[0]!;
    expect(live.id).toBe('manual');
    expect(live.cancelled_at).toBeNull();

    // 並び順に依らない (取消済みが先でも同じ)。
    expect(
      foldStaffEvents([
        ev({ id: 'kaipoke', source: 'kaipoke', cancelled_at: '2026-09-15T00:00:00Z' }),
        ev({ id: 'manual', source: 'manual', cancelled_at: null }),
      ])[0]!.id,
    ).toBe('manual');
  });

  it('両方が取消なら出所の順位で決まり、cancelled_at は残した行の値', () => {
    const kept = foldStaffEvents([
      ev({ id: 'manual', source: 'manual', cancelled_at: '2026-09-14T00:00:00Z' }),
      ev({ id: 'kaipoke', source: 'kaipoke', cancelled_at: '2026-09-15T00:00:00Z' }),
    ])[0]!;
    expect(kept.id).toBe('kaipoke');
    expect(kept.cancelled_at).toBe('2026-09-15T00:00:00Z');
  });

  it('同順位なら先に来た行を残し、入力順は保たれる', () => {
    const out = foldStaffEvents([
      ev({ id: 'first' }),
      ev({ id: 'other', start_time: '10:00' }),
      ev({ id: 'dup' }),
    ]);
    expect(out.map((e) => e.id)).toEqual(['first', 'other']);
  });

  it('未知 / 未指定の source は最下位 (既知の出所が勝つ)', () => {
    expect(
      foldStaffEvents([
        ev({ id: 'unknown', source: 'zzz' }),
        ev({ id: 'fixed', source: 'fixed' }),
      ])[0]!.id,
    ).toBe('fixed');
    expect(
      foldStaffEvents([
        ev({ id: 'none', source: undefined }),
        ev({ id: 'fixed', source: 'fixed' }),
      ])[0]!.id,
    ).toBe('fixed');
  });

  it('空配列はそのまま空', () => {
    expect(foldStaffEvents([])).toEqual([]);
  });
});
