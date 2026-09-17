/**
 * 音声記録の費用表示に使う固定為替レート（設計 §11-3「月額円換算は固定レート設定」）。
 *
 * BE は `cost_usd`（USD）しか返さない。現場・PO が見るのは円なので FE で換算するが、
 * 実勢レートは取りに行かない — 会計の正ではなく「だいたいいくら掛かっているか」を
 * 一目で掴むための数字だから。値を動かすとカードの円表示が全部動くので、
 * 変えるときは PO 合意のうえで**この 1 箇所だけ**を書き換える。
 */

/** 1 USD = 150 円（概算・固定）。 */
export const USD_JPY = 150;

/** USD を数値に寄せる（BE は Decimal を文字列で返すことがある）。 */
function toUsdNumber(cost: number | string | null | undefined): number {
  if (cost == null) return 0;
  const n = typeof cost === 'number' ? cost : Number(cost);
  return Number.isFinite(n) ? n : 0;
}

/**
 * `$0.1234` 形式（小数 4 桁）。
 *
 * 1 件あたり $0.003 程度なので、スタッフ別などの**明細**は 4 桁でないと
 * 全部 `$0.00` に潰れて比較できない。
 */
export function formatUsd(cost: number | string | null | undefined): string {
  return `$${toUsdNumber(cost).toFixed(4)}`;
}

/** 月の**合計**は通貨として読む数字なので 2 桁（`$1.23`）。 */
export function formatUsdTotal(cost: number | string | null | undefined): string {
  return `$${toUsdNumber(cost).toFixed(2)}`;
}

/** 固定レートの円換算（概算・1 円未満は丸める）。 */
export function formatJpyApprox(cost: number | string | null | undefined): string {
  return `約 ${Math.round(toUsdNumber(cost) * USD_JPY).toLocaleString('ja-JP')} 円`;
}
