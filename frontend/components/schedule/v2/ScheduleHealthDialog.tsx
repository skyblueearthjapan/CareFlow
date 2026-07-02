'use client';

/**
 * ScheduleHealthDialog — スケジュール健康診断 (Schedule Advisor Phase 1).
 *
 * 設計仕様書: ``docs/plans/schedule-advisor-design.md`` §3 Phase 1.
 *
 * 訪問看護の管理者が「移動や隙間の無駄」を数字で見る健康診断パネル.
 * 当週 + 前週の 2 クエリを張り:
 *   a. サマリタイル 3 枚 (移動時間 / 隙間時間 / 移動距離, office 横断合計 + 前週比 delta)
 *   b. 曜日フィルタ (全曜日 / 月〜土)
 *   c. コース別 横バー (移動時間ベース. 表示中平均の 1.5 倍超は warning トーン)
 *   d. 空 / ローディング / エラー状態
 * 複数 office が返る場合は office ごとにセクション見出し.
 *
 * デザイン: docs/design/01-design-system.md「Warm & Human」. トークンのみ使用
 * (bg-base / bg-muted / border-default / text 各種 / success / warning / error /
 *  brand-primary 系).
 * 数字は tabular-nums.
 *
 * RBAC: 呼出側 (CourseDayTablePanel) で admin/manager ガード済み.
 */
import * as React from 'react';
import { HeartPulse, TriangleAlert } from 'lucide-react';

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
import { Skeleton } from '@/components/ui/skeleton';
import { useScheduleHealth } from '@/lib/queries/scheduleHealth';
import { previousIsoWeek } from '@/lib/format/isoWeek';
import type {
  ScheduleHealthOffice,
  ScheduleHealthResponse,
} from '@/lib/schemas/v2/scheduleHealth';

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface ScheduleHealthDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** null = 全拠点. それ以外は単一拠点フィルタ. */
  officeId: string | null;
  /** ヘッダー表示用の週ラベル (例: "2026-W27"). */
  weekLabel: string;
}

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

/** 0=月..5=土 (日曜は健康診断の対象外: 訪問看護の稼働曜日に合わせる). */
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;
const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;

/** 表示中コース平均の何倍超で「移動が多いコース」warning とするか. */
const HIGH_TRAVEL_RATIO = 1.5;

// ─────────────────────────────────────────────────────────────────────────
// 集計ヘルパー
// ─────────────────────────────────────────────────────────────────────────

interface CrossOfficeTotals {
  travelMinutes: number;
  gapMinutes: number;
  travelKm: number;
}

/** office 横断で week_totals を合算する (サマリタイル用). */
function sumCrossOfficeTotals(data: ScheduleHealthResponse | undefined): CrossOfficeTotals {
  const acc: CrossOfficeTotals = { travelMinutes: 0, gapMinutes: 0, travelKm: 0 };
  for (const office of data?.offices ?? []) {
    acc.travelMinutes += office.week_totals.travel_minutes;
    acc.gapMinutes += office.week_totals.gap_minutes;
    acc.travelKm += office.week_totals.travel_km;
  }
  return acc;
}

interface CourseBar {
  courseCode: string;
  staffName: string | null;
  travelMinutes: number;
  gapMinutes: number;
  visitCount: number;
}

interface OfficeBars {
  officeId: string;
  officeName: string;
  bars: CourseBar[];
}

/**
 * 曜日フィルタ範囲の course を (office, course_code) で集約する.
 * "全曜日" 時は週内の同コースを合算し、1 コース = 1 バーにする.
 */
function aggregateOfficeBars(
  office: ScheduleHealthOffice,
  weekdayFilter: number | 'all',
): OfficeBars {
  const byCode = new Map<string, CourseBar>();
  for (const wd of office.weekdays) {
    if (weekdayFilter !== 'all' && wd.weekday !== weekdayFilter) continue;
    for (const course of wd.courses) {
      const existing = byCode.get(course.course_code);
      if (existing) {
        existing.travelMinutes += course.travel_minutes;
        existing.gapMinutes += course.gap_minutes;
        existing.visitCount += course.visit_count;
        // staff_name は最初に見つかった非 null を代表として保持.
        if (existing.staffName == null) existing.staffName = course.staff_name;
      } else {
        byCode.set(course.course_code, {
          courseCode: course.course_code,
          staffName: course.staff_name,
          travelMinutes: course.travel_minutes,
          gapMinutes: course.gap_minutes,
          visitCount: course.visit_count,
        });
      }
    }
  }
  const bars = [...byCode.values()].sort((a, b) => b.travelMinutes - a.travelMinutes);
  return { officeId: office.office_id, officeName: office.office_name, bars };
}

// ─────────────────────────────────────────────────────────────────────────
// 表示フォーマット
// ─────────────────────────────────────────────────────────────────────────

