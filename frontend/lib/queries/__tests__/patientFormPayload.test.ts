/**
 * `prepareFormPayload` の単体テスト — S1 レビュー M5。
 *
 * 「カイポケ サービス内容の上書き」は例外運用のための項目で、
 * **触っていない保存で勝手に消えてはいけない**。PATCH の慣習どおり
 * 「変わったときだけキーを送る」ことを固定する。
 */
import { describe, it, expect } from 'vitest';

import { prepareFormPayload } from '@/lib/queries/patients';
import {
  emptyPatientFormValues,
  patientUpdateSchema,
  type PatientFormValues,
} from '@/lib/schemas/patient';

function values(overrides: Partial<PatientFormValues> = {}): PatientFormValues {
  return { ...emptyPatientFormValues, name: 'テスト患者', ...overrides };
}

describe('prepareFormPayload — kaipoke_service_content (上書き)', () => {
  it('Create: 空欄なら送らない (undefined)', () => {
    const out = prepareFormPayload(values({ kaipoke_service_content: '' }));
    expect(out.kaipoke_service_content).toBeUndefined();
  });

  it('Create: 値があればそのまま送る', () => {
    const out = prepareFormPayload(values({ kaipoke_service_content: '精神基本療養費Ⅲ・正看' }));
    expect(out.kaipoke_service_content).toBe('精神基本療養費Ⅲ・正看');
  });

  it('Update: 変更が無ければキーごと送らない (既存の上書きを消さない)', () => {
    const initial = values({ kaipoke_service_content: '精神基本療養費Ⅲ・正看' });
    // 氏名だけ変えて保存する = 上書きは触っていない。
    const next = values({ kaipoke_service_content: '精神基本療養費Ⅲ・正看', name: '別名' });
    const out = prepareFormPayload(next, initial);
    expect(out.kaipoke_service_content).toBeUndefined();
    expect(out.name).toBe('別名');
  });

  it('Update: 空欄にしたときだけ明示的な null でクリアする', () => {
    const initial = values({ kaipoke_service_content: '精神基本療養費Ⅲ・正看' });
    const out = prepareFormPayload(values({ kaipoke_service_content: '' }), initial);
    expect(out.kaipoke_service_content).toBeNull();
  });

  it('Update: 値を変えたら新しい値を送る', () => {
    const initial = values({ kaipoke_service_content: '精神基本療養費Ⅲ・正看' });
    const out = prepareFormPayload(
      values({ kaipoke_service_content: '基本療養費Ⅰ・准看' }),
      initial,
    );
    expect(out.kaipoke_service_content).toBe('基本療養費Ⅰ・准看');
  });

  it('Update: 初期が空・今も空なら送らない (無変更)', () => {
    const initial = values({ kaipoke_service_content: '' });
    const out = prepareFormPayload(values({ kaipoke_service_content: '' }), initial);
    expect(out.kaipoke_service_content).toBeUndefined();
  });

  it('前後の空白だけの違いは変更として扱わない', () => {
    const initial = values({ kaipoke_service_content: '精神基本療養費Ⅲ・正看' });
    const out = prepareFormPayload(
      values({ kaipoke_service_content: '  精神基本療養費Ⅲ・正看  ' }),
      initial,
    );
    expect(out.kaipoke_service_content).toBeUndefined();
  });
});

describe('prepareFormPayload — visit_category (訪問看護区分)', () => {
  it('区分は常に送る (NOT NULL・既定 psychiatric)', () => {
    expect(prepareFormPayload(values()).visit_category).toBe('psychiatric');
    expect(prepareFormPayload(values({ visit_category: 'general' })).visit_category).toBe(
      'general',
    );
  });
});

describe('prepareFormPayload — 住所 2 つ (設計 2026-10-22 §4-1)', () => {
  const INS = '千葉県千葉市花見川区こてはし台9-9-9';
  const VISIT = '千葉県千葉市稲毛区園生町99-9';

  it('1. 「同じ」→ 訪問先は保険証の住所を写す', () => {
    const out = prepareFormPayload(
      values({ insurance_address: INS, visit_same_as_insurance: true, address: '古い住所' }),
    );
    expect(out).toMatchObject({
      insurance_address: INS,
      address: INS,
      visit_same_as_insurance: true,
    });
  });

  it('2. 「別の場所」で訪問先が空 → 「同じ」に戻す', () => {
    const out = prepareFormPayload(
      values({ insurance_address: INS, visit_same_as_insurance: false, address: '  ' }),
    );
    expect(out).toMatchObject({ address: INS, visit_same_as_insurance: true });
  });

  it('3. 「別の場所」で訪問先あり → そのまま', () => {
    const out = prepareFormPayload(
      values({ insurance_address: INS, visit_same_as_insurance: false, address: VISIT }),
    );
    expect(out).toMatchObject({
      insurance_address: INS,
      address: VISIT,
      visit_same_as_insurance: false,
    });
  });

  it('4. 保険証の住所が空で訪問先だけ → 推測で埋めず「別の場所」扱い', () => {
    const out = prepareFormPayload(
      values({ insurance_address: '', visit_same_as_insurance: true, address: VISIT }),
    );
    expect(out.insurance_address).toBeUndefined();
    expect(out).toMatchObject({ address: VISIT, visit_same_as_insurance: false });
  });

  it('更新: 入っていた保険証の住所を空にしたら null で消す (変わらなければ送らない)', () => {
    const initial = values({ insurance_address: INS, address: INS });
    const cleared = prepareFormPayload(
      values({ insurance_address: '', visit_same_as_insurance: false, address: VISIT }),
      initial,
    );
    expect(cleared.insurance_address).toBeNull();
    const untouched = prepareFormPayload(values({ insurance_address: '' }), values());
    expect(untouched.insurance_address).toBeUndefined();
  });

  it('更新の schema を通しても 2 つの値が残る (落とされない)', () => {
    const parsed = patientUpdateSchema.parse(
      prepareFormPayload(
        values({
          code: 'P902',
          insurance_address: INS,
          visit_same_as_insurance: false,
          address: VISIT,
        }),
        values({ code: 'P902' }),
      ),
    );
    expect(parsed).toMatchObject({
      insurance_address: INS,
      address: VISIT,
      visit_same_as_insurance: false,
    });
  });
});
