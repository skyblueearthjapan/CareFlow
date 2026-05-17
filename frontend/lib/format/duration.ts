/**
 * 実動時間 (分) を人間可読の日本語ラベルに変換するヘルパー.
 *
 * 仕様 (2026-W20 /schedule UX 改修):
 *   - 0 分 → "0 分"
 *   - N < 60 → "{N} 分" (例: 30 → "30 分", 45 → "45 分")
 *   - N == 60*k (k >= 1) → "{k} 時間" (例: 60 → "1 時間", 120 → "2 時間")
 *   - N > 60 で余りあり → "{k} 時間 {余り} 分"
 *     (例: 75 → "1 時間 15 分", 90 → "1 時間 30 分")
 *
 * 負値 / 非数値は "0 分" にフォールバックする (UI 表示の安全側).
 *
 * 用途:
 *   - /schedule 月-土タブ Before/After リスト表示の visit 実動時間
 *   - 患者詳細ダイアログの duration ラベル等
 */
export function formatDuration(durationMin: number): string {
  if (!Number.isFinite(durationMin) || durationMin <= 0) {
    return '0 分';
  }
  const n = Math.floor(durationMin);
  if (n < 60) {
    return `${n} 分`;
  }
  const hours = Math.floor(n / 60);
  const remainder = n % 60;
  if (remainder === 0) {
    return `${hours} 時間`;
  }
  return `${hours} 時間 ${remainder} 分`;
}
