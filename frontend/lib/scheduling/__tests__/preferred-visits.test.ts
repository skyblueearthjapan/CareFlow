/**
 * preferred-visits utility — vitest unit tests (Phase G-44).
 *
 * カバーするケース:
 *   - getDesiredWeeklyVisitCount: null / undefined / 0 / 正常値 / 範囲外クランプ
 *   - countWeekVisits: visit_date null は除外 / patient_id 不一致は除外
 *   - getShortageCount: desired=0 → 0, 希望 3 配置 1 → 2, 希望 3 配置 3 → 0,
 *     希望 3 配置 4 → 0 (= 過剰配置でも負値にならない)
 */
import { describe, it, expect } from 'vitest';

import { countWeekVisits, getDesiredWeeklyVisitCount, getShortageCount } from '../preferred-visits';

describe('getDesiredWeeklyVisitCount', () => {
  it('weekly_pattern が null → 0', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: null })).toBe(0);
  });

  it('weekly_pattern が undefined → 0', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: undefined })).toBe(0);
  });

  it('frequency_per_week が 0 → 0 (希望未設定扱い)', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: 0 } })).toBe(0);
  });

  it('frequency_per_week=3 → 3', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: 3 } })).toBe(3);
  });

  it('frequency_per_week=7 → 7 (上限)', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: 7 } })).toBe(7);
  });

  it('frequency_per_week=99 → 7 にクランプ', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: 99 } })).toBe(7);
  });

  it('frequency_per_week が文字列 "2" → 2 (数値化される)', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: '2' } })).toBe(2);
  });

  it('frequency_per_week が無効値 "abc" → 0', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: 'abc' } })).toBe(0);
  });

  it('frequency_per_week キー欠落 → 0', () => {
    expect(getDesiredWeeklyVisitCount({ weekly_pattern: {} })).toBe(0);
  });
});

describe('countWeekVisits', () => {
  it('patient_id 一致 + visit_date 非 null のみカウント', () => {
    const visits = [
      { patient_id: 'p1', visit_date: '2026-05-25' },
      { patient_id: 'p1', visit_date: '2026-05-26' },
      { patient_id: 'p2', visit_date: '2026-05-25' }, // 別 patient
      { patient_id: 'p1', visit_date: null }, // 未配置
      { patient_id: 'p1', visit_date: '' }, // 空文字
    ];
    expect(countWeekVisits(visits, 'p1')).toBe(2);
  });

  it('該当 visit が無い → 0', () => {
    expect(countWeekVisits([], 'p1')).toBe(0);
  });

  it('visit_date 省略 (undefined) → カウントしない', () => {
    const visits = [{ patient_id: 'p1' }, { patient_id: 'p1', visit_date: '2026-05-25' }];
    expect(countWeekVisits(visits, 'p1')).toBe(1);
  });
});

describe('getShortageCount', () => {
  it('希望未設定 (desired=0) → 0 (= pool 対象外)', () => {
    expect(getShortageCount({ weekly_pattern: null }, [], 'p1')).toBe(0);
  });

  it('希望 3 回 vs 実 1 件 → 不足 2 件', () => {
    const patient = { weekly_pattern: { frequency_per_week: 3 } };
    const visits = [{ patient_id: 'p1', visit_date: '2026-05-25' }];
    expect(getShortageCount(patient, visits, 'p1')).toBe(2);
  });

  it('希望 3 回 vs 実 3 件 → 不足 0 件', () => {
    const patient = { weekly_pattern: { frequency_per_week: 3 } };
    const visits = [
      { patient_id: 'p1', visit_date: '2026-05-25' },
      { patient_id: 'p1', visit_date: '2026-05-26' },
      { patient_id: 'p1', visit_date: '2026-05-27' },
    ];
    expect(getShortageCount(patient, visits, 'p1')).toBe(0);
  });

  it('希望 3 回 vs 実 4 件 (過剰) → 不足 0 件 (負値にならない)', () => {
    const patient = { weekly_pattern: { frequency_per_week: 3 } };
    const visits = [
      { patient_id: 'p1', visit_date: '2026-05-25' },
      { patient_id: 'p1', visit_date: '2026-05-26' },
      { patient_id: 'p1', visit_date: '2026-05-27' },
      { patient_id: 'p1', visit_date: '2026-05-28' },
    ];
    expect(getShortageCount(patient, visits, 'p1')).toBe(0);
  });

  it('希望 2 回 vs 実 0 件 → 不足 2 件', () => {
    const patient = { weekly_pattern: { frequency_per_week: 2 } };
    expect(getShortageCount(patient, [], 'p1')).toBe(2);
  });
});
