'use client';

/**
 * ScopeOptimizeDialog — 範囲最適化 (scope-optimization W1).
 *
 * 設計仕様書: `docs/plans/scope-optimization-design.md` §5.
 *
 * 管理者が範囲 (曜日 × コース。未選択 = 全体) を選んで「この範囲で計算」を押すと、
 * BE がその範囲内の move / swap を貪欲反復で積み上げた **手順列** と
 * **最適化前後の健康診断メトリクス** を返す (read-only)。
 *   a. 範囲選択 (曜日チップ / コースチップ。複数選択可・未選択 = 全部)
 *   b. 前後比較タイル 3 枚 (移動時間 / 隙間時間 / 移動距離。減少 = success)
 *   c. 手順カード列 (ImprovementSuggestionCard を表示専用 canEdit=false で流用)
 *   d. excluded_summary (N-6「黙って消さない」) と truncated 注記
 *
 * W1 は simulate 表示のみ (ワンクリック適用は W2)。手順は患者詳細の改善提案から
 * 個別採用するか、固定枠パネルで手動反映する運用。
 *
 * デザイン: Warm & Human トークンのみ。数字は tabular-nums。
 * RBAC: 呼出側 (CourseDayTablePanel) で admin/manager ガード済み。
 */
import * as React from 'react';
import { Loader2, Route } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { FilterChip } from '@/components/ui/filter-chip';
import { useScopeOptimizationSimulate } from '@/lib/queries/scopeOptimization';
import type {
  ScopeOptimizationExcludedSummary,
  ScopeOptimizationMetrics,
  ScopeOptimizationSimulateResponse,
} from '@/lib/schemas/v2/scopeOptimization';

import { ImprovementSuggestionCard } from './ImprovementSuggestionCard';

// ─────────────────────────────────────────────────────────────────────────
// Props / Constants
// ─────────────────────────────────────────────────────────────────────────

export interface ScopeOptimizeDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** null = 全拠点モード (範囲最適化は単一拠点前提のため案内を出す). */
  officeId: string | null;
  /** ヘッダー表示用の週ラベル (例: "2026-W27"). */
  weekLabel: string;
}

/** 0=月..5=土 (日曜は稼働曜日外: 健康診断と同じ). */
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
const SELECTABLE_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;

/** コース候補 (A-E + M=overflow)。存在しないコースを選んでも 0 手 200 で無害. */
const SELECTABLE_COURSES = ['A', 'B', 'C', 'D', 'E', 'M'] as const;

// ─────────────────────────────────────────────────────────────────────────
// 表示ヘルパー
// ─────────────────────────────────────────────────────────────────────────

function fmtMinutes(min: number): string {
  return `${Math.round(min)}分`;
}

function fmtKm(km: number): string {
  return `${(Math.round(km * 10) / 10).toFixed(1)}km`;
}

interface DeltaDisplay {
  text: string;
  toneClass: string;
}

/** 前後 delta (after − before)。減少 = success / 増加 = warning / ±0 = muted. */
function formatDelta(before: number, after: number, unit: '分' | 'km'): DeltaDisplay {
  const raw = after - before;
  const rounded = unit === 'km' ? Math.round(raw * 10) / 10 : Math.round(raw);
  const toneClass = rounded < 0 ? 'text-success' : rounded > 0 ? 'text-warning' : 'text-text-muted';
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '±';
  const abs = Math.abs(rounded);
  const value = unit === 'km' ? abs.toFixed(1) : String(abs);
  return { text: `${sign}${value}${unit}`, toneClass };
}

/** excluded_summary → 「ピン留めN件・…」の内訳テキスト (0 は省略). */
function summarizeExcluded(ex: ScopeOptimizationExcludedSummary): string[] {
  const parts: Array<[number, string]> = [
    [ex.pinned, `ピン留め${ex.pinned}件`],
    [ex.locked, `可動域が完全固定${ex.locked}件`],
    [ex.dismissed, `却下済み${ex.dismissed}件`],
    [ex.confirmation_required_excluded, `要確認のため除外${ex.confirmation_required_excluded}件`],
    [ex.no_current_visit, `固定枠と対応不明${ex.no_current_visit}件`],
  ];
  return parts.filter(([n]) => n > 0).map(([, label]) => label);
}

// ─────────────────────────────────────────────────────────────────────────
// 前後比較タイル
// ─────────────────────────────────────────────────────────────────────────

