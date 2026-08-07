/**
 * 可動域マーク (2026-08-07 / PO 要望「うっすらでもいいので表示」).
 *
 * 背景:
 *   可動域 (PFV.movability) は「ピン留めのさらに先」にある固定手段で、'locked' なら
 *   提案系エンジン (改善提案 / 範囲最適化 / 詰まり解消 / プール一括投入) も自動割当も
 *   その枠を動かさない。ところが盤面には一切表示されておらず、
 *   「全件ピン解除して提案を出させるが、完全固定の枠は守られている」という運用状態が
 *   現場から見えなかった。
 *
 * 表示方針:
 *   - ピン留め (📌) より **主張させない**。淡色 (opacity-40) の 1 文字。
 *   - 絵文字ではなく漢字 1 文字。この寸法 (8px) では絵文字が潰れて判別できないため。
 *   - 日タイムラインと週タイムラインで同一の判定・同一の見た目を使う (本モジュール)。
 */
import type { Movability } from '@/lib/schemas/v2/patient_fixed_visit';

/** 可動域 → 表示マークと説明 (tooltip)。 */
const MOVABILITY_MARK: Record<string, { mark: string; title: string }> = {
  locked: { mark: '固', title: '可動域: 完全固定（提案も自動割当も動かしません）' },
  time_flexible: { mark: '時', title: '可動域: 同じ曜日内での時刻変更は可' },
  day_flexible: { mark: '曜', title: '可動域: 曜日の変更も可' },
};

/** 日/週タイムラインの visit が共通で持つ、判定に必要な最小形。 */
export interface MovabilityMarkSource {
  is_pinned?: boolean | null;
  movability?: Movability | null;
}

/**
 * 表示すべき可動域マークを返す (無ければ null)。以下は **出さない**
 * (盤面のノイズを増やさないため):
 *   - PFV 非紐付け (weekly_pattern 由来) … 可動域の概念が無い
 *   - 'unknown' (既定値) … 大多数がこれ
 *   - ピン留め済み … 📌 が既に「動かさない」を表しており二重表示になる
 *   - 未知の値 … BE が将来値を増やしても盤面を落とさない (寛容)
 */
export function movabilityMarkFor(
  visit: MovabilityMarkSource,
): { mark: string; title: string } | null {
  if (visit.is_pinned) return null;
  const key = visit.movability ?? null;
  if (key === null || key === 'unknown') return null;
  return MOVABILITY_MARK[key] ?? null;
}

/** 可動域マーク本体。表示対象でなければ何も描かない。 */
export function MovabilityMark({
  visit,
  testIdPrefix = 'tl',
  visitId,
}: {
  visit: MovabilityMarkSource;
  /** data-testid の接頭辞 ('tl' = 日タイムライン / 'wtl' = 週タイムライン)。 */
  testIdPrefix?: string;
  visitId: string;
}) {
  const conf = movabilityMarkFor(visit);
  if (!conf) return null;
  return (
    <span
      className="shrink-0 rounded-sm border border-current px-px text-[8px] font-bold leading-none opacity-40"
      title={conf.title}
      aria-label={conf.title}
      data-testid={`${testIdPrefix}-movability-${visitId}`}
    >
      {conf.mark}
    </span>
  );
}
