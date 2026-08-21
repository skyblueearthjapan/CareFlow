/**
 * あいうえお順 (kana 昇順) の共有コンパレータ — 患者マスタ / スタッフマスタ用。
 *
 * 前提: 本番データの kana はカタカナ統一・全件登録済み (2026-08-21 確認:
 * 患者 102/102・スタッフ 7/7)。とはいえ防御的に:
 *   - kana 未設定 (null/空) は末尾へ
 *   - 同順位はコード順で安定化 (一覧の既定順と同じ第2キー)
 *   - localeCompare('ja') = ICU 照合。ひらがな/カタカナ混在・濁音・長音も
 *     辞書順として自然に並ぶ (コードポイント順より頑健)
 */

export type MasterSortOrder = 'code' | 'kana';

interface KanaSortable {
  kana?: string | null;
  code?: string | null;
}

export function compareByKana(a: KanaSortable, b: KanaSortable): number {
  const ak = (a.kana ?? '').trim();
  const bk = (b.kana ?? '').trim();
  if (!ak && !bk) return compareCode(a, b);
  if (!ak) return 1; // kana 無しは末尾
  if (!bk) return -1;
  const c = ak.localeCompare(bk, 'ja');
  return c !== 0 ? c : compareCode(a, b);
}

function compareCode(a: KanaSortable, b: KanaSortable): number {
  return (a.code ?? '').localeCompare(b.code ?? '', 'ja');
}
