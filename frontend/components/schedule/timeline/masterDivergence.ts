/**
 * 型 (固定訪問スケジュール) とのズレ表示 (2026-08-08 / PO 決定).
 *
 * 背景:
 *   今週の実配置が型と違う時刻になっていることがある。1 週 117 訪問のうち 2 件
 *   (1.7%) で、いずれも自動割当 (移動時間補正) が動かしたもの。ズレていると
 *   赤ピン (型のピン) が刺せないが、現場にはその理由が見えていなかった。
 *
 * PO 方針:
 *   「ズレていることを伝えるだけでよい。どうするかはユーザーに委ねる」
 *   → 合わせ直す導線は設けない。件数のサマリ表示も作らない。
 *
 * 表示 (案 A + 案 B の併用):
 *   - 案 A: カード左端の帯を警告色にする。文字を足さないので最小のカードでも見える。
 *   - 案 B: 時刻行に「（型 HH:MM）」を併記する。本来何時なのかが分かる
 *          (2 時間半ズレている例もあり、ズレの有無だけでは判断できないため)。
 *   ズレは 1.7% の例外事象なので、全カードに印を足す作りにはしない。
 */

/** 日/週タイムラインの visit が共通で持つ、ズレ判定に必要な最小形。 */
export interface MasterDivergenceSource {
  /** 型が存在し、かつ開始時刻が今週の実配置と一致しないときだけ 'HH:MM'。 */
  master_start_time?: string | null;
}

/** 型とズレているか。型が無い (希望由来) / 一致している場合は false。 */
export function isDivergedFromMaster(visit: MasterDivergenceSource): boolean {
  const t = visit.master_start_time;
  return typeof t === 'string' && t.length > 0;
}

/**
 * 案 B: 時刻行に併記する補足文字列。ズレていなければ null。
 * 例: '（型 13:00）'
 */
export function masterTimeSuffix(visit: MasterDivergenceSource): string | null {
  return isDivergedFromMaster(visit) ? `（型 ${visit.master_start_time}）` : null;
}

/**
 * カード全体に付ける説明 (title 属性)。ズレていなければ null。
 * 完全固定にできない理由もここで説明する (論点 1: 合わせる導線は設けず、伝えるだけ)。
 */
export function masterDivergenceTitle(visit: MasterDivergenceSource): string | null {
  if (!isDivergedFromMaster(visit)) return null;
  return (
    `固定訪問スケジュールは ${visit.master_start_time} です。` +
    'この時間帯では完全固定にできません'
  );
}

/**
 * 案 A: カード左端の帯に重ねるスタイル。ズレていなければ null。
 *
 * 既存の左端 3px 帯 (性別色) を上書きせず、**破線の外枠**で異質さを出す。
 * 色だけに依存しないよう線種でも差を付ける (色覚特性への配慮)。
 */
export function masterDivergenceCardStyle(
  visit: MasterDivergenceSource,
): { borderLeftStyle: 'dashed'; borderLeftColor: string } | null {
  if (!isDivergedFromMaster(visit)) return null;
  return { borderLeftStyle: 'dashed', borderLeftColor: 'var(--warning)' };
}