interface DeltaDisplay {
  text: string;
  toneClass: string;
}

/** 前週比 delta を符号付きラベル + トーンに変換 (増加=warning/減少=success/±0=muted). */
function formatDelta(current: number, previous: number, unit: '分' | 'km'): DeltaDisplay {
  const raw = current - previous;
  const rounded = unit === 'km' ? Math.round(raw * 10) / 10 : Math.round(raw);
  const toneClass =
    rounded > 0 ? 'text-warning' : rounded < 0 ? 'text-success' : 'text-text-muted';
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '±';
  const abs = Math.abs(rounded);
  const value = unit === 'km' ? abs.toFixed(1) : String(abs);
  return { text: `${sign}${value}${unit}`, toneClass };
}

function fmtMinutes(min: number): string {
  return `${Math.round(min)}分`;
}

function fmtKm(km: number): string {
  return `${(Math.round(km * 10) / 10).toFixed(1)}km`;
}

// ─────────────────────────────────────────────────────────────────────────
// サマリタイル
// ─────────────────────────────────────────────────────────────────────────

interface SummaryTileProps {
  label: string;
  value: string;
  delta: DeltaDisplay | null;
  testId: string;
}

function SummaryTile({ label, value, delta, testId }: SummaryTileProps) {
  return (
    <div
      className="rounded-lg border border-border-default bg-bg-base p-3"
      data-testid={testId}
    >
      <div className="text-xs text-text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-text-primary">{value}</div>
      <div className="mt-1 text-xs tabular-nums">
        {delta ? (
          <span className={delta.toneClass} data-testid={`${testId}-delta`}>
            前週比 {delta.text}
          </span>
        ) : (
          <span className="text-text-muted" data-testid={`${testId}-delta-none`}>
            前週データなし
          </span>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// コース別バー
// ─────────────────────────────────────────────────────────────────────────

interface CourseBarRowProps {
  bar: CourseBar;
  maxTravel: number;
  isHigh: boolean;
}

function CourseBarRow({ bar, maxTravel, isHigh }: CourseBarRowProps) {
  const pct = maxTravel > 0 ? (bar.travelMinutes / maxTravel) * 100 : 0;
  // travel>0 なら最低 4% は見せてバーの存在を認識できるようにする.
  const width = bar.travelMinutes > 0 ? Math.max(pct, 4) : 0;
  const label = bar.staffName ? `${bar.courseCode}・${bar.staffName}` : bar.courseCode;
  return (
    <div className="space-y-1" data-testid="schedule-health-bar" data-high={isHigh ? 'true' : 'false'}>
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="flex items-center gap-1 font-medium text-text-primary">
          {isHigh ? (
            <TriangleAlert className="h-3.5 w-3.5 text-warning" aria-hidden />
          ) : null}
          {label}
        </span>
        <span className="tabular-nums text-text-secondary">
          移動{fmtMinutes(bar.travelMinutes)}・隙間{fmtMinutes(bar.gapMinutes)}・訪問
          {bar.visitCount}件
        </span>
      </div>
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-bg-muted">
        <div
          className={`h-full rounded-full ${isHigh ? 'bg-warning' : 'bg-brand-primary'}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function ScheduleHealthDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
  weekLabel,
}: ScheduleHealthDialogProps) {
  const [weekdayFilter, setWeekdayFilter] = React.useState<number | 'all'>('all');

  // 診断ダイアログは開くたびにまっさらな全曜日ビューから始める (前回のフィルタを持ち越さない)。
  React.useEffect(() => {
    if (open) setWeekdayFilter('all');
  }, [open]);

  const prev = React.useMemo(() => previousIsoWeek(isoYear, isoWeek), [isoYear, isoWeek]);

  const currentQuery = useScheduleHealth({ isoYear, isoWeek, officeId, enabled: open });
  const prevQuery = useScheduleHealth({
    isoYear: prev.isoYear,
    isoWeek: prev.isoWeek,
    officeId,
    enabled: open,
  });

  const isLoading = currentQuery.isLoading;
  const currentData = currentQuery.data;

  // サマリタイル: 当週 / 前週の office 横断合計.
  const currentTotals = React.useMemo(() => sumCrossOfficeTotals(currentData), [currentData]);
  const prevTotals = React.useMemo(
    () => sumCrossOfficeTotals(prevQuery.data),
    [prevQuery.data],
  );
  // 前週データが取得できて 1 拠点以上あれば delta を出す. なければ「前週データなし」.
  const hasPrev = prevQuery.isSuccess && (prevQuery.data?.offices.length ?? 0) > 0;

  // コース別バー: 曜日フィルタ範囲を office ごとに集約.
  const officeBars = React.useMemo(() => {
    return (currentData?.offices ?? []).map((o) => aggregateOfficeBars(o, weekdayFilter));
  }, [currentData, weekdayFilter]);

  // 表示中の全バーで最大 travel と平均 travel を出し、1.5 倍超判定に使う.
  const { maxTravel, highThreshold } = React.useMemo(() => {
    const travels: number[] = [];
    for (const ob of officeBars) for (const b of ob.bars) travels.push(b.travelMinutes);
    if (travels.length === 0) return { maxTravel: 0, highThreshold: Infinity };
    const max = Math.max(...travels);
    const avg = travels.reduce((s, v) => s + v, 0) / travels.length;
    return { maxTravel: max, highThreshold: avg * HIGH_TRAVEL_RATIO };
  }, [officeBars]);

  const hasAnyBar = officeBars.some((ob) => ob.bars.length > 0);
  const hasData = (currentData?.offices.length ?? 0) > 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent
        className="max-h-[90vh] max-w-2xl overflow-y-auto"
        data-testid="schedule-health-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <HeartPulse className="h-5 w-5 text-brand-primary" aria-hidden />
            スケジュール健康診断
            <span className="tabular-nums text-sm font-normal text-text-muted">{weekLabel}</span>
          </DialogTitle>
          <DialogDescription>
            移動・隙間の無駄を数字で確認できます。前週比とコース別の移動時間から、
            「移動が多いコース」を一目で把握できます。
          </DialogDescription>
        </DialogHeader>

        {/* エラー */}
        {currentQuery.isError ? (
          <div className="py-6 text-center text-sm text-error" data-testid="schedule-health-error">
            健康診断データの取得に失敗しました。
          </div>
        ) : isLoading ? (
          /* ローディング */
          <div className="space-y-4 py-2" data-testid="schedule-health-loading">
            <div className="grid grid-cols-3 gap-3">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
            <Skeleton className="h-8 w-48" />
            <div className="space-y-3">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          </div>
        ) : !hasData ? (
          /* 空状態 */
          <div
            className="py-10 text-center text-sm text-text-muted"
            data-testid="schedule-health-empty"
          >
            この週の訪問データがありません。
          </div>
        ) : (
          <div className="space-y-5 py-2">
            {/* a. サマリタイル 3 枚 */}
            <div className="grid grid-cols-3 gap-3">
              <SummaryTile
                label="移動時間 合計"
                value={fmtMinutes(currentTotals.travelMinutes)}
                delta={
                  hasPrev
                    ? formatDelta(currentTotals.travelMinutes, prevTotals.travelMinutes, '分')
                    : null
                }
                testId="schedule-health-tile-travel"
              />
              <SummaryTile
                label="隙間時間 合計"
                value={fmtMinutes(currentTotals.gapMinutes)}
                delta={
                  hasPrev
                    ? formatDelta(currentTotals.gapMinutes, prevTotals.gapMinutes, '分')
                    : null
                }
                testId="schedule-health-tile-gap"
              />
              <SummaryTile
                label="移動距離 合計"
                value={fmtKm(currentTotals.travelKm)}
                delta={
                  hasPrev
                    ? formatDelta(currentTotals.travelKm, prevTotals.travelKm, 'km')
                    : null
                }
                testId="schedule-health-tile-km"
              />
            </div>

            {/* b. 曜日フィルタ */}
            <div className="flex flex-wrap gap-2" data-testid="schedule-health-weekday-filter">
              <FilterChip active={weekdayFilter === 'all'} onClick={() => setWeekdayFilter('all')}>
                全曜日
              </FilterChip>
              {DISPLAY_WEEKDAYS.map((wd) => (
                <FilterChip
                  key={wd}
                  active={weekdayFilter === wd}
                  onClick={() => setWeekdayFilter(wd)}
                >
                  {WEEKDAY_LABELS[wd]}
                </FilterChip>
              ))}
            </div>

            {/* c. コース別バー (office ごとにセクション) */}
            {!hasAnyBar ? (
              <div
                className="py-8 text-center text-sm text-text-muted"
                data-testid="schedule-health-no-course"
              >
                選択した曜日のコースデータがありません。
              </div>
            ) : (
              <div className="space-y-5">
                {officeBars.map((ob) =>
                  ob.bars.length === 0 ? null : (
                    <div key={ob.officeId} className="space-y-3">
                      <div className="text-sm font-semibold text-text-primary">
                        {ob.officeName}
                      </div>
                      <div className="space-y-3">
                        {ob.bars.map((bar) => (
                          <CourseBarRow
                            key={`${ob.officeId}:${bar.courseCode}`}
                            bar={bar}
                            maxTravel={maxTravel}
                            isHigh={bar.travelMinutes > highThreshold}
                          />
                        ))}
                      </div>
                    </div>
                  ),
                )}
              </div>
            )}
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
