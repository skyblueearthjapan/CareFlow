/**
 * スタッフ別の実績のグラフの色 = styles/tokens.css の --chart-* (状態色は使わない)。
 *
 * 主役 (この人) = らく助ピンク、実績 (QR) = 青、チーム平均 = 濃い灰の破線 (主役でない文脈の線)。
 * 1 日の内訳は 訪問 = 主役のピンク / 移動 = らく助の橙 / 会議・研修など = 紫 / 合間 = 中立の灰。
 */
export const PERF_COLORS = {
  self: 'var(--chart-self, #d6457a)',
  selfSoft: 'var(--chart-self-soft, #f9d5e1)',
  actual: 'var(--chart-actual, #2a78d6)',
  team: 'var(--chart-team, #6b665f)',
  travel: 'var(--chart-travel, #d97706)',
  meeting: 'var(--chart-meeting, #4a3aa7)',
  idle: 'var(--chart-idle, #8a847d)',
  grid: 'var(--chart-grid, #efece7)',
  axis: 'var(--text-secondary, #57534e)',
} as const;
