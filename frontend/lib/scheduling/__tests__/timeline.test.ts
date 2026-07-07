import { describe, expect, it } from 'vitest';

import {
  assignLanes,
  durationToHeight,
  genderKey,
  genderPalette,
  minutesToY,
  TL_DAY_END_MIN,
  TL_DAY_START_MIN,
  TL_ROW_PX,
  timeToY,
  timelineHeightPx,
} from '@/lib/scheduling/timeline';

describe('timeline geometry', () => {
  it('9:00 は Y=0、30分 = TL_ROW_PX', () => {
    expect(minutesToY(TL_DAY_START_MIN)).toBe(0);
    expect(minutesToY(TL_DAY_START_MIN + 30)).toBe(TL_ROW_PX);
    expect(minutesToY(TL_DAY_START_MIN + 60)).toBe(TL_ROW_PX * 2);
  });

  it('durationToHeight は所要分に比例する', () => {
    expect(durationToHeight(30)).toBe(TL_ROW_PX);
    expect(durationToHeight(60)).toBe(TL_ROW_PX * 2);
    expect(durationToHeight(35)).toBeCloseTo((35 / 30) * TL_ROW_PX);
  });

  it('全高は 9:00〜18:00 = 9時間 = 18行分', () => {
    expect(timelineHeightPx()).toBe(((TL_DAY_END_MIN - TL_DAY_START_MIN) / 30) * TL_ROW_PX);
    expect(timelineHeightPx()).toBe(18 * TL_ROW_PX);
  });

  it('timeToY は HH:MM / HH:MM:SS を解釈し、不正値は null', () => {
    expect(timeToY('09:00')).toBe(0);
    expect(timeToY('09:30:00')).toBe(TL_ROW_PX);
    expect(timeToY('')).toBeNull();
    expect(timeToY(null)).toBeNull();
  });
});

describe('genderKey / genderPalette', () => {
  it('male/female/その他 を m/f/n に写像する', () => {
    expect(genderKey('male')).toBe('m');
    expect(genderKey('female')).toBe('f');
    expect(genderKey('unknown')).toBe('n');
    expect(genderKey(null)).toBe('n');
    expect(genderKey(undefined)).toBe('n');
  });

  it('未設定は中立トークンを返す (表示が壊れない)', () => {
    expect(genderPalette(null).bg).toContain('neutral');
    expect(genderPalette('male').bg).toContain('male');
    expect(genderPalette('female').bar).toContain('female');
  });
});

describe('assignLanes', () => {
  it('重ならない訪問は全て lane 0', () => {
    const r = assignLanes([
      { id: 'a', startMin: 540, endMin: 575 },
      { id: 'b', startMin: 600, endMin: 635 },
    ]);
    expect(r.get('a')?.lane).toBe(0);
    expect(r.get('b')?.lane).toBe(0);
    expect(r.get('a')?.laneCount).toBe(1);
  });

  it('重なる2件は別レーン (laneCount=2)', () => {
    const r = assignLanes([
      { id: 'a', startMin: 540, endMin: 600 },
      { id: 'b', startMin: 570, endMin: 630 },
    ]);
    const la = r.get('a')?.lane;
    const lb = r.get('b')?.lane;
    expect(la).not.toBe(lb);
    expect(r.get('a')?.laneCount).toBe(2);
  });

  it('境界 (end == start) は重ならない扱い', () => {
    const r = assignLanes([
      { id: 'a', startMin: 540, endMin: 600 },
      { id: 'b', startMin: 600, endMin: 660 },
    ]);
    expect(r.get('a')?.lane).toBe(0);
    expect(r.get('b')?.lane).toBe(0);
  });

  it('重なる2名ペアの後に続く単体は laneCount=1 (全幅) — 現場報告バグの修正', () => {
    const r = assignLanes([
      // 9:00-10:00 に重なる2名ペア (半分ずつ)
      { id: 'pairA', startMin: 540, endMin: 600 },
      { id: 'pairB', startMin: 540, endMin: 600 },
      // その後の単体 (重ならない) は全幅であるべき
      { id: 'solo1', startMin: 660, endMin: 695 },
      { id: 'solo2', startMin: 720, endMin: 755 },
    ]);
    expect(r.get('pairA')?.laneCount).toBe(2);
    expect(r.get('pairB')?.laneCount).toBe(2);
    expect(r.get('pairA')?.lane).not.toBe(r.get('pairB')?.lane);
    // ペアと重ならない単体は全幅 (laneCount=1)。
    expect(r.get('solo1')?.laneCount).toBe(1);
    expect(r.get('solo2')?.laneCount).toBe(1);
  });

  it('別々に重なる2つの塊は独立して数える (3件塊と2件塊)', () => {
    const r = assignLanes([
      { id: 'a', startMin: 540, endMin: 640 },
      { id: 'b', startMin: 560, endMin: 660 },
      { id: 'c', startMin: 580, endMin: 680 }, // a,b,c が数珠つなぎ → 3レーン
      { id: 'x', startMin: 720, endMin: 780 },
      { id: 'y', startMin: 740, endMin: 800 }, // x,y → 2レーン
    ]);
    expect(r.get('a')?.laneCount).toBe(3);
    expect(r.get('c')?.laneCount).toBe(3);
    expect(r.get('x')?.laneCount).toBe(2);
    expect(r.get('y')?.laneCount).toBe(2);
  });
});
