/** compareByKana — あいうえお順コンパレータ (患者/スタッフマスタ共有・PO要望 2026-08-21)。 */
import { describe, expect, it } from 'vitest';

import { compareByKana } from '../kana-sort';

describe('compareByKana', () => {
  it('カタカナのあいうえお順に並ぶ (本番データの表記)', () => {
    const rows = [
      { kana: 'カワナ　チエ', code: 'S002' },
      { kana: 'アサクラ ミユ', code: 'P010' },
      { kana: 'イシヅカ　マイ', code: 'P003' },
    ];
    const sorted = [...rows].sort(compareByKana);
    expect(sorted.map((r) => r.code)).toEqual(['P010', 'P003', 'S002']);
  });

  it('ひらがな/カタカナ混在でも辞書順 (localeCompare ja)', () => {
    const rows = [
      { kana: 'たなか', code: 'B' },
      { kana: 'アオキ', code: 'A' },
    ];
    const sorted = [...rows].sort(compareByKana);
    expect(sorted.map((r) => r.code)).toEqual(['A', 'B']);
  });

  it('kana 未設定 (null/空) は末尾・同順はコードで安定', () => {
    const rows = [
      { kana: null, code: 'Z1' },
      { kana: 'アアア', code: 'C2' },
      { kana: '', code: 'Z0' },
      { kana: 'アアア', code: 'C1' },
    ];
    const sorted = [...rows].sort(compareByKana);
    expect(sorted.map((r) => r.code)).toEqual(['C1', 'C2', 'Z0', 'Z1']);
  });
});