function BeforeAfterTile({
  label,
  before,
  after,
  unit,
  testId,
}: {
  label: string;
  before: number;
  after: number;
  unit: '分' | 'km';
  testId: string;
}) {
  const delta = formatDelta(before, after, unit);
  const fmt = unit === 'km' ? fmtKm : fmtMinutes;
  return (
    <div className="rounded-lg border border-border-default bg-bg-base p-3" data-testid={testId}>
      <div className="text-xs text-text-muted">{label}</div>
      <div className="mt-1 flex items-baseline gap-1.5 tabular-nums">
        <span className="text-sm text-text-muted line-through">{fmt(before)}</span>
        <span aria-hidden>→</span>
        <span className="text-xl font-semibold text-text-primary">{fmt(after)}</span>
      </div>
      <div
        className={`mt-1 text-xs tabular-nums ${delta.toneClass}`}
        data-testid={`${testId}-delta`}
      >
        {delta.text}
      </div>
    </div>
  );
}

function MetricsTiles({
  before,
  after,
}: {
  before: ScopeOptimizationMetrics;
  after: ScopeOptimizationMetrics;
}) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <BeforeAfterTile
        label="移動時間 合計"
        before={before.travel_minutes}
        after={after.travel_minutes}
        unit="分"
        testId="scope-optimize-tile-travel"
      />
      <BeforeAfterTile
        label="隙間時間 合計"
        before={before.gap_minutes}
        after={after.gap_minutes}
        unit="分"
        testId="scope-optimize-tile-gap"
      />
      <BeforeAfterTile
        label="移動距離 合計"
        before={before.travel_km}
        after={after.travel_km}
        unit="km"
        testId="scope-optimize-tile-km"
      />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_FULL = ['月', '火', '水', '木', '金', '土', '日'] as const;

