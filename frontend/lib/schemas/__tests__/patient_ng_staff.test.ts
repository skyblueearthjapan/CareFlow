/**
 * NG スタッフ zod schemas + 422 パーサのテスト
 * (patient-ng-staff-design.md §4-1 / §7-2).
 */
import { describe, it, expect } from 'vitest';

import {
  formatNgStaffNames,
  parseConstraintConfirmationDetail,
  patientNgStaffListSchema,
  patientNgStaffReadSchema,
  staffNgPatientListSchema,
  staffNgPatientReadSchema,
} from '../patient_ng_staff';
import { patientReadSchema } from '../patient';

const STAFF_ID = '00000000-0000-0000-0000-0000000000a1';

const validRow = {
  staff_id: STAFF_ID,
  staff_name: '田中 一郎',
  staff_is_deleted: false,
  note: 'ご家族からの申し出',
  decided_by_user_id: '00000000-0000-0000-0000-0000000000a2',
  created_at: '2026-08-11T00:00:00Z',
};

describe('patientNgStaffReadSchema', () => {
  it('1. 正しい行を parse できる', () => {
    const r = patientNgStaffReadSchema.parse(validRow);
    expect(r.staff_id).toBe(STAFF_ID);
    expect(r.note).toBe('ご家族からの申し出');
  });

  it('2. staff_is_deleted 省略時は false にデフォルト', () => {
    const { staff_is_deleted: _omit, ...rest } = validRow;
    expect(patientNgStaffReadSchema.parse(rest).staff_is_deleted).toBe(false);
  });

  it('3. note / staff_name / decided_by_user_id が null でも parse 成功', () => {
    const r = patientNgStaffReadSchema.parse({
      ...validRow,
      staff_name: null,
      note: null,
      decided_by_user_id: null,
    });
    expect(r.note).toBeNull();
    expect(r.staff_name).toBeNull();
  });

  it('4. staff_id が UUID でなければ ZodError', () => {
    expect(() => patientNgStaffReadSchema.parse({ ...validRow, staff_id: 'x' })).toThrow();
  });

  it('5. 配列 (一覧レスポンス) を parse できる', () => {
    expect(patientNgStaffListSchema.parse([validRow])).toHaveLength(1);
    expect(patientNgStaffListSchema.parse([])).toEqual([]);
  });
});

describe('formatNgStaffNames', () => {
  it('6. 0 件は「なし」', () => {
    expect(formatNgStaffNames([])).toBe('なし');
  });

  it('7. 複数件は「・」区切り、退職済みには (退職) を添える', () => {
    const label = formatNgStaffNames([
      patientNgStaffReadSchema.parse(validRow),
      patientNgStaffReadSchema.parse({
        ...validRow,
        staff_id: '00000000-0000-0000-0000-0000000000a3',
        staff_name: '退職 花子',
        staff_is_deleted: true,
      }),
    ]);
    expect(label).toBe('田中 一郎・退職 花子(退職)');
  });

  it('8. staff_name が null なら (氏名未登録)', () => {
    const label = formatNgStaffNames([
      patientNgStaffReadSchema.parse({ ...validRow, staff_name: null }),
    ]);
    expect(label).toBe('(氏名未登録)');
  });
});

describe('staffNgPatientReadSchema (§8-2 Phase 2: 逆引き)', () => {
  const validReverseRow = {
    patient_id: '00000000-0000-0000-0000-0000000000b1',
    patient_name: '山田 太郎',
    note: '相性不良',
    created_at: '2026-08-11T00:00:00Z',
  };

  it('9. 正しい行を parse できる', () => {
    const r = staffNgPatientReadSchema.parse(validReverseRow);
    expect(r.patient_id).toBe(validReverseRow.patient_id);
    expect(r.patient_name).toBe('山田 太郎');
    expect(r.note).toBe('相性不良');
  });

  it('10. note / patient_name / created_at が null でも parse 成功', () => {
    const r = staffNgPatientReadSchema.parse({
      ...validReverseRow,
      patient_name: null,
      note: null,
      created_at: null,
    });
    expect(r.note).toBeNull();
  });

  it('11. patient_id が UUID でなければ ZodError', () => {
    expect(() => staffNgPatientReadSchema.parse({ ...validReverseRow, patient_id: 'x' })).toThrow();
  });

  it('12. 配列 (一覧レスポンス) を parse できる。0 件は []', () => {
    expect(staffNgPatientListSchema.parse([validReverseRow])).toHaveLength(1);
    expect(staffNgPatientListSchema.parse([])).toEqual([]);
  });
});

describe('patientReadSchema.ng_staff_count (§8-2 Phase 2: バッジ用の派生値)', () => {
  const basePatient = {
    id: '00000000-0000-0000-0000-0000000000c1',
    code: 'P-001',
    name: '佐藤 花子',
    status: 'active',
    created_at: '2026-08-11T00:00:00Z',
    updated_at: '2026-08-11T00:00:00Z',
  };

  it('13. BE が返した件数がそのまま入る', () => {
    expect(patientReadSchema.parse({ ...basePatient, ng_staff_count: 2 }).ng_staff_count).toBe(2);
  });

  it('14. フィールド欠落 (旧 BE) では 0 にフォールバック', () => {
    expect(patientReadSchema.parse(basePatient).ng_staff_count).toBe(0);
  });
});

describe('parseConstraintConfirmationDetail (§7-2 の 422)', () => {
  const body = {
    detail: {
      code: 'constraint_confirmation_required',
      warnings: [
        {
          kind: 'ng_staff',
          patient_id: 'p1',
          patient_name: '山田',
          staff_id: 's1',
          staff_name: '田中 一郎',
          note: 'ご家族からの申し出',
        },
        {
          kind: 'gender',
          patient_id: 'p2',
          patient_name: '佐藤',
          staff_id: 's1',
          staff_name: '田中 一郎',
          note: null,
        },
      ],
    },
  };

  it('9. 正しい 422 body から detail を取り出せる', () => {
    const d = parseConstraintConfirmationDetail(body);
    expect(d).not.toBeNull();
    expect(d?.warnings).toHaveLength(2);
    expect(d?.warnings[0]?.kind).toBe('ng_staff');
    expect(d?.warnings[1]?.kind).toBe('gender');
  });

  it('10. code が異なる detail は null (別種の 422 を誤認しない)', () => {
    expect(
      parseConstraintConfirmationDetail({ detail: { code: 'other', warnings: [] } }),
    ).toBeNull();
  });

  it('11. detail が文字列 / 未定義 / null なら null', () => {
    expect(parseConstraintConfirmationDetail({ detail: 'week is pinned' })).toBeNull();
    expect(parseConstraintConfirmationDetail({})).toBeNull();
    expect(parseConstraintConfirmationDetail(null)).toBeNull();
    expect(parseConstraintConfirmationDetail('boom')).toBeNull();
  });

  it('12. warnings の kind が未知なら null (形が違えば拾わない)', () => {
    expect(
      parseConstraintConfirmationDetail({
        detail: {
          code: 'constraint_confirmation_required',
          warnings: [{ kind: 'other', patient_id: 'p', staff_id: 's' }],
        },
      }),
    ).toBeNull();
  });

  it('13. warnings が空配列でも detail として成立する', () => {
    const d = parseConstraintConfirmationDetail({
      detail: { code: 'constraint_confirmation_required', warnings: [] },
    });
    expect(d?.warnings).toEqual([]);
  });
});
