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

import { RakusukeSays } from '@/components/brand/Rakusuke';
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
  ScopeOptimizationCourseBeforeAfter,
  ScopeOptimizationExcludedSummary,
  ScopeOptimizationMetrics,
  ScopeOptimizationSimulateResponse,
} from '@/lib/schemas/v2/scopeOptimization';

import { ChangeScopeChoice, type ChangeScopeValue } from './ChangeScopeChoice';
import { CourseMoveTimeline, type TimelineRowMeta } from './CourseMoveTimeline';
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
   * 外部導線が引き継ぐ拠点 (処方箋フロー: 全拠点表示の健康診断からでも、クリックした
   * 行の拠点で自動計算できる)。ページ側 officeId があればそちらが優先。
   */
  initialOfficeId?: string | null;
  /**
   * 拠点一覧 (全拠点モードでダイアログ内から拠点を選ぶためのチップ用)。
   * officeId が指定されているときは使わない。
   */
  offices?: { id: string; name: string }[];
  /**
   * T-4: 患者 ID → 表示メタ (性別ウォッシュ・住所)。コースタイムラインの
   * カード視覚言語に使う。optional — 未指定なら中立色で描画される。
   */
  patientMetaById?: ReadonlyMap<string, TimelineRowMeta>;
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

/** excluded_summary → 「完全固定N件・…」の内訳テキスト (0 は省略).
    統合 (2026-08-09): 旧「ピン留め」と「可動域が完全固定」は同一概念のため合算表示。 */
