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

/**
 * スタッフコード順 (職員スケジュールの既定の並び・PO 要望 2026-08-23)。
 *   - コードは "S007" のような英字+数字を想定。数値部分は数値として比較
 *     (S2 < S10) し、英字部分は辞書順。
 *   - コード未設定は末尾、同順位は氏名 (ja) で安定化。
 */
export interface CodeSortable {
  code?: string | null;
  name?: string | null;
}

export function compareByStaffCode(a: CodeSortable, b: CodeSortable): number {
  const ac = (a.code ?? '').trim();
  const bc = (b.code ?? '').trim();
  if (!ac && !bc) return (a.name ?? '').localeCompare(b.name ?? '', 'ja');
  if (!ac) return 1;
  if (!bc) return -1;
  const c = ac.localeCompare(bc, 'ja', { numeric: true, sensitivity: 'base' });
  return c !== 0 ? c : (a.name ?? '').localeCompare(b.name ?? '', 'ja');
}
