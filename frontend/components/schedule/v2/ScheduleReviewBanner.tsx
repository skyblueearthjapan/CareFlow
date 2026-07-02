'use client';

/**
 * ScheduleReviewBanner — 見直しどきバナー (Schedule Advisor Phase 3「見直しどき通知」).
 *
 * クライアントの課題「半年経つとスケジュールが劣化する。見直しどきに気づけない」に対し、
 * 過去週の実 Visit から算出したトレンド (useScheduleHealthTrend) を見て、移動時間の
 * 悪化を検知したときだけスケジュール画面に控えめなバナーを出す.
 *
 * 判定は **FE 側** (現場フィードバックで閾値・文言を回しやすくするため). BE は素データのみ.
 * 設計仕様書: ``docs/plans/schedule-advisor-design.md`` §3 Phase 3.
 */
import { useEffect, useState } from 'react';
import { TriangleAlert } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useScheduleHealthTrend } from '@/lib/queries/scheduleHealth';
import type { ScheduleHealthTrendWeek } from '@/lib/schemas/v2/scheduleHealth';

// --- 劣化判定パラメータ (named 定数: 現場フィードバックで調整する対象) ---
/** トレンドで遡る週数. 直近ブロック + 前ブロック = 4 + 4. */
export const TREND_WEEKS = 8;
/** 直近ブロック / 前ブロックの週数 (各 4 週 = 約1ヶ月). */
const BLOCK_WEEKS = 4;
/** 直近ブロックの travel_minutes 合計が前ブロック比で +20% 以上なら「見直しどき」. */
const TRAVEL_INCREASE_THRESHOLD = 0.2;
/**
 * データ薄週の誤検知防止: 8週のうち visit_count=0 の週が 3週以上あれば判定しない.
 * (欠測が多い期間はトレンドが不安定なため).
 */
const MAX_EMPTY_WEEKS_ALLOWED = 2;

export interface ReviewEvaluation {
  /** バナーを出すべきか. */
  shouldShow: boolean;
  /** 移動時間の増加率 (%). shouldShow=false のときは 0. */
  increasePct: number;
}

/**
 * トレンド週配列 (古→新順) から見直しどき判定を行う純関数.
 *
 * 条件 (すべて成立でのみ shouldShow=true):
 *   1. 週数が {@link TREND_WEEKS} 以上ある (直近4 + 前4 が揃う).
 *   2. visit_count=0 の週が {@link MAX_EMPTY_WEEKS_ALLOWED} 以下 (= 3週未満).
 *   3. 直近週の visit_count > 0 (今週が動いている).
 *   4. 前ブロックの travel_minutes 合計 > 0 (比較の分母がある).
 *   5. (直近4週 travel_minutes 合計) が (前4週合計) 比で +20% 以上.
 */
export function evaluateScheduleReview(weeks: ScheduleHealthTrendWeek[]): ReviewEvaluation {
  const none: ReviewEvaluation = { shouldShow: false, increasePct: 0 };
  if (weeks.length < TREND_WEEKS) return none;

  // 直近 TREND_WEEKS 週のみを対象にする (古→新順の末尾).
  const recentSpan = weeks.slice(-TREND_WEEKS);

  const emptyWeeks = recentSpan.filter((w) => w.totals.visit_count === 0).length;
  if (emptyWeeks > MAX_EMPTY_WEEKS_ALLOWED) return none;

  const latest = recentSpan[recentSpan.length - 1];
  if (!latest || latest.totals.visit_count <= 0) return none;

  const priorBlock = recentSpan.slice(0, BLOCK_WEEKS);
  const recentBlock = recentSpan.slice(BLOCK_WEEKS);
  const priorTravel = priorBlock.reduce((s, w) => s + w.totals.travel_minutes, 0);
  const recentTravel = recentBlock.reduce((s, w) => s + w.totals.travel_minutes, 0);
  if (priorTravel <= 0) return none;

  const increase = (recentTravel - priorTravel) / priorTravel;
  if (increase < TRAVEL_INCREASE_THRESHOLD) return none;

  return { shouldShow: true, increasePct: Math.round(increase * 100) };
}

/**
 * 「今週は非表示」の localStorage キー.
 *
 * (officeId, isoYear, isoWeek) の組み合わせをキーにすることで、
 * 翌年の同一週番号 (例: 2027-W20) まで非表示が継続しないようにする.
 */
export function reviewDismissKey(
  officeId: string | null | undefined,
  isoYear: number,
  isoWeek: number,
): string {
  return `careflow.scheduleReviewDismissed.${officeId ?? 'all'}.${isoYear}.${isoWeek}`;
}

export interface ScheduleReviewBannerProps {
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
  /** [健康診断を開く] 押下時. 既存 scheduleHealthOpen を開く. */
  onOpenHealth: () => void;
}

/**
 * 見直しどきバナー. 条件成立時のみ描画する. 非成立 / 非表示済み / データ不足では null.
 */
export function ScheduleReviewBanner({
  isoYear,
  isoWeek,
  officeId,
  onOpenHealth,
}: ScheduleReviewBannerProps) {
  const { data } = useScheduleHealthTrend({ isoYear, isoWeek, weeks: TREND_WEEKS, officeId });

  // 「今週は非表示」の記憶を mount 後に読む (SSR で localStorage 不在のため).
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    setDismissed(
      window.localStorage.getItem(reviewDismissKey(officeId, isoYear, isoWeek)) === '1',
    );
  }, [officeId, isoYear, isoWeek]);

  if (dismissed) return null;

  const evaluation = evaluateScheduleReview(data?.weeks ?? []);
  if (!evaluation.shouldShow) return null;

  const handleDismiss = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(reviewDismissKey(officeId, isoYear, isoWeek), '1');
    }
    setDismissed(true);
  };

  return (
    <div
      role="status"
      data-testid="schedule-review-banner"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border-warning bg-warning-bg px-3 py-2 text-sm text-warning-strong"
    >
      <TriangleAlert className="h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1 leading-relaxed">
        直近1ヶ月の移動時間が以前より +{evaluation.increasePct}%
        増えています。スケジュールの見直しどきかもしれません
      </span>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onOpenHealth}
          data-testid="schedule-review-open-health"
        >
          健康診断を開く
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={handleDismiss}
          data-testid="schedule-review-dismiss"
        >
          今週は非表示
        </Button>
      </div>
    </div>
  );
}
