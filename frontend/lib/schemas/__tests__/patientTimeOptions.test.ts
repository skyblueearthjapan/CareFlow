import { describe, expect, it } from 'vitest';

import { PATIENT_TIME_OPTIONS, patientTimeOptionsWith } from '../patient';

describe('患者マスターの時刻の選択肢 (5 分刻み・08:00〜19:00)', () => {
  it('08:00 から 19:00 まで 5 分刻み', () => {
    expect(PATIENT_TIME_OPTIONS[0]).toBe('08:00');
    expect(PATIENT_TIME_OPTIONS[1]).toBe('08:05');
    expect(PATIENT_TIME_OPTIONS.at(-1)).toBe('19:00');
    expect(PATIENT_TIME_OPTIONS).toHaveLength(11 * 12 + 1);
    expect(PATIENT_TIME_OPTIONS).toContain('17:35');
  });

  it('今の値が選択肢にあればそのまま', () => {
    expect(patientTimeOptionsWith('10:05')).toBe(PATIENT_TIME_OPTIONS);
    expect(patientTimeOptionsWith('10:05:00')).toBe(PATIENT_TIME_OPTIONS);
    expect(patientTimeOptionsWith(null)).toBe(PATIENT_TIME_OPTIONS);
  });

  it('範囲外・刻み外の今の値は消さずに足して並べる (開いただけで化けない)', () => {
    const early = patientTimeOptionsWith('07:30');
    expect(early[0]).toBe('07:30');
    const odd = patientTimeOptionsWith('10:07:00');
    expect(odd).toContain('10:07');
    expect(odd.indexOf('10:07')).toBe(odd.indexOf('10:05') + 1);
  });
});
