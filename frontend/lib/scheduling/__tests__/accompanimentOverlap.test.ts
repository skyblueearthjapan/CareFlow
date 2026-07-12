/**
 * 新人同行の時間重複判定 (§7.1) の単体テスト。
 */
import { describe, it, expect } from 'vitest';

import {
  computeAccompanimentOverlaps,
  type AccompanimentOverlapEntry,
} from '../accompanimentOverlap';

function entry(over: Partial<AccompanimentOverlapEntry>): AccompanimentOverlapEntry {
  return {
    visitId: 'v1',
    dayKey: 0,
    dayLabel: '7/14(月)',
    startMin: 600, // 10:00
    endMin: 660, // 11:00
    patientName: '山田',
    courseLabel: '稲毛A',
    ...over,
  };
}

describe('computeAccompanimentOverlaps', () => {
  it('重複なし → 空', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 660 }),
      entry({ visitId: 'b', startMin: 660, endMin: 720 }), // 端点接触=重複としない
    ]);
    expect(res.overlapVisitIds.size).toBe(0);
    expect(res.messages).toHaveLength(0);
  });

  it('同住所ペア (sameAddressKey 一致) は同時刻でも重複としない (90分占有ルール)', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 660, sameAddressKey: '35.600:140.100' }),
      entry({ visitId: 'b', startMin: 600, endMin: 660, sameAddressKey: '35.600:140.100' }),
    ]);
    expect(res.overlapVisitIds.size).toBe(0);
    expect(res.messages).toHaveLength(0);
  });

  it('座標キーが片方 null なら免除しない (保守的にブロック)', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 660, sameAddressKey: null }),
      entry({ visitId: 'b', startMin: 600, endMin: 660, sameAddressKey: '35.600:140.100' }),
    ]);
    expect(res.overlapVisitIds.size).toBe(2);
  });

  it('座標キーが異なれば従来どおり重複', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 660, sameAddressKey: '35.600:140.100' }),
      entry({ visitId: 'b', startMin: 600, endMin: 660, sameAddressKey: '35.700:140.200' }),
    ]);
    expect(res.overlapVisitIds.size).toBe(2);
  });

  it('同一日で時間帯が交差 → 両方を重複として検出', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 660, patientName: '山田', courseLabel: '稲毛A' }),
      entry({ visitId: 'b', startMin: 630, endMin: 690, patientName: '佐藤', courseLabel: '稲毛C' }),
    ]);
    expect(res.overlapVisitIds.has('a')).toBe(true);
    expect(res.overlapVisitIds.has('b')).toBe(true);
    expect(res.messages).toHaveLength(1);
    expect(res.messages[0]).toContain('7/14(月)');
    expect(res.messages[0]).toContain('山田（稲毛A）');
    expect(res.messages[0]).toContain('佐藤（稲毛C）');
    expect(res.messages[0]).toContain('同時には行けません');
  });

  it('別日の同時刻は重複でない', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', dayKey: 0, startMin: 600, endMin: 660 }),
      entry({ visitId: 'b', dayKey: 1, startMin: 600, endMin: 660 }),
    ]);
    expect(res.overlapVisitIds.size).toBe(0);
  });

  it('時刻不明 (null) の訪問は判定から除外', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: null, endMin: null }),
      entry({ visitId: 'b', startMin: 600, endMin: 660 }),
    ]);
    expect(res.overlapVisitIds.size).toBe(0);
  });

  it('同一 visitId の重複入力は 1 件に畳む (コース選択 ∪ 個別)', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 660 }),
      entry({ visitId: 'a', startMin: 600, endMin: 660 }),
    ]);
    expect(res.overlapVisitIds.size).toBe(0);
  });

  it('3件が相互に重なると全件を検出', () => {
    const res = computeAccompanimentOverlaps([
      entry({ visitId: 'a', startMin: 600, endMin: 700 }),
      entry({ visitId: 'b', startMin: 610, endMin: 710 }),
      entry({ visitId: 'c', startMin: 620, endMin: 720 }),
    ]);
    expect(res.overlapVisitIds.size).toBe(3);
    expect(res.messages.length).toBe(3); // a×b, a×c, b×c
  });
});
