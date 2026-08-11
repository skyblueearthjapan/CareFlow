/**
 * 提案系 warnings ラベル — NG スタッフ (patient-ng-staff-design.md §8-2 Phase 2).
 *
 * BE (`propose_slots_service.py`) が返す警告コード `staff_ng_mismatch` が
 * 日本語ラベルに解決されること (= 生コードが UI に漏れないこと) を固定する。
 */
import { describe, it, expect } from 'vitest';

import { proposeWarningLabel } from '../fieldBoard';

describe('proposeWarningLabel — staff_ng_mismatch', () => {
  it('1. staff_ng_mismatch は「NGスタッフに該当」に解決される', () => {
    expect(proposeWarningLabel('staff_ng_mismatch')).toBe('NGスタッフに該当');
  });

  it('2. 既存の性別コードは従来どおり', () => {
    expect(proposeWarningLabel('staff_sex_mismatch')).toBe('性別条件に不適合');
  });

  it('3. 未知コードはそのまま返す (フォールバック維持)', () => {
    expect(proposeWarningLabel('brand_new_code')).toBe('brand_new_code');
  });
});
