/**
 * businessBlocksFromHours + computeFreeGaps(custom blocks) 単体テスト — Phase G-88。
 *
 * 営業時間設定 (business_start/business_end) からフロント営業枠を組み立て、
 * 空き帯算出に反映する導線を検証する。不正入力時は null (= 既定枠フォールバック)。
 */
import { describe, it, expect } from 'vitest';

import { BUSINESS_BLOCKS, businessBlocksFromHours, computeFreeGaps, fmtHM } from '../freeGaps';

const v = (start: string, end: string) => ({ start_time: start, end_time: end });

describe('businessBlocksFromHours (Phase G-88)', () => {
  it('既定 09:30〜18:00 → 昼休み帯で 2 分割 (09:30–12:00 / 13:00–18:00)', () => {
    expect(businessBlocksFromHours('09:30:00', '18:00:00')).toEqual([
      [9 * 60 + 30, 12 * 60],
      [13 * 60, 18 * 60],
    ]);
  });

  it('"HH:MM" でも解釈できる (秒なし)', () => {
    expect(businessBlocksFromHours('09:30', '18:00')).toEqual([
      [9 * 60 + 30, 12 * 60],
      [13 * 60, 18 * 60],
    ]);
  });

  it('営業が昼前で終わる → 単一枠 (08:00–11:30)', () => {
    expect(businessBlocksFromHours('08:00:00', '11:30:00')).toEqual([[8 * 60, 11 * 60 + 30]]);
  });

  it('営業が昼後に始まる → 単一枠 (13:30–19:00)', () => {
    expect(businessBlocksFromHours('13:30:00', '19:00:00')).toEqual([[13 * 60 + 30, 19 * 60]]);
  });

  it('開始>=終了 は null (= 既定枠フォールバック)', () => {
    expect(businessBlocksFromHours('18:00:00', '09:30:00')).toBeNull();
    expect(businessBlocksFromHours('10:00:00', '10:00:00')).toBeNull();
  });

  it('null / 不正値は null', () => {
    expect(businessBlocksFromHours(null, '18:00:00')).toBeNull();
    expect(businessBlocksFromHours('09:30:00', undefined)).toBeNull();
    expect(businessBlocksFromHours('xx', 'yy')).toBeNull();
  });
});

describe('computeFreeGaps with custom business blocks (Phase G-88)', () => {
  it('カスタム営業枠 (07:00–10:00 早朝のみ) で空きが算出される', () => {
    const blocks = businessBlocksFromHours('07:00:00', '10:00:00')!;
    const gaps = computeFreeGaps([], blocks);
    // 訪問が無ければ営業枠全体 (180 分 ≥ 60) が空き。
    expect(gaps).toHaveLength(1);
    expect(fmtHM(gaps[0]!.startMin)).toBe('07:00');
    expect(fmtHM(gaps[0]!.endMin)).toBe('10:00');
  });

  it('省略時は既定 BUSINESS_BLOCKS を使う (後方互換)', () => {
    const gaps = computeFreeGaps([v('09:30:00', '10:30:00')]);
    // 既定枠で算出されることを確認 (PM ブロック 13:00–18:00 が空きに含まれる)。
    expect(gaps.some((g) => fmtHM(g.startMin) === '13:00')).toBe(true);
    expect(BUSINESS_BLOCKS.length).toBe(2);
  });
});