export function ScopeOptimizeDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
  weekLabel,
}: ScopeOptimizeDialogProps) {
  // 未選択 = 全部 (チップの「全曜日」「全コース」は選択クリアと同義).
  const [weekdaySel, setWeekdaySel] = React.useState<Set<number>>(new Set());
  const [courseSel, setCourseSel] = React.useState<Set<string>>(new Set());
  const simulateMut = useScopeOptimizationSimulate();
  const [result, setResult] = React.useState<ScopeOptimizationSimulateResponse | null>(null);

  // 開くたびにまっさらな状態から始める (前回の範囲・結果を持ち越さない).
  React.useEffect(() => {
    if (open) {
      setWeekdaySel(new Set());
      setCourseSel(new Set());
      setResult(null);
      simulateMut.reset();
    }
    // simulateMut は毎レンダーで新オブジェクトのため依存に含めない (reset のみ).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const toggleWeekday = (wd: number) => {
    setWeekdaySel((prev) => {
      const next = new Set(prev);
      if (next.has(wd)) next.delete(wd);
      else next.add(wd);
      return next;
    });
  };
  const toggleCourse = (code: string) => {
    setCourseSel((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  const handleSimulate = () => {
    if (!officeId || simulateMut.isPending) return;
    setResult(null);
    simulateMut.mutate(
      {
        iso_year: isoYear,
        iso_week: isoWeek,
        scope: {
          office_id: officeId,
          weekdays: weekdaySel.size > 0 ? [...weekdaySel].sort((a, b) => a - b) : null,
          course_codes: courseSel.size > 0 ? [...courseSel].sort() : null,
        },
      },
      { onSuccess: setResult },
    );
  };

  const excludedParts = result ? summarizeExcluded(result.excluded_summary) : [];
  const scopeLabel = [
    weekdaySel.size > 0
      ? [...weekdaySel]
          .sort((a, b) => a - b)
          .map((wd) => WEEKDAY_FULL[wd])
          .join('・')
      : '全曜日',
    courseSel.size > 0 ? `${[...courseSel].sort().join('・')}コース` : '全コース',
  ].join(' / ');

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent
        className="max-h-[90vh] max-w-2xl overflow-y-auto"
        data-testid="scope-optimize-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Route className="h-5 w-5 text-brand-primary" aria-hidden />
            範囲最適化
            <span className="tabular-nums text-sm font-normal text-text-muted">{weekLabel}</span>
          </DialogTitle>
          <DialogDescription>
            範囲を選んで計算すると、その範囲の中だけで「どの枠をどう動かすとどれだけ移動が減るか」を
            手順として提案します。ピン留め・完全固定・却下済みの枠は動かしません。
          </DialogDescription>
        </DialogHeader>

        {!officeId ? (
          <div
            className="py-8 text-center text-sm text-text-muted"
            data-testid="scope-optimize-no-office"
          >
            範囲最適化は拠点単位で行います。左上の拠点フィルタで拠点を選択してください。
          </div>
        ) : (
          <div className="space-y-4 py-1">
            {/* a. 範囲選択 */}
            <div className="space-y-2">
              <div
                className="flex flex-wrap items-center gap-2"
                data-testid="scope-optimize-weekday-filter"
              >
                <span className="w-14 text-xs text-text-muted">曜日</span>
                <FilterChip active={weekdaySel.size === 0} onClick={() => setWeekdaySel(new Set())}>
                  全曜日
                </FilterChip>
                {SELECTABLE_WEEKDAYS.map((wd) => (
                  <FilterChip
                    key={wd}
                    active={weekdaySel.has(wd)}
                    onClick={() => toggleWeekday(wd)}
                  >
                    {WEEKDAY_LABELS[wd]}
                  </FilterChip>
                ))}
              </div>
              <div
                className="flex flex-wrap items-center gap-2"
                data-testid="scope-optimize-course-filter"
              >
                <span className="w-14 text-xs text-text-muted">コース</span>
                <FilterChip active={courseSel.size === 0} onClick={() => setCourseSel(new Set())}>
                  全コース
                </FilterChip>
                {SELECTABLE_COURSES.map((code) => (
                  <FilterChip
                    key={code}
                    active={courseSel.has(code)}
                    onClick={() => toggleCourse(code)}
                  >
                    {code}
                  </FilterChip>
                ))}
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-text-muted" data-testid="scope-optimize-scope-label">
                  対象: {scopeLabel}
                </span>
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSimulate}
                  disabled={simulateMut.isPending}
                  data-testid="scope-optimize-run-button"
                >
                  {simulateMut.isPending ? (
                    <>
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                      計算中…
                    </>
                  ) : (
                    'この範囲で計算'
                  )}
                </Button>
              </div>
            </div>

            {/* エラー */}
            {simulateMut.isError ? (
              <div
                className="py-4 text-center text-sm text-error"
                data-testid="scope-optimize-error"
              >
                計算に失敗しました。時間をおいて再度お試しください。
              </div>
            ) : null}

            {/* b-d. 結果 */}
            {result ? (
              result.steps.length === 0 ? (
                <div
                  className="rounded border border-border-default bg-bg-muted px-3 py-4 text-sm text-text-secondary"
                  data-testid="scope-optimize-empty"
                >
                  この範囲では、確認不要で 10 分/週以上短縮できる変更は見つかりませんでした。
                  {excludedParts.length > 0 ? (
                    <span className="mt-1 block text-xs text-text-muted">
                      対象外: {excludedParts.join('・')}
                    </span>
                  ) : null}
                </div>
              ) : (
                <div className="space-y-4" data-testid="scope-optimize-result">
                  <MetricsTiles before={result.before} after={result.after} />

                  <div className="space-y-2" data-testid="scope-optimize-steps">
                    <div className="text-sm font-semibold text-text-primary">
                      提案手順（上から順に適用する前提の
                      <span className="tabular-nums">{result.steps.length}</span>手）
                    </div>
                    {result.steps.map((step) => (
                      <div key={step.seq} className="space-y-1">
                        <div className="flex items-baseline justify-between gap-2 text-xs">
                          <span className="font-medium text-text-primary">
                            手順{step.seq}: {step.patient_name} 様
                          </span>
                          <span className="tabular-nums text-text-muted">
                            ここまでの累積 −{step.cumulative_delta_minutes}分/週
                          </span>
                        </div>
                        {/* 表示専用 (W1): canEdit=false で採用/見送りボタンは出ない. */}
                        <ImprovementSuggestionCard
                          suggestion={step.suggestion}
                          canEdit={false}
                          patientName={step.patient_name}
                          onAdopt={() => undefined}
                          onDismiss={() => undefined}
                        />
                      </div>
                    ))}
                  </div>

                  {result.excluded_summary.truncated ? (
                    <div className="text-xs text-warning" data-testid="scope-optimize-truncated">
                      手順数が上限に達したため打ち切りました。適用後に再計算するとさらに提案が出る場合があります。
                    </div>
                  ) : null}
                  {excludedParts.length > 0 ? (
                    <div className="text-xs text-text-muted" data-testid="scope-optimize-excluded">
                      対象外: {excludedParts.join('・')}
                    </div>
                  ) : null}
                  <div className="text-xs text-text-muted" data-testid="scope-optimize-w1-note">
                    ※ この画面は提案の確認用です。反映は患者詳細の「改善提案」から採用するか、
                    固定枠パネルで変更してください（ワンクリック適用は次の更新で提供予定）。
                  </div>
                </div>
              )
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