function summarizeExcluded(ex: ScopeOptimizationExcludedSummary): string[] {
  const parts: Array<[number, string]> = [
    [ex.pinned + ex.locked, `完全固定${ex.pinned + ex.locked}件`],
    [ex.dismissed, `却下済み${ex.dismissed}件`],
    [ex.confirmation_required_excluded, `要確認のため除外${ex.confirmation_required_excluded}件`],
    [ex.no_current_visit, `固定枠と対応不明${ex.no_current_visit}件`],
    // W-12a (D-5): 2名体制患者は当面 movable から除外 (保護).
    [ex.two_staff, `2名体制のため保護${ex.two_staff}件`],
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

// 手順のコースタイムラインは共有コンポーネント CourseMoveTimeline に統一 (UI 統一)。
// 同一コース = 1 枚 / 別コース = 2 枚並列。改善提案 (患者詳細) と同じ見せ方。

const WEEKDAY_FULL = ['月', '火', '水', '木', '金', '土', '日'] as const;

/**
 * H2: コース別の実行後見通し (「稲B(火) 移動 92分→51分」)。
 * 変化のあるコースのみ行で見せ、変化なしは件数で畳む。見通しは恒久パターン基準 (D-1)。
 */
function CourseBeforeAfterList({ courses }: { courses: ScopeOptimizationCourseBeforeAfter[] }) {
  const changed = courses.filter(
    (c) =>
      c.before.travel_minutes !== c.after.travel_minutes ||
      c.before.travel_km !== c.after.travel_km ||
      c.before.gap_minutes !== c.after.gap_minutes,
  );
  const unchangedCount = courses.length - changed.length;
  if (courses.length === 0) return null;
  return (
    <div
      className="space-y-1 rounded-lg border border-border-default bg-bg-base p-3"
      data-testid="scope-optimize-courses"
    >
      <div className="text-xs font-semibold text-text-primary">
        コース別の見通し
        <span className="ml-1 font-normal text-text-muted">（適用した場合・恒久パターン基準）</span>
      </div>
      {changed.length === 0 ? (
        <div className="text-xs text-text-muted">変化するコースはありません。</div>
      ) : (
        changed.map((c) => {
          const diff = c.after.travel_minutes - c.before.travel_minutes;
          return (
            <div
              key={`${c.office_id}-${c.weekday}-${c.course_code}`}
              className="flex flex-wrap items-baseline gap-1.5 text-xs tabular-nums"
              data-testid="scope-optimize-course-row"
            >
              <span className="font-medium text-text-primary">
                {c.course_label}（{WEEKDAY_FULL[c.weekday]}
                {c.staff_name ? `・${c.staff_name}` : ''}）:
              </span>
              <span className="text-text-muted line-through">移動{c.before.travel_minutes}分</span>
              <span aria-hidden>→</span>
              <span className="font-bold text-text-primary">{c.after.travel_minutes}分</span>
              <span className={diff < 0 ? 'text-success' : 'text-warning'}>
                （{diff < 0 ? '−' : '+'}
                {Math.abs(diff)}分）
              </span>
              <span className="text-[10px] text-text-muted">
                {c.before.travel_km.toFixed(1)}km→{c.after.travel_km.toFixed(1)}km
              </span>
            </div>
          );
        })
      )}
      {unchangedCount > 0 ? (
        <div className="text-[10px] text-text-muted">変化なし {unchangedCount} コース</div>
      ) : null}
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
  initialOfficeId = null,
  offices = [],
  patientMetaById,
}: ScopeOptimizeDialogProps) {
  // 全拠点モード (officeId=null) でダイアログ内から選ぶ拠点.
  const [manualOfficeId, setManualOfficeId] = React.useState<string | null>(null);
  // 実効拠点: ページ側フィルタ優先、無ければダイアログ内選択.
  const effectiveOfficeId = officeId ?? manualOfficeId;
  // 未選択 = 全部 (チップの「全曜日」「全コース」は選択クリアと同義).
  const [weekdaySel, setWeekdaySel] = React.useState<Set<number>>(new Set());
  const [courseSel, setCourseSel] = React.useState<Set<string>>(new Set());
  // §10: 探索範囲 (移動先を探す範囲)。既定 = 拠点全体 (推奨).
  const [searchMode, setSearchMode] = React.useState<'office' | 'same' | 'custom'>('office');
  const [searchWeekdaySel, setSearchWeekdaySel] = React.useState<Set<number>>(new Set());
  const [searchCourseSel, setSearchCourseSel] = React.useState<Set<string>>(new Set());
  const simulateMut = useScopeOptimizationSimulate();
  const applyMut = useScopeOptimizationApply();
  const [result, setResult] = React.useState<ScopeOptimizationSimulateResponse | null>(null);
  // 結果 (result) を出したときの scope。適用は必ずこの scope で送る
  // (計算後にチップを触っても適用対象が黙って変わらないように).
  const [resultScope, setResultScope] = React.useState<{
    weekdays: number[] | null;
    courseCodes: string[] | null;
    // §10: 計算に使った探索範囲 (null = フォーカスと同じ)。apply でエコーバックする.
    search: { weekdays: number[] | null; courseCodes: string[] | null } | null;
  } | null>(null);
  // 適用する手数 (先頭から N 手。既定 = 全手).
  const [applyCount, setApplyCount] = React.useState(0);
  // U-1: 反映先の選択 ('pattern' = A: 固定訪問週間に登録 + 今週即反映 / 'week' = B: この週だけ). 既定 A.
  const [changeScopeChoice, setChangeScopeChoice] = React.useState<ChangeScopeValue>('pattern');

  /** §10: 現在の探索範囲選択から search 部分を組み立てる (null = フォーカスと同じ). */
  const buildSearch = React.useCallback(
    (
      focusWeekdays: number[] | null,
      focusCourses: string[] | null,
    ): { weekdays: number[] | null; courseCodes: string[] | null } | null => {
      if (searchMode === 'same') return null;
      if (searchMode === 'office') return { weekdays: null, courseCodes: null };
      // custom: フォーカスとの和集合を送る (探索 ⊇ フォーカスを UI 側でも保証).
      const wd =
        focusWeekdays === null || searchWeekdaySel.size === 0
          ? null
          : [...new Set([...focusWeekdays, ...searchWeekdaySel])].sort((a, b) => a - b);
      const cc =
        focusCourses === null || searchCourseSel.size === 0
          ? null
          : [...new Set([...focusCourses, ...searchCourseSel])].sort();
      return { weekdays: wd, courseCodes: cc };
    },
    [searchMode, searchWeekdaySel, searchCourseSel],
  );

  const runSimulate = React.useCallback(
    (
      weekdays: number[] | null,
      courseCodes: string[] | null,
      // open 直後は manualOfficeId の setState が未反映のため、外部導線からの
      // 自動計算では拠点を明示的に受け取る (state 反映待ちの stale 回避).
      officeOverride?: string,
      // 同様に searchMode の setState 未反映を避けるため明示指定できる
      // (undefined = 現在の選択から組み立てる).
      searchOverride?: { weekdays: number[] | null; courseCodes: string[] | null } | null,
    ) => {
      const oid = officeOverride ?? effectiveOfficeId;
      if (!oid) return;
      const search =
        searchOverride !== undefined ? searchOverride : buildSearch(weekdays, courseCodes);
      setResult(null);
      setResultScope(null);
      simulateMut.mutate(
        {
          iso_year: isoYear,
          iso_week: isoWeek,
          scope: { office_id: oid, weekdays, course_codes: courseCodes },
          // undefined ならフィールド自体が JSON から消える (旧 BE の extra=forbid にも透過).
          search_scope: search
            ? { office_id: oid, weekdays: search.weekdays, course_codes: search.courseCodes }
            : undefined,
        },
        {
          onSuccess: (data) => {
            setResult(data);
            setResultScope({ weekdays, courseCodes, search });
            setApplyCount(data.steps.length);
          },
        },
      );
    },
    // mutation オブジェクトは毎レンダーで変わるため mutate 呼出のみに依存を絞る.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [effectiveOfficeId, isoYear, isoWeek, buildSearch],
  );

  // 開くたびにまっさらな状態から始める (前回の範囲・結果を持ち越さない).
  // initialScope (健康診断からの導線) があればプリセットして自動計算する.
  //
  // 依存は **open のみ** に固定する (本番 hotfix)。runSimulate を deps に含めると、
  // 拠点チップ選択 → effectiveOfficeId 変化 → runSimulate 再生成 → 本 effect が
  // 再発火して setManualOfficeId(null) で選択が即座に取り消される
  // (= チップを押しても何も起きないように見える) ループになる。
  React.useEffect(() => {
    if (!open) return;
    const wd = initialScope?.weekdays ?? null;
    const cc = initialScope?.courseCodes ?? null;
    // 外部導線 (健康診断) からの拠点引き継ぎ: 全拠点表示でも行の拠点で自動計算できる.
    // W-6 項目2: 通常起動 (initialOfficeId 無し) でも先頭拠点を既定選択し、拠点ピッカー
    // 画面を飛ばして即「対策を絞りたい範囲」画面を開く。拠点は上部チップで変更可能。
    // offices が空のときのみ null のまま = 従来のピッカーがフォールバックとして出る。
    setManualOfficeId(initialOfficeId ?? offices[0]?.id ?? null);
    setWeekdaySel(new Set(wd ?? []));
    setCourseSel(new Set(cc ?? []));
    setSearchMode('office'); // §10: 探索範囲の既定は拠点全体 (推奨).
    setSearchWeekdaySel(new Set());
    setSearchCourseSel(new Set());
    setResult(null);
    setResultScope(null);
    setApplyCount(0);
    setChangeScopeChoice('pattern');
    simulateMut.reset();
    applyMut.reset();
    const autoOfficeId = officeId ?? initialOfficeId ?? null;
    if (initialScope && autoOfficeId) {
      // manualOfficeId / searchMode の setState はこの effect 内では未反映のため
      // 明示的に渡す (拠点=行の拠点・探索=拠点全体).
      runSimulate(wd, cc, autoOfficeId, { weekdays: null, courseCodes: null });
    }
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
        // §10: simulate と同じ探索範囲をエコーバック (state_token の再計算規約を一致).
        search_scope: resultScope.search
          ? {
              office_id: effectiveOfficeId,
              weekdays: resultScope.search.weekdays,
              course_codes: resultScope.search.courseCodes,
            }
          : undefined,
        // U-1: 反映先選択 ('pattern_and_week' = A / 'week_only' = B).
        change_scope: changeScopeChoice === 'pattern' ? 'pattern_and_week' : 'week_only',
      },
      {
        onSuccess: (data) => {
          if (changeScopeChoice === 'week') {
            // B 経路: この週だけ反映
            toast.success('この週だけ反映しました。来週は従来の型で生成されます');
          } else {
            // A 経路: 型 + 今週即反映 (固定枠戻の案内は不要: BE が今週へも自動反映)
            toast.success(
              `${data.applied_count}手を適用しました（−${steps[steps.length - 1]!.cumulative_delta_minutes}分/週）`,
            );
          }
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
            スケジュール最適化
            <span className="tabular-nums text-sm font-normal text-text-muted">{weekLabel}</span>
          </DialogTitle>
          <DialogDescription>
            範囲を選んで計算すると、その範囲の中だけで「どの枠をどう動かすとどれだけ移動が減るか」を
            手順として提案します。ピン留め・完全固定・却下済みの枠は動かしません。
          </DialogDescription>
        </DialogHeader>

        {/* R-10b: 計算前から「見つけました」と断言しない (PO指摘)。結果は計算後の手順表示が示す。 */}
        <RakusukeSays pose="idea" message="もっと楽になる並びがないか、一緒に探しましょう" />

        {!effectiveOfficeId ? (
          <div className="space-y-3 py-4" data-testid="scope-optimize-no-office">
            <div className="text-sm text-text-secondary">
              スケジュール最適化は拠点単位で行います。対象の拠点を選んでください。
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
            {/* a. 範囲選択 (§10: ①フォーカス → ②探索範囲 の 2 段階) */}
            <div className="space-y-2">
              <div className="text-xs font-semibold text-text-primary">
                ① 対策を練りたい範囲（フォーカス）
              </div>
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
              {/* §10 ②: 移動先・入れ替え相手を探す範囲. */}
              <div className="pt-1 text-xs font-semibold text-text-primary">② 移動先を探す範囲</div>
              <div
                className="flex flex-wrap items-center gap-2"
                data-testid="scope-optimize-search-filter"
              >
                <span className="w-14 text-xs text-text-muted">探索</span>
                <FilterChip
                  active={searchMode === 'office'}
                  onClick={() => setSearchMode('office')}
                >
                  拠点全体（推奨）
                </FilterChip>
                <FilterChip active={searchMode === 'same'} onClick={() => setSearchMode('same')}>
                  フォーカスと同じ
                </FilterChip>
                <FilterChip
                  active={searchMode === 'custom'}
                  onClick={() => setSearchMode('custom')}
                >
                  カスタム
                </FilterChip>
              </div>
              {searchMode === 'custom' ? (
                <div className="space-y-2 rounded border border-border-default bg-bg-muted p-2">
                  <div className="text-[10px] text-text-muted">
                    フォーカスに加えて探索に含める曜日・コース（未選択の軸は全体を探索）
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="w-14 text-xs text-text-muted">曜日</span>
                    {SELECTABLE_WEEKDAYS.map((wd) => (
                      <FilterChip
                        key={`sw-${wd}`}
                        active={searchWeekdaySel.has(wd)}
                        onClick={() =>
                          setSearchWeekdaySel((prev) => {
                            const next = new Set(prev);
                            if (next.has(wd)) next.delete(wd);
                            else next.add(wd);
                            return next;
                          })
                        }
                      >
                        {WEEKDAY_LABELS[wd]}
                      </FilterChip>
                    ))}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="w-14 text-xs text-text-muted">コース</span>
                    {SELECTABLE_COURSES.map((code) => (
                      <FilterChip
                        key={`sc-${code}`}
                        active={searchCourseSel.has(code)}
                        onClick={() =>
                          setSearchCourseSel((prev) => {
                            const next = new Set(prev);
                            if (next.has(code)) next.delete(code);
                            else next.add(code);
                            return next;
                          })
                        }
                      >
                        {code}
                      </FilterChip>
                    ))}
                  </div>
                </div>
              ) : null}
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-text-muted" data-testid="scope-optimize-scope-label">
                  フォーカス: {scopeLabel} ／ 探索:{' '}
                  {searchMode === 'office'
                    ? '拠点全体'
                    : searchMode === 'same'
                      ? 'フォーカスと同じ'
                      : 'カスタム'}
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
                  {/* §10: 探索がフォーカスより広いときは、フォーカスの前後比較を主役にする. */}
                  {resultScope?.search && result.focus_before && result.focus_after ? (
                    <div className="space-y-1.5">
                      <div className="text-xs font-semibold text-text-primary">
                        フォーカス（対策対象）の見通し
                      </div>
                      <MetricsTiles before={result.focus_before} after={result.focus_after} />
                      <div
                        className="tabular-nums text-xs text-text-muted"
                        data-testid="scope-optimize-global-line"
                      >
                        探索範囲全体: 移動 {result.before.travel_minutes}分 →{' '}
                        {result.after.travel_minutes}分・{result.before.travel_km.toFixed(1)}km →{' '}
                        {result.after.travel_km.toFixed(1)}km
                        （フォーカス外へのしわ寄せは下のコース別見通しで確認できます）
                      </div>
                    </div>
                  ) : (
                    <MetricsTiles before={result.before} after={result.after} />
                  )}

                  {/* H2: コース別の実行後見通し. */}
                  <CourseBeforeAfterList courses={result.courses} />

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
                          {/* コースタイムライン (共有 CourseMoveTimeline: 改善提案と同一の見せ方). */}
                          <CourseMoveTimeline
                            suggestion={step.suggestion}
                            targetPatientId={step.patient_id}
                            patientName={step.patient_name}
                            sourceCourse={step.source_course}
                            destinationCourse={step.destination_course}
                            patientMetaById={patientMetaById}
                          />
                        </div>
                      );
                    })}
                  </div>

                  {/* 適用範囲スライダー + 反映先選択 + 適用ボタン (W2. プレフィックスのみ). */}
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
                      {/* U-1: 反映先の2択 (既定 A). */}
                      <ChangeScopeChoice
                        value={changeScopeChoice}
                        onChange={setChangeScopeChoice}
                        disabled={applyMut.isPending}
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
                      {/* U-1: 反映先の説明 (選択に応じて動的に変更). */}
                      <div
                        className="text-[11px] text-text-muted"
                        data-testid="scope-optimize-note"
                      >
                        {changeScopeChoice === 'pattern'
                          ? '※ 固定訪問週間（毎週の型）と今週のスケジュールの両方に反映されます。'
                          : '※ 今週のスケジュールのみ変更します。毎週の型（固定訪問週間）は変わりません。'}
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
