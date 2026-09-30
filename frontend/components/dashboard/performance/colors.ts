/**
 * スタッフ別の実績のグラフの色 = styles/tokens.css の --chart-* (状態色は使わない)。
 *
 * 主役 (この人) = 青、実績 (QR) = 赤紫、チーム平均 = 濃い灰の破線 (主役でない文脈の線)。
 * 1 日の内訳は 訪問 = 主役の青 / 移動 = 橙 / 会議・研修など = 紫 / 合間 = 中立の灰。
 */
export const PERF_COLORS = {
  self: 'var(--chart-self, #2a78d6)',
  selfSoft: 'var(--chart-self-soft, #cde2fb)',
  actual: 'var(--chart-actual, #c9457a)',
  team: 'var(--chart-team, #6b665f)',
  travel: 'var(--chart-travel, #eb6834)',
  meeting: 'var(--chart-meeting, #4a3aa7)',
  idle: 'var(--chart-idle, #8a847d)',
  grid: 'var(--chart-grid, #efece7)',
  axis: 'var(--text-secondary, #57534e)',
} as const;
