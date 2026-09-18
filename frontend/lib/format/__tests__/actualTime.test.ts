/** 打刻の実績時刻ヘルパー (お客様要望 2026-09-18) の境界テスト。 */
import { describe, it, expect } from 'vitest';

import { actualTimeParts, fmtActualRange, jstHm } from '../actualTime';

describe('jstHm', () => {
  it('ISO (UTC) を JST の HH:MM にする', () => {
    expect(jstHm('2026-09-18T03:56:00Z')).toBe('12:56');
  });

  it('tz 付きの +09:00 表記もそのまま読む', () => {
    expect(jstHm('2026-09-18T13:40:00+09:00')).toBe('13:40');
  });

  it('tz 無しの naive な値は JST として読む (実行環境の TZ に依存しない)', () => {
    expect(jstHm('2026-09-18T12:56:00')).toBe('12:56');
    expect(jstHm('2026-09-18T12:56:00.123')).toBe('12:56');
    // UTC として読むと 21:56 になってしまう = 9 時間ズレを出さないことの確認。
    expect(jstHm('2026-09-18T12:56:00')).not.toBe('21:56');
  });

  it('+0900 (コロンなし) の tz も tz 付きとして読む', () => {
    expect(jstHm('2026-09-18T13:40:00+0900')).toBe('13:40');
  });

  it('JST 深夜 0 時は 24:00 ではなく 00:00', () => {
    expect(jstHm('2026-09-17T15:00:00Z')).toBe('00:00');
  });

  it('null / 空 / 不正な値は null', () => {
    expect(jstHm(null)).toBeNull();
    expect(jstHm(undefined)).toBeNull();
    expect(jstHm('')).toBeNull();
    expect(jstHm('not-a-date')).toBeNull();
  });
});

describe('actualTimeParts', () => {
  it('打刻なし (null / null) は null', () => {
    expect(actualTimeParts(null, null)).toBeNull();
    expect(actualTimeParts(undefined, undefined)).toBeNull();
  });

  it('到着のみ = 未退出。range は「12:56 〜」', () => {
    const parts = actualTimeParts('2026-09-18T03:56:00Z', null);
    expect(parts).not.toBeNull();
    expect(parts!.arrival).toBe('12:56');
    expect(parts!.departure).toBeNull();
    expect(parts!.done).toBe(false);
    expect(parts!.range).toBe('12:56 〜');
    expect(parts!.compactRange).toBe('12:56〜');
  });

  it('到着+退出 = 確定した実績レンジ', () => {
    const parts = actualTimeParts('2026-09-18T03:56:00Z', '2026-09-18T04:40:00Z');
    expect(parts!.done).toBe(true);
    expect(parts!.range).toBe('12:56 – 13:40');
    expect(parts!.compactRange).toBe('12:56–13:40');
  });

  it('到着が無ければ退出だけでは出さない (null)', () => {
    expect(actualTimeParts(null, '2026-09-18T04:40:00Z')).toBeNull();
  });

  it('日跨ぎは時刻だけを出す (日付は出さない)', () => {
    const parts = actualTimeParts('2026-09-18T14:50:00Z', '2026-09-18T15:20:00Z');
    expect(parts!.range).toBe('23:50 – 00:20');
    expect(parts!.range).not.toContain('/');
  });
});

describe('fmtActualRange', () => {
  it('打刻なしは null・ありは range 文字列', () => {
    expect(fmtActualRange(null, null)).toBeNull();
    expect(fmtActualRange('2026-09-18T03:56:00Z', '2026-09-18T04:40:00Z')).toBe('12:56 – 13:40');
    expect(fmtActualRange('2026-09-18T03:56:00Z', null)).toBe('12:56 〜');
  });
});
