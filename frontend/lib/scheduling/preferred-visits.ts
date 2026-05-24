/**
 * preferred-visits — 「希望週訪問回数」 vs 「実 visit 数」 の共通 utility (Phase G-44).
 *
 * 設計 (= ディレクター指示):
 *   - 患者マスター 「希望訪問パターン」 (= weekly_pattern.frequency_per_week) を
 *     Source of Truth とする。
 *   - 当週 visit 数が希望週訪問回数に満たない患者は pool に残す (= 不足 1 件以上)。
 *   - pool 表示で「希望 N、配置 X、不足 Y」を可視化する。
 *
 * 使用箇所:
 *   - CourseDayTablePanel.tsx: poolPatients 判定 + Pool 表示の不足バッジ
 *   - FullOptimizeDialog.tsx:  全面最適化 popup の数値サマリ計算
 *
 * 共通契約:
 *   - 希望未設定 (weekly_pattern 無し or frequency_per_week=0) → desired=0 を返す。
 *   - desired=0 のとき pool 判定対象外 (= shortage=0 で返す)。
 *   - 実 visit カウントは visit_date 非 null のもののみ.
 */
import { coerceWeeklyPattern, type PatientRead } from '@/lib/schemas/patient';

/**
 * 希望週訪問回数を取得する.
 *
 * weekly_pattern (= JSONB) は server から `Record<string, unknown> | null` で
 * 来るため、 coerceWeeklyPattern を経由して構造化する。
 *
 * - weekly_pattern null → 0
 * - frequency_per_week 0 / 無効値 → 0
 *
 * @example
 *   getDesiredWeeklyVisitCount({ weekly_pattern: { frequency_per_week: 3 } }) // → 3
 *   getDesiredWeeklyVisitCount({ weekly_pattern: null })                      // → 0
 */
export function getDesiredWeeklyVisitCount(patient: Pick<PatientRead, 'weekly_pattern'>): number {
  if (patient.weekly_pattern == null) return 0;
  const wp = coerceWeeklyPattern(patient.weekly_pattern);
  // coerceWeeklyPattern は frequency_per_week=null/undefined のとき 1 にデフォルトする.
  // ここでは 「希望未設定」 を区別したいので raw を直接見る.
  const raw = (patient.weekly_pattern as Record<string, unknown>).frequency_per_week;
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return 0;
  // coerceWeeklyPattern が 1..7 にクランプしているので max 7.
  return Math.min(7, Math.max(0, wp.frequency_per_week));
}

/**
 * 指定 patient_id の当週実 visit 数 (= visit_date 非 null) をカウントする.
 *
 * weekVisits は `useVisits` の `items` (= VisitRead[]) を想定するが、
 * 入力型を厳しくしないために最小限の `{ patient_id, visit_date }` 形だけ受ける.
 */
export function countWeekVisits(
  visits: ReadonlyArray<{ patient_id: string; visit_date?: string | null }>,
  patientId: string,
): number {
  let n = 0;
  for (const v of visits) {
    if (v.patient_id === patientId && v.visit_date != null && v.visit_date !== '') {
      n++;
    }
  }
  return n;
}

/**
 * 当該患者の「不足 visit 数」 (= 希望 − 配置済).
 *
 * - desired=0 (= 希望未設定) → 0 (= pool 対象外).
 * - actual >= desired → 0.
 * - それ以外 → desired - actual.
 */
export function getShortageCount(
  patient: Pick<PatientRead, 'weekly_pattern'>,
  visits: ReadonlyArray<{ patient_id: string; visit_date?: string | null }>,
  patientId: string,
): number {
  const desired = getDesiredWeeklyVisitCount(patient);
  if (desired === 0) return 0;
  const actual = countWeekVisits(visits, patientId);
  return Math.max(0, desired - actual);
}
