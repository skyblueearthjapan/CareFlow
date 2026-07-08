/**
 * Phase G-6: courseGrid の detectSameAddressPair (同住所×同時刻ペア判定) の
 * pure 関数 unit test.
 *
 * 仕様:
 *   - 異なる patient_id × 同 same_address_group_id × 2 件以上 で true.
 *   - 同一患者の 2-staff 重複 (visit_group の主訪問+副訪問) は false (= 除外).
 *   - null / undefined key は判定対象外.
 *   - occupants 1 件以下は常に false.
 */
import { describe, expect, it } from 'vitest';

import { detectSameAddressPair } from '@/components/schedule/v2/courseGrid';

type Occupant = { patient_id: string; same_address_group_id?: string | null };

const o = (pid: string, key: string | null | undefined): Occupant => ({
  patient_id: pid,
  same_address_group_id: key,
});

describe('detectSameAddressPair (Phase G-6)', () => {
  it('占有者 0 件 → false', () => {
    expect(detectSameAddressPair([])).toBe(false);
  });

  it('占有者 1 件 → false', () => {
    expect(detectSameAddressPair([o('P1', 'k1')])).toBe(false);
  });

  it('異なる patient × 同 key 2 件 → true (= 同住所ペア)', () => {
    expect(detectSameAddressPair([o('P1', 'k1'), o('P2', 'k1')])).toBe(true);
  });

  it('異なる patient × 別 key 2 件 → false', () => {
    expect(detectSameAddressPair([o('P1', 'k1'), o('P2', 'k2')])).toBe(false);
  });

  it('**同一患者** × 同 key 2 件 (visit_group 主+副) → false (= ペアではない)', () => {
    // 2-staff 訪問では同じ患者の visit が 2 件 occupants に並ぶケースがある.
    // 同住所×同時刻「ペア」 は **異なる患者** 同士のことを指すため、 これは false.
    expect(detectSameAddressPair([o('P1', 'k1'), o('P1', 'k1')])).toBe(false);
  });

  it('同一患者 2 件 + 別患者 1 件 × 同 key → true', () => {
    expect(detectSameAddressPair([o('P1', 'k1'), o('P1', 'k1'), o('P2', 'k1')])).toBe(true);
  });

  it('key が null の visit は判定対象外 (= lat/lng なし患者)', () => {
    expect(detectSameAddressPair([o('P1', null), o('P2', null)])).toBe(false);
  });

  it('key が undefined の visit は判定対象外', () => {
    expect(detectSameAddressPair([o('P1', undefined), o('P2', undefined)])).toBe(false);
  });

  it('null + 同 key 異患者 → 異患者ペアが成立すれば true', () => {
    expect(detectSameAddressPair([o('P1', null), o('P2', 'k1'), o('P3', 'k1')])).toBe(true);
  });

  it('3 患者すべて同 key → true', () => {
    expect(detectSameAddressPair([o('P1', 'k1'), o('P2', 'k1'), o('P3', 'k1')])).toBe(true);
  });
});
