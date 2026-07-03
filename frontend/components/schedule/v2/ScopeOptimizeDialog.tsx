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
 * W2 (適用): 「先頭から N 手」のプレフィックス適用のみ。スライダーで N を選び、
 * 選択外の手は薄く表示する。apply は state_token 楽観ロック付き 1 TX (BE §4.2)。
 * 409 (simulate 以降に固定枠が変わった) は結果を破棄して再計算を促す。
 * 健康診断からの導線 (initialScope) では開いた直後にその範囲で自動計算する。
 *
 * デザイン: Warm & Human トークンのみ。数字は tabular-nums。
 * RBAC: ボタン表示は呼出側 (CourseDayTablePanel) で admin/manager ガード済み。
 * 適用ボタンは canEdit のときのみ表示 (BE でも 403 担保)。
 */
import * as React from 'react';
import { Loader2, Route } from 'lucide-react';
import { toast } from 'sonner';

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
import { ApiError } from '@/lib/api-client';
import {
  useScopeOptimizationApply,
  useScopeOptimizationSimulate,
} from '@/lib/queries/scopeOptimization';
import type {
  ScopeCourseSnapshot,
  ScopeOptimizationExcludedSummary,
  ScopeOptimizationMetrics,
  ScopeOptimizationSimulateResponse,
  ScopeOptimizationStep,
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
  /** admin / manager のみ適用可 (RBAC; BE でも担保). */
  canEdit: boolean;
  /**
   * 健康診断など外部からの導線用の範囲プリセット。指定されていると、開いた直後に
   * その範囲で自動計算する (null 要素 = 全部)。
   */
  initialScope?: { weekdays: number[] | null; courseCodes: string[] | null } | null;
  /**
   * 拠点一覧 (全拠点モードでダイアログ内から拠点を選ぶためのチップ用)。
   * officeId が指定されているときは使わない。
   */
  offices?: { id: string; name: string }[];
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
// 手順のコースタイムライン (W3)
//   同一コース内の手 = 1 枚のタイムラインで「元の位置 (打消し) → ここへ移動 (強調)」。
//   別コースへの手 = 移動元 / 移動先の 2 枚を並列表示して比較できるようにする。
//   swap は双方の抜き差しを両パネルに描く。
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_FULL = ['月', '火', '水', '木', '金', '土', '日'] as const;

function hhmmToMin(t: string): number {
  const [h, m] = t.slice(0, 5).split(':').map(Number);
  return (h ?? 0) * 60 + (m ?? 0);
}

function addMinutes(t: string, minutes: number): string {
  const total = hhmmToMin(t) + minutes;
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

interface TimelineRow {
  key: string;
  name: string;
  start: string; // HH:MM
  end: string; // HH:MM
  kind: 'normal' | 'out' | 'in';
}

function TimelinePanel({
  title,
  rows,
  testId,
}: {
  title: string;
  rows: TimelineRow[];
  testId: string;
}) {
  const sorted = [...rows].sort(
    (a, b) => hhmmToMin(a.start) - hhmmToMin(b.start) || (a.kind === 'in' ? 1 : -1),
  );
  return (
    <div
      className="min-w-0 rounded border border-border-default bg-bg-base p-2"
      data-testid={testId}
    >
      <div className="mb-1 text-[11px] font-medium text-text-secondary">{title}</div>
      <div className="space-y-0.5">
        {sorted.map((row) => (
          <div
            key={row.key}
            className={
              row.kind === 'in'
                ? 'flex items-center gap-1.5 rounded border-l-2 border-brand-primary bg-bg-muted px-1.5 py-0.5 text-[11px] font-medium text-text-primary'
                : row.kind === 'out'
                  ? 'flex items-center gap-1.5 px-1.5 py-0.5 text-[11px] text-text-muted line-through'
                  : 'flex items-center gap-1.5 px-1.5 py-0.5 text-[11px] text-text-primary'
            }
            data-kind={row.kind}
          >
            <span className="tabular-nums">
              {row.start}–{row.end}
            </span>
            <span className="truncate">{row.name} 様</span>
            {row.kind === 'in' ? (
              <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] text-brand-primary">
                ← ここへ移動
              </span>
            ) : row.kind === 'out' ? (
              <span className="ml-auto shrink-0 whitespace-nowrap text-[10px] no-underline">
                移動元
              </span>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

/** スナップショットから patient の所要分数を引く (見つからなければ fallback). */
function durationOf(snapshot: ScopeCourseSnapshot, patientId: string, fallback: number): number {
  const v = snapshot.visits.find((x) => x.patient_id === patientId);
  if (!v) return fallback;
  return Math.max(0, hhmmToMin(v.end_time) - hhmmToMin(v.start_time));
}

function StepCourseTimelines({ step }: { step: ScopeOptimizationStep }) {
  const sug = step.suggestion;
  const src = step.source_course;
  if (!src) return null; // 旧 BE レスポンス互換: スナップショットが無ければ出さない.
  const dst = step.destination_course;
  const cp = sug.swap_counterpart;

  // X (視点主) の新枠.
  const xIn: TimelineRow = {
    key: 'in-x',
    name: step.patient_name,
    start: sug.candidate.start_time.slice(0, 5),
    end: sug.candidate.end_time.slice(0, 5),
    kind: 'in',
  };
  // Y (swap 相手) の新枠 = X の旧コースへ。end はスナップショットの所要分数から導出.
  const yIn: TimelineRow | null = cp
    ? {
        key: 'in-y',
        name: cp.patient_name,
        start: cp.new_start_time.slice(0, 5),
        end: addMinutes(cp.new_start_time.slice(0, 5), durationOf(dst ?? src, cp.patient_id, 30)),
        kind: 'in',
      }
    : null;

  const srcRows: TimelineRow[] = src.visits.map((v) => ({
    key: `s-${v.patient_id}-${v.start_time}`,
    name: v.patient_name,
    start: v.start_time.slice(0, 5),
    end: v.end_time.slice(0, 5),
    kind:
      v.patient_id === step.patient_id || (!dst && cp && v.patient_id === cp.patient_id)
        ? 'out'
        : 'normal',
  }));

  if (!dst) {
    // 同一コース内: 1 枚に out (旧位置) と in (新位置) を同居させる.
    srcRows.push(xIn);
    if (yIn) srcRows.push(yIn);
    return (
      <div data-testid="scope-step-timeline-single">
        <TimelinePanel
          title={`コース内の動き（${src.course_label}${src.staff_name ? `・${src.staff_name}` : ''}）`}
          rows={srcRows}
          testId="scope-step-timeline-src"
        />
      </div>
    );
  }

  // 別コース: 移動元 / 移動先を並列表示.
  if (yIn) srcRows.push(yIn); // swap: Y は X の旧コース (src) へ入る.
  const dstRows: TimelineRow[] = dst.visits.map((v) => ({
    key: `d-${v.patient_id}-${v.start_time}`,
    name: v.patient_name,
    start: v.start_time.slice(0, 5),
    end: v.end_time.slice(0, 5),
    kind: cp && v.patient_id === cp.patient_id ? 'out' : 'normal',
  }));
  dstRows.push(xIn);
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2" data-testid="scope-step-timeline-pair">
      <TimelinePanel
        title={`移動元（${src.course_label}${src.staff_name ? `・${src.staff_name}` : ''}）`}
        rows={srcRows}
        testId="scope-step-timeline-src"
      />
      <TimelinePanel
        title={`移動先（${dst.course_label}${dst.staff_name ? `・${dst.staff_name}` : ''}）`}
        rows={dstRows}
        testId="scope-step-timeline-dst"
      />
    </div>
  );
}

export function ScopeOptimizeDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
  weekLabel,
  canEdit,
  initialScope = null,
  offices = [],
}: ScopeOptimizeDialogProps) {
  // 全拠点モード (officeId=null) でダイアログ内から選ぶ拠点.
  const [manualOfficeId, setManualOfficeId] = React.useState<string | null>(null);
  // 実効拠点: ページ側フィルタ優先、無ければダイアログ内選択.
  const effectiveOfficeId = officeId ?? manualOfficeId;
  // 未選択 = 全部 (チップの「全曜日」「全コース」は選択クリアと同義).
  const [weekdaySel, setWeekdaySel] = React.useState<Set<number>>(new Set());
  const [courseSel, setCourseSel] = React.useState<Set<string>>(new Set());
  const simulateMut = useScopeOptimizationSimulate();
  const applyMut = useScopeOptimizationApply();
  const [result, setResult] = React.useState<ScopeOptimizationSimulateResponse | null>(null);
  // 結果 (result) を出したときの scope。適用は必ずこの scope で送る
  // (計算後にチップを触っても適用対象が黙って変わらないように).
  const [resultScope, setResultScope] = React.useState<{
    weekdays: number[] | null;
    courseCodes: string[] | null;
  } | null>(null);
  // 適用する手数 (先頭から N 手。既定 = 全手).
  const [applyCount, setApplyCount] = React.useState(0);

  const runSimulate = React.useCallback(
    (weekdays: number[] | null, courseCodes: string[] | null) => {
      if (!effectiveOfficeId) return;
      setResult(null);
      setResultScope(null);
      simulateMut.mutate(
        {
          iso_year: isoYear,
          iso_week: isoWeek,
          scope: { office_id: effectiveOfficeId, weekdays, course_codes: courseCodes },
        },
        {
          onSuccess: (data) => {
            setResult(data);
            setResultScope({ weekdays, courseCodes });
            setApplyCount(data.steps.length);
          },
        },
      );
    },
    // mutation オブジェクトは毎レンダーで変わるため mutate 呼出のみに依存を絞る.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [effectiveOfficeId, isoYear, isoWeek],
  );

  // 開くたびにまっさらな状態から始める (前回の範囲・結果を持ち越さない).
  // initialScope (健康診断からの導線) があればプリセットして自動計算する.
  React.useEffect(() => {
    if (!open) return;
    const wd = initialScope?.weekdays ?? null;
    const cc = initialScope?.courseCodes ?? null;
    setManualOfficeId(null);
    setWeekdaySel(new Set(wd ?? []));
    setCourseSel(new Set(cc ?? []));
    setResult(null);
    setResultScope(null);
    setApplyCount(0);
    simulateMut.reset();
    applyMut.reset();
    if (initialScope && officeId) {
      runSimulate(wd, cc);
    }
    // simulateMut / applyMut は毎レンダーで新オブジェクトのため依存に含めない.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialScope, officeId, runSimulate]);

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
    if (!effectiveOfficeId || simulateMut.isPending || applyMut.isPending) return;
    runSimulate(
      weekdaySel.size > 0 ? [...weekdaySel].sort((a, b) => a - b) : null,
      courseSel.size > 0 ? [...courseSel].sort() : null,
    );
  };

  const handleApply = () => {
    if (!effectiveOfficeId || !result || !resultScope || applyMut.isPending) return;
    if (applyCount < 1 || result.steps.length === 0) return;
    const steps = result.steps.slice(0, applyCount);
    applyMut.mutate(
      {
        iso_year: isoYear,
        iso_week: isoWeek,
        scope: {
          office_id: effectiveOfficeId,
          weekdays: resultScope.weekdays,
          course_codes: resultScope.courseCodes,
        },
        state_token: result.state_token,
        steps,
      },
      {
        onSuccess: (data) => {
          toast.success(
            `${data.applied_count}手を適用しました（−${steps[steps.length - 1]!.cumulative_delta_minutes}分/週）`,
          );
          for (const w of data.warnings.slice(0, 3)) toast.warning(w);
          if (data.warnings.length > 3) {
            toast.warning(`他 ${data.warnings.length - 3} 件の警告があります`);
          }
          // 適用後は最新状態で自動再計算 (残りの改善が見える).
          runSimulate(resultScope.weekdays, resultScope.courseCodes);
        },
        onError: (err) => {
          // 409 = state_token 不一致 (simulate 以降に固定枠が変わった)。
          // fetcher の ApiError.message は detail を含まないため status で判定する。
          if (err instanceof ApiError && err.status === 409) {
            toast.error('スケジュールが変更されています。再計算します');
            runSimulate(resultScope.weekdays, resultScope.courseCodes);
          } else {
            const msg = err instanceof Error ? err.message : '';
            toast.error(`適用に失敗しました${msg ? `: ${msg}` : ''}`);
          }
        },
      },
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

        {!effectiveOfficeId ? (
          <div className="space-y-3 py-4" data-testid="scope-optimize-no-office">
            <div className="text-sm text-text-secondary">
              範囲最適化は拠点単位で行います。対象の拠点を選んでください。
            </div>
            <div className="flex flex-wrap gap-2" data-testid="scope-optimize-office-picker">
              {offices.map((o) => (
                <FilterChip key={o.id} active={false} onClick={() => setManualOfficeId(o.id)}>
                  {o.name}
                </FilterChip>
              ))}
              {offices.length === 0 ? (
                <span className="text-xs text-text-muted">
                  拠点情報を取得できませんでした。画面上部右側の「拠点」プルダウンで拠点を選んでから開き直してください。
                </span>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="space-y-4 py-1">
            {/* a. 範囲選択 */}
            <div className="space-y-2">
              {/* 全拠点モードでダイアログ内選択した場合のみ、拠点の切替チップを出す. */}
              {!officeId ? (
                <div
                  className="flex flex-wrap items-center gap-2"
                  data-testid="scope-optimize-office-filter"
                >
                  <span className="w-14 text-xs text-text-muted">拠点</span>
                  {offices.map((o) => (
                    <FilterChip
                      key={o.id}
                      active={manualOfficeId === o.id}
                      onClick={() => {
                        setManualOfficeId(o.id);
                        setResult(null);
                        setResultScope(null);
                      }}
                    >
                      {o.name}
                    </FilterChip>
                  ))}
                </div>
              ) : null}
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
                    {result.steps.map((step) => {
                      const included = step.seq <= applyCount;
                      return (
                        <div
                          key={step.seq}
                          className={`space-y-1 ${included ? '' : 'opacity-40'}`}
                          data-testid={`scope-optimize-step-${step.seq}`}
                          data-included={included ? 'true' : 'false'}
                        >
                          <div className="flex items-baseline justify-between gap-2 text-xs">
                            <span className="font-medium text-text-primary">
                              手順{step.seq}: {step.patient_name} 様
                              {included ? '' : '（適用しない）'}
                            </span>
                            <span className="tabular-nums text-text-muted">
                              ここまでの累積 −{step.cumulative_delta_minutes}分/週
                            </span>
                          </div>
                          {/* カードは表示専用: canEdit=false で採用/見送りボタンは出ない. */}
                          <ImprovementSuggestionCard
                            suggestion={step.suggestion}
                            canEdit={false}
                            patientName={step.patient_name}
                            onAdopt={() => undefined}
                            onDismiss={() => undefined}
                          />
                          {/* W3: コースタイムライン (同一コース=1枚 / 別コース=2枚並列). */}
                          <StepCourseTimelines step={step} />
                        </div>
                      );
                    })}
                  </div>

                  {/* 適用範囲スライダー + 適用ボタン (W2. プレフィックスのみ). */}
                  {canEdit ? (
                    <div
                      className="space-y-2 rounded-lg border border-border-default bg-bg-muted p-3"
                      data-testid="scope-optimize-apply-panel"
                    >
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <span className="font-medium text-text-primary">
                          先頭から
                          <span className="tabular-nums text-base font-semibold">{applyCount}</span>
                          手を適用
                        </span>
                        <span
                          className="tabular-nums text-success"
                          data-testid="scope-optimize-apply-cumulative"
                        >
                          {applyCount > 0
                            ? `−${result.steps[applyCount - 1]!.cumulative_delta_minutes}分/週（−${Math.abs(
                                result.steps[applyCount - 1]!.cumulative_delta_km,
                              ).toFixed(1)}km/週）`
                            : '適用なし'}
                        </span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={result.steps.length}
                        step={1}
                        value={applyCount}
                        onChange={(e) => setApplyCount(Number(e.target.value))}
                        disabled={applyMut.isPending}
                        className="w-full accent-brand-primary"
                        aria-label="適用する手数"
                        data-testid="scope-optimize-apply-slider"
                      />
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] text-text-muted">
                          手順は前の手が空けた枠を使うため、途中の手だけ選ぶことはできません。
                        </span>
                        <Button
                          type="button"
                          size="sm"
                          onClick={handleApply}
                          disabled={applyMut.isPending || applyCount < 1}
                          data-testid="scope-optimize-apply-button"
                        >
                          {applyMut.isPending ? (
                            <>
                              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                              適用中…
                            </>
                          ) : (
                            `${applyCount}手を適用`
                          )}
                        </Button>
                      </div>
                    </div>
                  ) : null}

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
                  <div className="text-xs text-text-muted" data-testid="scope-optimize-note">
                    ※ 適用は固定訪問スケジュール（恒久パターン）に反映されます。今週の実予定へ
                    反映するには「固定枠戻」を実行してください。
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
