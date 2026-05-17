/**
 * formatDuration — 2026-W20 後期 /schedule UX 改修 (Task A).
 *
 * 実動時間 (分) を「30 分」「1 時間 15 分」等の日本語表記へ変換するヘルパー
 * の単体テスト. 仕様で要求される全パターンを網羅する.
 */
import { describe, it, expect } from 'vitest';

import { formatDuration } from '@/lib/format/duration';

describe('formatDuration', () => {
  it('0 分 → "0 分"', () => {
    expect(formatDuration(0)).toBe('0 分');
  });

  it('30 分 → "30 分"', () => {
    expect(formatDuration(30)).toBe('30 分');
  });

  it('45 分 → "45 分"', () => {
    expect(formatDuration(45)).toBe('45 分');
  });

  it('60 分 → "1 時間"', () => {
    expect(formatDuration(60)).toBe('1 時間');
  });

  it('75 分 → "1 時間 15 分"', () => {
    expect(formatDuration(75)).toBe('1 時間 15 分');
  });

  it('90 分 → "1 時間 30 分"', () => {
    expect(formatDuration(90)).toBe('1 時間 30 分');
  });

  it('120 分 → "2 時間"', () => {
    expect(formatDuration(120)).toBe('2 時間');
  });

  it('150 分 → "2 時間 30 分"', () => {
    expect(formatDuration(150)).toBe('2 時間 30 分');
  });

  it('180 分 → "3 時間"', () => {
    expect(formatDuration(180)).toBe('3 時間');
  });

  it('負値は "0 分" (UI 安全側のフォールバック)', () => {
    expect(formatDuration(-15)).toBe('0 分');
  });

  it('NaN は "0 分" (UI 安全側のフォールバック)', () => {
    expect(formatDuration(Number.NaN)).toBe('0 分');
  });

  it('小数は床関数で切り下げる (29.7 → "29 分")', () => {
    expect(formatDuration(29.7)).toBe('29 分');
  });

  it('小数 60.9 → "1 時間" (床関数で 60 になる)', () => {
    expect(formatDuration(60.9)).toBe('1 時間');
  });
});
