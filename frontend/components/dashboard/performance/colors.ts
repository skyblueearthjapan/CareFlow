/**
 * スタッフ別の実績のグラフの色 (既存のトークンだけを使う・状態色は使わない)。
 *
 * 主役 (この人) = 青、実績 (QR) = 緑、チーム平均 = 灰 (主役でない文脈の線)。
 * 1 日の内訳は 訪問 = 主役の青 / 移動 = 橙 / 会議・研修など = 紫 / 合間 = 灰。
 */
export const PERF_COLORS = {
  self: 'var(--sched-ghost-before, #2f7fd1)',
  selfSoft: 'var(--sched-ghost-before-bg, #e8f1fb)',
  actual: 'var(--sched-event-bar, #2f9e63)',
  team: 'var(--text-muted, #a8a29e)',
  travel: 'var(--sched-now, #d2683c)',
  meeting: 'var(--sched-ghost-after, #7c5cd6)',
  idle: 'var(--border-strong, #d6d3d1)',
  grid: 'var(--border-subtle, #f0ede8)',
  axis: 'var(--text-secondary, #57534e)',
} as const;
