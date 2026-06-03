/**
 * CareFlow Mobile — 現場ボード Warm & Human パレット (Phase2-3c)
 *
 * 旧 `mockData.ts` のうち**実データ接続後も使う意匠定義のみ**を切り出したもの。
 * 患者 / 週ボード / 提案ランキングのモックデータは実 API 接続により撤去済み。
 *
 * - `CF_THEME`  : 配色トークン (teal/terra/plum/mint + ink/cream/line)
 * - `CF_COURSE` : コース色 (A〜M。muted on cream)
 * - `courseColorKey` / `cc` : course_code → 色キー解決 (A..M / M2.. → 色)
 */

// ── 型定義 ──────────────────────────────────────────────────────────────────

export type InsuranceKind = 'med' | 'care';
export type OfficeKey = 'INAGE' | 'TSUGA';
export type Dow = '月' | '火' | '水' | '木' | '金' | '土' | '日';

export type CourseColorKey = 'A' | 'B' | 'C' | 'D' | 'E' | 'M';

// ── Warm & Human パレット ───────────────────────────────────────────────────

export const CF_THEME = {
  TEAL: '#0D9488',
  TEAL_DEEP: '#0F766E',
  TERRA: '#D97706',
  TERRA_DEEP: '#B45309',
  PLUM: '#8B5C9E',
  MINT: '#0E9F6E',
  INK: '#1C1917',
  INK2: '#57534E',
  INK3: '#A8A29E',
  CREAM: '#FAF7F2',
  LINE: '#EAE3D8',
  PANEL: '#FFFFFF',
  GOLD: '#C08A1E',
} as const;

/** コース色 (Warm-aligned, muted on cream) */
export const CF_COURSE: Record<CourseColorKey, { c: string; bg: string; soft: string }> = {
  A: { c: '#0D8478', bg: '#D7F2EE', soft: '#EBF9F7' },
  B: { c: '#2F6FB0', bg: '#E2EDF8', soft: '#F0F5FB' },
  C: { c: '#8B5C9E', bg: '#F0E7F5', soft: '#F7F2F9' },
  D: { c: '#C75C77', bg: '#FAE4E9', soft: '#FCEFF2' },
  E: { c: '#B0831F', bg: '#F7EDD4', soft: '#FBF5E6' },
  M: { c: '#D97706', bg: '#FCEBD6', soft: '#FDF4E8' },
};

/**
 * course_code (A..E / M.. / M2..M9 等) を安定的に色キー (A〜M) へ写像する。
 *
 * A〜E はそのまま。M 始まり (移動枠 / 臨時コース) はすべて M (テラコッタ) に寄せる。
 * それ以外の未知コードは先頭文字を A 起点でローテーションして色割当する
 * (実 API のコードに依存せず、視認できる色を必ず返す)。
 */
export function courseColorKey(courseCode: string): CourseColorKey {
  const head = (courseCode || '').trim().charAt(0).toUpperCase();
  if (head === 'A' || head === 'B' || head === 'C' || head === 'D' || head === 'E') {
    return head;
  }
  if (head === 'M') return 'M';
  // 未知コード: 先頭文字コードから A..E をローテーション割当。
  if (head >= 'F' && head <= 'Z') {
    const keys: CourseColorKey[] = ['A', 'B', 'C', 'D', 'E'];
    const idx = (head.charCodeAt(0) - 'F'.charCodeAt(0)) % keys.length;
    return keys[idx] ?? 'M';
  }
  return 'M';
}

/** course_code → 色トークン。未知は M (テラコッタ) にフォールバック。 */
export const cc = (courseCode: string): { c: string; bg: string; soft: string } =>
  CF_COURSE[courseColorKey(courseCode)] ?? CF_COURSE.M;

export const CF_DOWS: Dow[] = ['月', '火', '水', '木', '金', '土', '日'];
