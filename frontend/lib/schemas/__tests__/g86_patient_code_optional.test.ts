/**
 * Phase G-86: 患者コードの自動採番 (必須→任意) — schema 単体テスト.
 *
 * カバーするシナリオ:
 *   - patientCreateSchema: code 空文字 → undefined に畳む (wire から落として
 *     backend に自動採番させる)。code 省略でも parse 成功。
 *   - patientCreateSchema: code 入力あり → 従来どおりその値を保持。
 *   - patientFormSchema: code 空でも resolver が通る (氏名のみ必須)。
 *   - patientV2CreateSchema: code 省略でも parse 成功 / 入力ありは保持。
 *   - 回帰: name は依然必須 (空は error)。
 */
import { describe, it, expect } from 'vitest';

import { patientCreateSchema, patientFormSchema, emptyWeeklyPattern } from '../patient';
import { patientV2CreateSchema } from '../v2/patient';

describe('Phase G-86 — patientCreateSchema の code 任意化', () => {
  it('code 空文字 → undefined に畳む (自動採番に倒す)', () => {
    const parsed = patientCreateSchema.parse({ code: '', name: '田中 太郎' });
    expect(parsed.code).toBeUndefined();
    expect(parsed.name).toBe('田中 太郎');
  });

  it('code 省略でも parse 成功', () => {
    const parsed = patientCreateSchema.parse({ name: '田中 太郎' });
    expect(parsed.code).toBeUndefined();
  });

  it('code 入力あり → 従来どおりその値を保持 (前後空白は trim)', () => {
    const parsed = patientCreateSchema.parse({ code: ' P-1042 ', name: '青柳 あい' });
    expect(parsed.code).toBe('P-1042');
  });

  it('name は依然必須 (空は error)', () => {
    const res = patientCreateSchema.safeParse({ code: '', name: '' });
    expect(res.success).toBe(false);
  });
});

describe('Phase G-86 — patientFormSchema の code 任意化', () => {
  const base = {
    name: 'テスト患者',
    kana: '',
    sex: '' as const,
    status: 'active' as const,
    insurance: '' as const,
    address: '',
    lat: '',
    lng: '',
    primary_office_id: '',
    sex_restriction: '' as const,
    requires_multiple_staff: false,
    note: '',
    weekly_pattern: emptyWeeklyPattern,
    special_week: false,
  };

  it('code 空でも resolver (form schema) が通る', () => {
    const res = patientFormSchema.safeParse({ ...base, code: '' });
    expect(res.success).toBe(true);
    if (res.success) expect(res.data.code).toBeUndefined();
  });

  it('code 入力ありは保持される', () => {
    const res = patientFormSchema.safeParse({ ...base, code: 'P-0001' });
    expect(res.success).toBe(true);
    if (res.success) expect(res.data.code).toBe('P-0001');
  });

  it('name 空はやはり error', () => {
    const res = patientFormSchema.safeParse({ ...base, name: '', code: '' });
    expect(res.success).toBe(false);
  });
});

describe('Phase G-86 — patientV2CreateSchema の code 任意化', () => {
  it('code 省略でも parse 成功', () => {
    const parsed = patientV2CreateSchema.parse({ name: '田中 太郎' });
    expect(parsed.code).toBeUndefined();
  });

  it('code 入力ありは保持される', () => {
    const parsed = patientV2CreateSchema.parse({ code: 'P-2000', name: '田中 太郎' });
    expect(parsed.code).toBe('P-2000');
  });

  it('name は依然必須', () => {
    const res = patientV2CreateSchema.safeParse({ code: 'P-1' });
    expect(res.success).toBe(false);
  });
});
