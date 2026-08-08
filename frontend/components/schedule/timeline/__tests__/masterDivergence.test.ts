/**
 * 型 (固定訪問スケジュール) とのズレ表示の判定 (PO 決定 2026-08-08)。
 *
 * 方針: ズレていることを伝えるだけ。合わせ直す導線も件数サマリも作らない。
 * 表示は案 A (左端の帯) + 案 B (時刻行に「（型 HH:MM）」併記) の併用。
 */
import { describe, expect, it } from 'vitest';

import {
  isDivergedFromMaster,
  masterDivergenceCardStyle,
  masterDivergenceTitle,
  masterTimeSuffix,
} from '../masterDivergence';

describe('型とのズレ判定', () => {
  it('型の開始時刻があればズレていると判定する', () => {
    expect(isDivergedFromMaster({ master_start_time: '13:00' })).toBe(true);
  });

  it('一致 / 固定枠なしはズレ扱いしない', () => {
    expect(isDivergedFromMaster({ master_start_time: null })).toBe(false);
    expect(isDivergedFromMaster({})).toBe(false);
    // 空文字は「値なし」として扱う (BE から '' が来ても誤検知しない)。
    expect(isDivergedFromMaster({ master_start_time: '' })).toBe(false);
  });
});

describe('案 B: 時刻行への併記', () => {
  it('ズレているときだけ本来の時刻を出す', () => {
    expect(masterTimeSuffix({ master_start_time: '13:00' })).toBe('（型 13:00）');
  });

  it('ズレていなければ何も出さない', () => {
    expect(masterTimeSuffix({ master_start_time: null })).toBeNull();
    expect(masterTimeSuffix({})).toBeNull();
  });
});

describe('案 A: カード左端の帯', () => {
  it('ズレているときは破線 + 警告色にする', () => {
    expect(masterDivergenceCardStyle({ master_start_time: '10:00' })).toEqual({
      borderLeftStyle: 'dashed',
      borderLeftColor: 'var(--warning)',
    });
  });

  it('ズレていなければスタイルを足さない (性別色の帯をそのまま残す)', () => {
    expect(masterDivergenceCardStyle({ master_start_time: null })).toBeNull();
  });
});

describe('説明文', () => {
  it('ピン留めできない理由まで含めて伝える', () => {
    expect(masterDivergenceTitle({ master_start_time: '13:00' })).toBe(
      '固定訪問スケジュールは 13:00 です。この時間帯ではピン留めできません',
    );
  });

  it('ズレていなければ説明を出さない', () => {
    expect(masterDivergenceTitle({})).toBeNull();
  });
});
