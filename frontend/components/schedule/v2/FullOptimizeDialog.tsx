'use client';

/**
 * FullOptimizeDialog — Wave 41 v2 (全面最適化モード, 機能 B).
 *
 * 仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §4, §7, §13.5.2
 *
 * フロー (2 段階):
 *   1. 開くと POST /full-optimize で全 active 患者の再構築提案を算出 (spinner)
 *   2. 結果サマリー画面 (reviewing-summary): KPI バー + 曜日タブ Before/After
 *   3. 大枠判断: 「変更しない (閉じる)」 or 「個別に確認していく →」
 *   4. 個別調整モード (individual-review): 患者ごと Before/After ポップアップ
 *   5. 全件確認で completed → ダイアログ閉じる
 *
 * 一括採用ボタンは設けない (Q2 確定).
 */
import * as React from 'react';
import { ArrowRight, CalendarRange, CheckCircle2, Loader2, Pin, RefreshCw, X } from 'lucide-react';
import { toast } from 'sonner';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  useApplyIndividualMutation,
  useApplyWeekOnlyMutation,
  useFullOptimizeMutation,
} from '@/lib/queries/autoScheduleV2';
import type {
  FullOptimizeResponse,
  IndividualProposal,
  V2CourseSummary,
  V2VisitForUI,
  V2VisitPlan,
  V2Warning,
  V2WeekdayBeforeAfter,
} from '@/lib/schemas/v2/autoScheduleV2';

import { cn } from '@/lib/utils';

import { useStaffList } from '@/lib/queries/staff';

import { FixedTimeEditModal } from './FixedTimeEditModal';
import { ProposalWeekCalendar } from './ProposalWeekCalendar';
import { VisitArrow } from './VisitArrow';
import { formatDelta, formatErr, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Constants / Helpers
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;
const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;

function fmtWd(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? `?${weekday}`;
}

/**
 * W41 v2 (UI 時間詳細表示): time_type バッジを preferred_start/end と合わせて整形.
 *
 * - 時間帯 (preferred_start/end あり): "🕐 時間帯 (09:00-10:30)"
 * - 固定 (preferred_start あり): "🕐 固定 (10:00)"
 * - 午前: "🕐 午前 (~12:00)"
 * - 午後: "🕐 午後 (13:00~)"
 * - 終日: "🕐 終日"
 * - その他 (string): "🕐 {time_type}"
 * - null / undefined: null (バッジ非表示)
 */
function formatTimeCondition(v: V2VisitForUI): string | null {
  if (v.time_type === '時間帯' && v.preferred_start && v.preferred_end) {
    return `🕐 時間帯 (${trimSeconds(v.preferred_start)}-${trimSeconds(v.preferred_end)})`;
  }
  if (v.time_type === '固定' && v.preferred_start) {
    return `🕐 固定 (${trimSeconds(v.preferred_start)})`;
  }
  if (v.time_type === '午前') return '🕐 午前 (~12:00)';
  if (v.time_type === '午後') return '🕐 午後 (13:00~)';
  if (v.time_type === '終日') return '🕐 終日';
  return v.time_type ? `🕐 ${v.time_type}` : null;
}

// ─────────────────────────────────────────────────────────────────────────
// State machine
// ─────────────────────────────────────────────────────────────────────────

type DialogStage =
  | 'idle' // 初期
  | 'allocating' // /full-optimize 実行中
  | 'reviewing-summary' // 結果サマリー閲覧中 (大枠判断前)
  | 'week-only-confirm' // 「この週だけ試す」確認ダイアログ表示中
  | 'week-only-applying' // /apply-week-only 実行中
  | 'individual-review' // 個別調整モード (1 件ずつ確認)
  | 'completed'; // 全件確認終了 or 中断

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface FullOptimizeDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function FullOptimizeDialog({
  open,
  onClose,
  isoYear,
  isoWeek,
  officeId,
}: FullOptimizeDialogProps) {
  const fetchMut = useFullOptimizeMutation();
  const applyMut = useApplyIndividualMutation();
  const applyWeekOnlyMut = useApplyWeekOnlyMutation();

  const [stage, setStage] = React.useState<DialogStage>('idle');
  const [result, setResult] = React.useState<FullOptimizeResponse | null>(null);
  const [activeTab, setActiveTab] = React.useState<string>('0'); // weekday string
  const [activePatient, setActivePatient] = React.useState<IndividualProposal | null>(null);
  // 患者 ID → 採用/却下 のローカル状態.
  const [appliedPatientIds, setAppliedPatientIds] = React.useState<Set<string>>(new Set());
  const [rejectedPatientIds, setRejectedPatientIds] = React.useState<Set<string>>(new Set());
  const [skippedPatientIds, setSkippedPatientIds] = React.useState<Set<string>>(new Set());
  // W41 v2 拡張 (警告アクション): 「⏰ 固定時間を変更」モーダル + 編集済み件数.
  const [editingWarning, setEditingWarning] = React.useState<V2Warning | null>(null);
  const [editedWarningCount, setEditedWarningCount] = React.useState(0);
  const [editedWarningKeys, setEditedWarningKeys] = React.useState<Set<string>>(new Set());
  // W41 v2 拡張 (1週間 B/A グリッド): デフォルト非表示でパフォーマンス確保.

  // open のたびにリセット + 再計算.
  React.useEffect(() => {
    if (!open) return;
    setStage('allocating');
    setResult(null);
    setActiveTab('0');
    setActivePatient(null);
    setAppliedPatientIds(new Set());
    setRejectedPatientIds(new Set());
    setSkippedPatientIds(new Set());
    setEditingWarning(null);
    setEditedWarningCount(0);
    setEditedWarningKeys(new Set());
    fetchMut.reset();
    applyMut.reset();
    applyWeekOnlyMut.reset();
    void (async () => {
      try {
        const res = await fetchMut.mutateAsync({
          iso_year: isoYear,
          iso_week: isoWeek,
          office_ids: officeId ? [officeId] : [],
        });
        setResult(res);
        setStage('reviewing-summary');
      } catch (err) {
        toast.error(`全面最適化に失敗しました: ${formatErr(err)}`);
        setStage('idle');
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isoYear, isoWeek, officeId]);

  const isLoading = fetchMut.isPending;
  const isApplying = applyMut.isPending;
  const isApplyingWeekOnly = applyWeekOnlyMut.isPending;
  const isBusy = isLoading || isApplying || isApplyingWeekOnly;

  /**
   * 再実行: 警告アクションで固定時間を修正した後、全面最適化を再算出する.
   * 同住所集約だけの自動再実行は NG (ユーザー意図の確認が必要) のため、
   * このボタンはユーザー明示 click でのみ呼ばれる.
   */
  const handleReallocate = React.useCallback(async () => {
    setStage('allocating');
    setResult(null);
    setActivePatient(null);
    setAppliedPatientIds(new Set());
    setRejectedPatientIds(new Set());
    setSkippedPatientIds(new Set());
    setEditedWarningCount(0);
    setEditedWarningKeys(new Set());
    try {
      const res = await fetchMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeId ? [officeId] : [],
      });
      setResult(res);
      setStage('reviewing-summary');
      toast.success('全面最適化を再実行しました');
    } catch (err) {
      toast.error(`全面最適化に失敗しました: ${formatErr(err)}`);
      setStage('idle');
    }
  }, [fetchMut, isoYear, isoWeek, officeId]);

  const handleClose = () => {
    if (isBusy) return;
    onClose();
  };

  /** 結果サマリー画面から個別調整モードへ移行 */
  const handleStartIndividualReview = () => {
    if (!result) return;
    const first = result.individual_proposals[0];
    if (!first) {
      toast.info('調整可能な患者がありません');
      return;
    }
    setActivePatient(first);
    setStage('individual-review');
  };

  /** 「この週だけ試す」ボタン押下 → 確認ダイアログを表示 */
  const handleRequestWeekOnly = () => {
    if (!result) return;
    if (result.individual_proposals.length === 0) {
      toast.info('反映可能な患者がいません');
      return;
    }
    setStage('week-only-confirm');
  };

  /** 「この週だけ試す」確認ダイアログのキャンセル */
  const handleCancelWeekOnly = () => {
    if (isApplyingWeekOnly) return;
    setStage('reviewing-summary');
  };

  /** 「この週だけ試す」確認ダイアログの OK → API 呼出 → 完了 */
  const handleConfirmWeekOnly = async () => {
    if (!result) return;
    const proposals = result.individual_proposals;
    if (proposals.length === 0) {
      toast.info('反映可能な患者がいません');
      setStage('reviewing-summary');
      return;
    }
    setStage('week-only-applying');
    try {
      const res = await applyWeekOnlyMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeId ? [officeId] : [],
        visit_plans_per_patient: proposals.map((p) => ({
          patient_id: p.patient_id,
          visit_plans: p.proposed_pfv,
        })),
        confirm: true,
      });
      toast.success(`${res.visits_created} 件の visits を反映しました (固定枠は変更なし)`);
      setStage('completed');
      onClose();
    } catch (err) {
      toast.error(`一括反映に失敗しました: ${formatErr(err)}`);
      setStage('reviewing-summary');
    }
  };

  const weekProposalByWeekday = React.useMemo(() => {
    const map = new Map<number, V2WeekdayBeforeAfter>();
    if (result) for (const w of result.week_proposals) map.set(w.weekday, w);
    return map;
  }, [result]);

  const handleAllDone = React.useCallback(() => {
    setStage('completed');
    toast.success('すべての患者の確認が完了しました');
    onClose();
  }, [onClose]);

  const remainingPatients = React.useMemo(() => {
    if (!result) return [];
    return result.individual_proposals.filter(
      (p) =>
        !appliedPatientIds.has(p.patient_id) &&
        !rejectedPatientIds.has(p.patient_id) &&
        !skippedPatientIds.has(p.patient_id),
    );
  }, [result, appliedPatientIds, rejectedPatientIds, skippedPatientIds]);

  const handleApplyPatient = async (p: IndividualProposal) => {
    if (!result) return;
    try {
      await applyMut.mutateAsync({
        proposal_batch_id: result.proposal_batch_id,
        patient_id: p.patient_id,
        confirm: true,
        iso_year: isoYear,
        iso_week: isoWeek,
        visit_plans: p.proposed_pfv,
      });
      toast.success(`${p.patient_name} の固定枠を更新しました`);
      setAppliedPatientIds((prev) => new Set(prev).add(p.patient_id));
      // 次の患者へ進む
      const next = remainingPatients.find((x) => x.patient_id !== p.patient_id);
      setActivePatient(next ?? null);
      if (!next) handleAllDone();
    } catch (err) {
      toast.error(`採用に失敗しました: ${formatErr(err)}`);
    }
  };

  const handleRejectPatient = (p: IndividualProposal) => {
    setRejectedPatientIds((prev) => new Set(prev).add(p.patient_id));
    // 次の患者へ進む
    const next = remainingPatients.find((x) => x.patient_id !== p.patient_id);
    setActivePatient(next ?? null);
    if (!next) handleAllDone();
  };

  const handleSkipPatient = (p: IndividualProposal) => {
    setSkippedPatientIds((prev) => new Set(prev).add(p.patient_id));
    const next = remainingPatients.find((x) => x.patient_id !== p.patient_id);
    setActivePatient(next ?? null);
    if (!next) handleAllDone();
  };

  // 個別調整モードで現在の患者インデックスを計算 (進捗表示用)
  const totalPatients = result?.individual_proposals.length ?? 0;
  const currentIndex = React.useMemo(() => {
    if (!activePatient || totalPatients === 0) return 0;
    const idx = (result?.individual_proposals ?? []).findIndex(
      (p) => p.patient_id === activePatient.patient_id,
    );
    return idx >= 0 ? idx + 1 : 0;
  }, [activePatient, result, totalPatients]);

  // ─── Render ──────────────────────────────────────────────────────
  return (
    <Dialog open={open} onOpenChange={(o) => (!o ? handleClose() : undefined)}>
      <DialogContent
        className="max-h-[92vh] max-w-5xl overflow-y-auto"
        data-testid="full-optimize-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RefreshCw className="h-5 w-5 text-brand-primary" aria-hidden />
            全面最適化 - 週単位の再構築提案
            {stage === 'individual-review' ? (
              <Badge variant="secondary" className="ml-2 text-[10px]">
                {totalPatients} 件中 {currentIndex} 件目
              </Badge>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            {stage === 'individual-review'
              ? '患者一人ひとりの変更を確認してください。採用・却下・スキップを選んでください。'
              : stage === 'week-only-confirm' || stage === 'week-only-applying'
                ? 'この週の visits だけに反映します。患者マスタの固定枠は変更されません。'
                : '全 active 患者で固定枠を再算出し、移動距離・偏差を改善する提案を生成します。「この週だけ試す」を選ぶと固定枠を変更せず一括反映できます。'}
          </DialogDescription>
        </DialogHeader>

        {/* エラー */}
        {fetchMut.error ? (
          <Alert variant="destructive">
            <AlertTitle>算出に失敗しました</AlertTitle>
            <AlertDescription>{formatErr(fetchMut.error)}</AlertDescription>
          </Alert>
        ) : null}

        {/* ローディング (spinner) */}
        {stage === 'allocating' ? (
          <div
            className="flex flex-col items-center justify-center gap-2 py-12 text-sm text-text-muted"
            data-testid="full-optimize-loading"
          >
            <Loader2 className="h-6 w-6 animate-spin" aria-hidden />全 active 患者で再構築中…
            (時間がかかる場合があります)
          </div>
        ) : null}

        {/* 結果サマリー (reviewing-summary / individual-review / week-only-*) */}
        {(stage === 'reviewing-summary' ||
          stage === 'individual-review' ||
          stage === 'week-only-confirm' ||
          stage === 'week-only-applying') &&
        result ? (
          <div className="space-y-3 py-2" data-testid="full-optimize-result">
            {/* W41 v2 (Mode 2 UI 拡張): グループ化基準の説明 */}
            <div className="text-[10px] text-text-muted" data-testid="full-optimize-grouping-info">
              ℹ️ グループ化基準: 最近接 2-3 名を 1 セット / コース容量 6 名/コース
            </div>

            {/* W41 v2 (Mode 2 UI 拡張): 割当状況バナー */}
            <AssignmentSummaryBanner result={result} />

            {/* KPI バー (移動距離 / コース数 / 容量超過 / 警告) */}
            <section
              className="grid grid-cols-2 gap-2 sm:grid-cols-4"
              data-testid="full-optimize-kpi"
            >
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">移動距離 (km)</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {(result.kpi_overall.total_distance_km_before ?? 0).toFixed(1)} →{' '}
                  {(result.kpi_overall.total_distance_km_after ?? 0).toFixed(1)}
                </div>
                <div className="tnum text-[10px] text-text-muted">
                  削減 {(result.kpi_overall.distance_reduction_pct ?? 0).toFixed(1)}%
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">コース数</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.kpi_overall.courses_count_before} →{' '}
                  {result.kpi_overall.courses_count_after}
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">容量超過</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.kpi_overall.capacity_overflows} 件
                </div>
              </div>
              <div className="rounded border border-border-default p-2">
                <div className="text-[10px] text-text-muted">警告</div>
                <div className="tnum text-sm font-semibold text-text-primary">
                  {result.warnings.length} 件
                </div>
              </div>
            </section>

            {/* W41 v2 拡張: 警告 + 曜日タブ + Before/After を 1 つの Tabs で連動.
                順序は「警告 → 曜日タブ → Before/After」 (ユーザー希望)
                警告は currentTab に応じて該当曜日 (+ 曜日不問) のみ表示. */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
              {result.warnings.length > 0 ? (
                <WarningSection
                  warnings={result.warnings}
                  editedKeys={editedWarningKeys}
                  onActionClick={(w) => setEditingWarning(w)}
                  currentTab={activeTab}
                />
              ) : null}

              {/* W41 v2 拡張: 再実行誘導バナー (修正済み件数 > 0 のとき). */}
              {editedWarningCount > 0 ? (
                <div
                  className="mt-2 rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900"
                  data-testid="full-optimize-reallocate-banner"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      ✏️ 固定時間を {editedWarningCount} 件修正しました。
                      整合性確保のため全面最適化の再実行を推奨します。
                    </span>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => {
                        void handleReallocate();
                      }}
                      disabled={isBusy}
                      data-testid="full-optimize-reallocate-button"
                    >
                      <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
                      全面最適化を再実行
                    </Button>
                  </div>
                </div>
              ) : null}

              {/* 曜日タブ (警告と Before/After の間に配置) */}
              <TabsList className="mt-3 flex w-full flex-wrap gap-1 bg-bg-muted">
                {DISPLAY_WEEKDAYS.map((wd) => (
                  <TabsTrigger key={wd} value={String(wd)} data-testid={`full-optimize-tab-${wd}`}>
                    {WEEKDAY_LABELS[wd]}
                  </TabsTrigger>
                ))}
                <TabsTrigger value="all" data-testid="full-optimize-tab-all">
                  全体
                </TabsTrigger>
              </TabsList>

              {DISPLAY_WEEKDAYS.map((wd) => {
                const wp = weekProposalByWeekday.get(wd);
                return (
                  <TabsContent
                    key={wd}
                    value={String(wd)}
                    data-testid={`full-optimize-panel-${wd}`}
                  >
                    {wp ? (
                      <BeforeAfterWeekPanel weekday={wd} proposal={wp} />
                    ) : (
                      <div className="py-6 text-center text-xs text-text-muted">
                        {WEEKDAY_LABELS[wd]}曜日の提案はありません
                      </div>
                    )}
                  </TabsContent>
                );
              })}

              <TabsContent value="all" data-testid="full-optimize-panel-all">
                <AllWeekSummary proposals={result.week_proposals} />
              </TabsContent>
            </Tabs>
          </div>
        ) : null}

        {/* フッター: ステージ別アクションボタン */}
        {stage === 'reviewing-summary' ? (
          /* ── 大枠判断フッター (3 ボタン構成) ── */
          <div
            className="mt-4 rounded-lg border border-border-default bg-bg-muted p-4"
            data-testid="full-optimize-decision-panel"
          >
            <p className="mb-3 text-sm font-semibold text-text-primary">この提案を採用しますか？</p>
            <p className="mb-4 text-xs text-text-muted">
              「この週だけ試す」を選ぶと固定枠は変更せず、その週の予定だけを一括で反映します。
              「固定枠を更新する」を選ぶと、患者ごとに固定枠の更新を確認できます。
            </p>
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
              <Button
                type="button"
                variant="outline"
                size="lg"
                onClick={handleClose}
                disabled={isBusy}
                data-testid="full-optimize-decline-button"
                aria-label="変更しないでダイアログを閉じる"
              >
                <X className="mr-2 h-4 w-4" aria-hidden />
                変更しない (閉じる)
              </Button>
              <Button
                type="button"
                variant="outline"
                size="lg"
                onClick={handleRequestWeekOnly}
                disabled={isBusy || !result || result.individual_proposals.length === 0}
                data-testid="full-optimize-week-only-button"
                aria-label="この週だけ試す (固定枠は変更しない)"
                className="border-brand-primary/40 text-brand-primary hover:bg-brand-primary/5"
              >
                <CalendarRange className="mr-2 h-4 w-4" aria-hidden />
                この週だけ試す ({result?.individual_proposals.length ?? 0} 件)
              </Button>
              <Button
                type="button"
                size="lg"
                onClick={handleStartIndividualReview}
                disabled={isBusy || !result || result.individual_proposals.length === 0}
                data-testid="full-optimize-individual-button"
                aria-label="固定枠を更新する (個別に確認していく)"
              >
                <Pin className="mr-2 h-4 w-4" aria-hidden />
                固定枠を更新する ({remainingPatients.length} 件)
                <ArrowRight className="ml-2 h-4 w-4" aria-hidden />
              </Button>
            </div>
          </div>
        ) : stage === 'week-only-confirm' || stage === 'week-only-applying' ? (
          /* ── 「この週だけ試す」確認/実行中フッター ── */
          <div
            className="mt-4 rounded-lg border border-brand-primary/40 bg-brand-primary/5 p-4"
            data-testid="full-optimize-week-only-confirm-panel"
          >
            <p className="mb-2 text-sm font-semibold text-text-primary">
              この週のスケジュールを一括反映しますか？
            </p>
            <p className="mb-4 text-xs text-text-muted">
              対象週の visits を提案内容で一括上書きします。
              <span className="font-semibold text-text-primary">
                患者マスタの固定枠 (patient_fixed_visits) は変更しません。
              </span>
              来週からは元の固定枠ベースのスケジュールに戻ります。よろしいですか？
            </p>
            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={handleCancelWeekOnly}
                disabled={isApplyingWeekOnly}
                data-testid="full-optimize-week-only-cancel"
                aria-label="一括反映をキャンセル"
              >
                <X className="mr-1 h-4 w-4" aria-hidden />
                キャンセル
              </Button>
              <Button
                type="button"
                onClick={() => {
                  void handleConfirmWeekOnly();
                }}
                disabled={isApplyingWeekOnly}
                data-testid="full-optimize-week-only-confirm"
                aria-label="この週のスケジュールを一括反映する"
              >
                {isApplyingWeekOnly ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <CalendarRange className="mr-1 h-4 w-4" aria-hidden />
                )}
                この週だけ反映する
              </Button>
            </div>
          </div>
        ) : stage === 'individual-review' ? (
          /* ── 個別調整モード中フッター (中断ボタン) ── */
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button
              type="button"
              variant="outline"
              onClick={handleClose}
              disabled={isBusy}
              data-testid="full-optimize-abort-button"
              aria-label="個別調整を中断して閉じる"
            >
              <X className="mr-1 h-4 w-4" aria-hidden />
              中断して閉じる
            </Button>
          </DialogFooter>
        ) : stage === 'allocating' ? null : (
          /* ── idle / completed 時のフォールバック閉じるボタン ── */
          <DialogFooter>
            <Button type="button" variant="outline" onClick={handleClose}>
              閉じる
            </Button>
          </DialogFooter>
        )}
      </DialogContent>

      {/* 個別調整: 患者ごと Before/After ポップアップ */}
      {stage === 'individual-review' && activePatient ? (
        <IndividualPatientPopup
          proposal={activePatient}
          isApplying={isApplying}
          totalPatients={totalPatients}
          currentIndex={currentIndex}
          onApply={() => handleApplyPatient(activePatient)}
          onReject={() => handleRejectPatient(activePatient)}
          onNext={() => handleSkipPatient(activePatient)}
          onAbort={handleClose}
          remaining={remainingPatients.length}
        />
      ) : null}

      {/* W41 v2 拡張: 固定時間編集モーダル. */}
      {editingWarning ? (
        <FixedTimeEditModal
          open={!!editingWarning}
          onClose={() => setEditingWarning(null)}
          onSuccess={() => {
            if (editingWarning) {
              const k = warningKey(editingWarning);
              setEditedWarningKeys((prev) => {
                const next = new Set(prev);
                next.add(k);
                return next;
              });
              setEditedWarningCount((n) => n + 1);
            }
            setEditingWarning(null);
          }}
          warning={editingWarning}
        />
      ) : null}
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// W41 v2 拡張: 警告セクション (曜日タブ + actionable ボタン)
// ─────────────────────────────────────────────────────────────────────────

/** 警告の識別キー (修正済みバッジ追跡用). */
function warningKey(w: V2Warning): string {
  return [
    w.type,
    w.patient_id ?? '-',
    w.weekday ?? '-',
    w.current_time ?? '-',
    w.message.slice(0, 24),
  ].join('|');
}

function WarningSection({
  warnings,
  editedKeys,
  onActionClick,
  currentTab,
}: {
  warnings: V2Warning[];
  editedKeys: Set<string>;
  onActionClick: (w: V2Warning) => void;
  /** 親の曜日タブ state ('0'-'6' or 'all'). 曜日連動表示に使用. */
  currentTab: string;
}) {
  // currentTab に応じて表示する警告を絞り込み.
  //  - '0'-'6': 該当曜日 + weekday=null (曜日不問) を表示
  //  - 'all'  : 全警告を表示
  const visibleWarnings = React.useMemo(() => {
    if (currentTab === 'all') return warnings;
    const wd = Number.parseInt(currentTab, 10);
    if (Number.isNaN(wd)) return warnings;
    return warnings.filter(
      (w) => w.weekday === wd || w.weekday === null || w.weekday === undefined,
    );
  }, [warnings, currentTab]);

  // 表示ラベル (どの曜日の警告を見ているか)
  const scopeLabel =
    currentTab === 'all'
      ? '全体'
      : `${WEEKDAY_LABELS[Number.parseInt(currentTab, 10)] ?? '?'}曜日 + 曜日不問`;

  return (
    <Alert variant="warning" data-testid="full-optimize-warning-section">
      <AlertTitle className="flex items-center justify-between text-xs">
        <span>
          警告 ({visibleWarnings.length} / {warnings.length} 件)
        </span>
        <span className="text-[10px] font-normal text-text-secondary">表示中: {scopeLabel}</span>
      </AlertTitle>
      <AlertDescription>
        {visibleWarnings.length === 0 ? (
          <div className="py-2 text-center text-[11px] text-text-muted">
            ({scopeLabel} の警告はありません)
          </div>
        ) : (
          <ul className="ml-0 list-none space-y-1 text-xs">
            {visibleWarnings.map((w, i) => {
              const edited = editedKeys.has(warningKey(w));
              return (
                <li
                  key={`${i}-${w.message.slice(0, 12)}`}
                  className="flex flex-wrap items-center gap-2"
                >
                  <span className="flex-1">{w.message}</span>
                  {edited ? (
                    <Badge variant="outline" className="text-[10px] text-emerald-700">
                      ✓ 修正済み
                    </Badge>
                  ) : w.actionable ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => onActionClick(w)}
                      className="h-6 text-[10px]"
                      data-testid="full-optimize-warning-action"
                    >
                      ⏰ 固定時間を変更
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </AlertDescription>
    </Alert>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// AssignmentSummaryBanner — W41 v2 (Mode 2 UI 拡張)
//
// 全 active 患者のうち、何名が割当できて何名が未割当かをバナー表示.
// 未割当 0 → 緑バナー / 未割当あり → アンバーバナー (詳細展開可).
// ─────────────────────────────────────────────────────────────────────────

function AssignmentSummaryBanner({ result }: { result: FullOptimizeResponse }) {
  // 割当済み患者: after に少なくとも 1 visit ある = 個別提案 / week_proposals.after に出る.
  const assignedSet = new Set<string>();
  for (const wp of result.week_proposals) {
    for (const c of wp.after.courses) {
      for (const v of c.visits) {
        assignedSet.add(v.patient_id);
      }
    }
  }
  const unassignedCount = result.unassigned_patients.length;
  const assignedCount = assignedSet.size;
  const totalCount = assignedCount + unassignedCount;

  if (unassignedCount === 0) {
    return (
      <div
        className="rounded border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-800"
        data-testid="full-optimize-assignment-banner-ok"
      >
        ✅ 全 {totalCount} 名の患者を割当できました
      </div>
    );
  }
  return (
    <div
      className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
      data-testid="full-optimize-assignment-banner-warn"
    >
      ⚠️ {totalCount} 名中 {assignedCount} 名割当 / {unassignedCount} 名がプール残
      <details className="mt-2">
        <summary className="cursor-pointer text-xs">▼ 未割当患者の詳細</summary>
        <ul className="mt-2 space-y-1 text-xs" data-testid="full-optimize-unassigned-list">
          {result.unassigned_patients.map((p) => (
            <li key={p.patient_id}>
              ・<span className="font-semibold">{p.patient_code ?? '—'}</span> {p.patient_name}
              {' — 理由: '}
              <span className="text-amber-700">{p.reason}</span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// BeforeAfterWeekPanel — 曜日タブの中身 (Before / After を 2 列で並べる)
//
// Backend は ``before/after = { courses: V2CourseSummary[] }`` で渡してくる.
// ─────────────────────────────────────────────────────────────────────────

function totalsFor(courses: V2CourseSummary[]): { distance: number; visits: number } {
  let distance = 0;
  let visits = 0;
  for (const c of courses) {
    distance += c.distance_km;
    visits += c.visits_count;
  }
  return { distance, visits };
}

function BeforeAfterWeekPanel({
  weekday,
  proposal,
}: {
  weekday: number;
  proposal: V2WeekdayBeforeAfter;
}) {
  const beforeTotal = totalsFor(proposal.before.courses);
  const afterTotal = totalsFor(proposal.after.courses);
  return (
    <div
      className="grid grid-cols-1 gap-2 md:grid-cols-2"
      data-testid={`full-optimize-before-after-${weekday}`}
    >
      <CourseListColumn
        title="Before"
        courses={proposal.before.courses}
        total={beforeTotal}
        tone="muted"
      />
      <CourseListColumn
        title="After"
        courses={proposal.after.courses}
        total={afterTotal}
        tone="primary"
      />
      <div className="md:col-span-2 rounded border border-border-default p-2 text-xs">
        <div className="font-semibold text-text-primary">変化</div>
        <ul className="ml-4 mt-1 list-disc text-text-secondary">
          <li>距離: {formatDelta(afterTotal.distance - beforeTotal.distance)}</li>
          <li>
            訪問数: {beforeTotal.visits} → {afterTotal.visits}
          </li>
        </ul>
      </div>
    </div>
  );
}

function CourseListColumn({
  title,
  courses,
  total,
  tone,
}: {
  title: string;
  courses: V2CourseSummary[];
  total: { distance: number; visits: number };
  tone: 'muted' | 'primary';
}) {
  const headerCls =
    tone === 'primary'
      ? 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
      : 'border-border-default bg-bg-muted text-text-muted';
  // W41 v2 (Mode 2 Before/After 表示拡張): Backend がソート済みでも
  // (拠点名, コード) で安全に再ソート (順序の安定性確保).
  const sortedCourses = React.useMemo(
    () =>
      [...courses].sort((a, b) => {
        const ofA = a.office_name ?? a.office_id;
        const ofB = b.office_name ?? b.office_id;
        if (ofA !== ofB) return ofA.localeCompare(ofB);
        return (a.code ?? 'Z').localeCompare(b.code ?? 'Z');
      }),
    [courses],
  );
  return (
    <div className="overflow-hidden rounded border border-border-default">
      <div
        className={`flex items-center justify-between border-b px-2 py-1 text-[11px] font-semibold ${headerCls}`}
      >
        <span>{title}</span>
        <span className="tnum text-text-secondary">
          {total.visits}件 / {total.distance.toFixed(1)}km
        </span>
      </div>
      {sortedCourses.length === 0 ? (
        <div className="py-4 text-center text-[11px] text-text-muted">(コースなし)</div>
      ) : (
        <ul className="divide-y divide-border-default">
          {sortedCourses.map((c) => (
            <li
              key={`${c.office_id}-${c.code}-${c.assigned_staff_id ?? 'none'}`}
              className="px-2 py-1.5"
            >
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-semibold text-text-primary">
                  {c.office_name ?? '不明'} {c.code} コース
                </span>
                <span className="tnum text-text-muted">
                  {c.visits_count}件 / {c.distance_km.toFixed(1)}km
                </span>
              </div>
              {c.visits.length > 0 ? (
                <ul className="mt-1 space-y-0.5">
                  {c.visits.slice(0, 8).map((v: V2VisitForUI, i, arr) => {
                    // W41 v2 (H2 視覚化): 同住所グループの連続表示
                    const prev = arr[i - 1];
                    const next = arr[i + 1];
                    const inGroup = !!v.same_address_group_id;
                    const sameAsPrev =
                      inGroup && prev?.same_address_group_id === v.same_address_group_id;
                    const sameAsNext =
                      inGroup && next?.same_address_group_id === v.same_address_group_id;
                    const isGroupStart = inGroup && !sameAsPrev;
                    const isGroupEnd = inGroup && !sameAsNext;
                    // グループ先頭で患者数を計算 (slice 前の c.visits 全体を参照)
                    let groupSize = 0;
                    if (isGroupStart) {
                      for (const fullV of c.visits) {
                        if (fullV.same_address_group_id === v.same_address_group_id) {
                          groupSize += 1;
                        }
                      }
                    }
                    return (
                      <li
                        key={i}
                        className={cn(
                          'flex flex-wrap items-center gap-1 text-[10px]',
                          inGroup && 'border-l-2 border-yellow-400 bg-yellow-50/60 pl-2',
                          isGroupStart && 'pt-1 mt-1',
                          isGroupEnd && 'pb-1 mb-1',
                        )}
                      >
                        {isGroupStart && groupSize >= 2 ? (
                          <span className="w-full text-[9px] font-semibold text-yellow-700">
                            📍 同住所グループ ({groupSize} 名)
                          </span>
                        ) : null}
                        <span className="tnum text-text-muted">{trimSeconds(v.start_time)}</span>
                        <span className="text-text-primary">{v.patient_name}</span>
                        {v.area_label ? (
                          <span className="rounded bg-brand-primary/10 px-1 text-[9px] text-brand-primary">
                            {v.area_label}
                          </span>
                        ) : null}
                        {v.address ? (
                          <span
                            className="text-[9px] text-text-muted"
                            title={v.address}
                            aria-label={`住所 ${v.address}`}
                          >
                            {v.address.length > 18 ? `${v.address.slice(0, 18)}…` : v.address}
                          </span>
                        ) : null}
                        {(() => {
                          const label = formatTimeCondition(v);
                          return label ? (
                            <span className="text-[9px] text-text-secondary">{label}</span>
                          ) : null;
                        })()}
                        {v.sex_restriction === 'female_only' ? (
                          <span className="text-[9px] text-pink-600">👩 女性のみ</span>
                        ) : null}
                        {v.sex_restriction === 'male_only' ? (
                          <span className="text-[9px] text-blue-600">👨 男性のみ</span>
                        ) : null}
                        {/* W41 v2 (訪問間距離 行内右端版): 次の patient までの距離.
                            行と行の間に挟まず、各行の右端にコンパクトに表示. */}
                        <VisitArrow distanceKm={v.distance_to_next_km} />
                      </li>
                    );
                  })}
                  {c.visits.length > 8 ? (
                    <li className="text-[10px] text-text-muted">…他 {c.visits.length - 8} 件</li>
                  ) : null}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// WeekdaySummaryTable — 上部固定: 曜日別 距離 / 訪問数 サマリー表
//
// 「どの曜日でどれだけ距離が削減されたか」「訪問数の Before/After」を
// 一覧表で俯瞰. ユーザー希望により全面最適化結果の上部に常時表示.
// ─────────────────────────────────────────────────────────────────────────

function WeekdaySummaryTable({ proposals }: { proposals: V2WeekdayBeforeAfter[] }) {
  const sorted = React.useMemo(
    () => [...proposals].sort((a, b) => a.weekday - b.weekday),
    [proposals],
  );
  if (sorted.length === 0) return null;
  return (
    <div
      className="overflow-x-auto rounded border border-border-default"
      data-testid="full-optimize-weekday-summary"
    >
      <table className="w-full text-xs">
        <thead className="bg-bg-muted text-[10px] text-text-muted">
          <tr>
            <th className="px-2 py-1 text-left">曜日</th>
            <th className="px-2 py-1 text-right">Before km</th>
            <th className="px-2 py-1 text-right">After km</th>
            <th className="px-2 py-1 text-right">距離 Δ</th>
            <th className="px-2 py-1 text-right">訪問 Before</th>
            <th className="px-2 py-1 text-right">訪問 After</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((p) => {
            const b = totalsFor(p.before.courses);
            const a = totalsFor(p.after.courses);
            return (
              <tr key={p.weekday} className="border-t border-border-default">
                <td className="px-2 py-1">{fmtWd(p.weekday)}曜日</td>
                <td className="tnum px-2 py-1 text-right">{b.distance.toFixed(1)}</td>
                <td className="tnum px-2 py-1 text-right">{a.distance.toFixed(1)}</td>
                <td className="tnum px-2 py-1 text-right">
                  {formatDelta(a.distance - b.distance)}
                </td>
                <td className="tnum px-2 py-1 text-right">{b.visits}</td>
                <td className="tnum px-2 py-1 text-right">{a.visits}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// AllWeekSummary — 「全体」タブ: コース × 曜日 週カレンダー Before/After (縦積み)
//
// `/schedule` の「週」ビュー風に、コース行 × 曜日列 (月〜土) で提案結果を
// 俯瞰する. Before/After を縦に並べて差を比較しやすくする.
// 行軸は「拠点 + コードコース」(例: 稲毛 A コース) ── スタッフ未アサインでも
// コース割当は完了しているため、コース粒度で俯瞰する.
// 各コースに担当スタッフ名がある場合は行ラベルに併記.
// ─────────────────────────────────────────────────────────────────────────

function AllWeekSummary({ proposals }: { proposals: V2WeekdayBeforeAfter[] }) {
  // staff_id → 表示名 (氏名) を staff master から構築.
  const staffQuery = useStaffList({ limit: 500 });
  const staffNameById = React.useMemo(() => {
    const m = new Map<string, string>();
    for (const s of staffQuery.data ?? []) {
      m.set(s.id, s.name);
    }
    return m;
  }, [staffQuery.data]);

  if (proposals.length === 0) {
    return <div className="py-4 text-center text-xs text-text-muted">提案がありません</div>;
  }
  return (
    <div className="space-y-3" data-testid="full-optimize-all-summary">
      {/* 曜日別サマリー表 (「全体」タブ内のみ表示) */}
      <WeekdaySummaryTable proposals={proposals} />
      <ProposalWeekCalendar proposals={proposals} side="before" staffNameById={staffNameById} />
      <ProposalWeekCalendar proposals={proposals} side="after" staffNameById={staffNameById} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// IndividualPatientPopup — 個別調整モードのポップアップ
// ─────────────────────────────────────────────────────────────────────────

interface IndividualPatientPopupProps {
  proposal: IndividualProposal;
  isApplying: boolean;
  remaining: number;
  totalPatients: number;
  currentIndex: number;
  onApply: () => void;
  onReject: () => void;
  onNext: () => void;
  onAbort: () => void;
}

function VisitPlanList({
  title,
  plans,
  tone,
}: {
  title: string;
  plans: V2VisitPlan[];
  tone: 'muted' | 'primary';
}) {
  const cls =
    tone === 'primary'
      ? 'border-brand-primary/40 bg-brand-primary/5'
      : 'border-border-default bg-bg-muted';
  return (
    <div className={`rounded border p-2 text-xs ${cls}`}>
      <div className="text-[10px] font-semibold uppercase text-text-muted">{title}</div>
      {plans.length === 0 ? (
        <div className="mt-1 text-[11px] text-text-muted">(なし)</div>
      ) : (
        <ul className="mt-1 space-y-0.5">
          {plans.map((v, i) => (
            <li key={i} className="tnum flex justify-between">
              <span>
                {fmtWd(v.weekday)} {trimSeconds(v.start_time)}
              </span>
              <span className="text-text-muted">{v.course_code}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function IndividualPatientPopup({
  proposal,
  isApplying,
  remaining,
  totalPatients,
  currentIndex,
  onApply,
  onReject,
  onNext,
  onAbort,
}: IndividualPatientPopupProps) {
  const proposedSummary = proposal.proposed_pfv[0];
  return (
    <Dialog open onOpenChange={(o) => (!o ? onAbort() : undefined)}>
      <DialogContent
        className="flex max-h-[90vh] max-w-md flex-col"
        data-testid={`full-optimize-popup-${proposal.patient_id}`}
      >
        <DialogHeader className="flex-none">
          <DialogTitle className="text-base">
            {proposal.patient_name} 様
            <Badge variant="secondary" className="ml-2 text-[10px]">
              {totalPatients} 件中 {currentIndex} 件目 (残り {remaining} 件)
            </Badge>
          </DialogTitle>
          <DialogDescription>
            {proposedSummary
              ? `提案: ${fmtWd(proposedSummary.weekday)} ${trimSeconds(proposedSummary.start_time)} ${proposedSummary.course_code} コース`
              : '提案 visit がありません'}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-1 py-2 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <VisitPlanList title="Before" plans={proposal.current_pfv} tone="muted" />
            <VisitPlanList title="After" plans={proposal.proposed_pfv} tone="primary" />
          </div>

          <div className="rounded border border-border-default p-2 text-xs">
            <div className="font-semibold text-text-primary">影響</div>
            <ul className="ml-4 mt-1 list-disc space-y-0.5 text-text-secondary">
              <li>距離: {formatDelta(proposal.delta.distance_km)}</li>
              {proposal.delta.capacity ? <li>容量: {proposal.delta.capacity}</li> : null}
              <li>
                訪問数: {proposal.delta.course_visits_count_before} →{' '}
                {proposal.delta.course_visits_count_after}
              </li>
            </ul>
          </div>

          {proposal.warnings.length > 0 ? (
            <Alert variant="warning">
              <AlertTitle className="text-xs">警告</AlertTitle>
              <AlertDescription>
                <ul className="ml-4 list-disc space-y-0.5 text-xs">
                  {proposal.warnings.map((w, i) => (
                    <li key={i}>{w.message}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          ) : null}
        </div>

        <DialogFooter className="flex-none flex-wrap gap-2 border-t pt-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onAbort}
            disabled={isApplying}
            data-testid="full-optimize-popup-abort"
            aria-label="個別調整を中断して閉じる"
            className="text-text-muted"
          >
            <X className="mr-1 h-4 w-4" aria-hidden />
            中断して閉じる
          </Button>
          <div className="flex gap-2 sm:ml-auto">
            <Button
              type="button"
              variant="outline"
              onClick={onReject}
              disabled={isApplying}
              data-testid="full-optimize-popup-reject"
              aria-label="この患者の変更を却下して次へ"
            >
              <X className="mr-1 h-4 w-4" aria-hidden />
              この患者は変更しない
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={onNext}
              disabled={isApplying}
              data-testid="full-optimize-popup-next"
              aria-label="この患者をスキップして次へ"
            >
              <ArrowRight className="mr-1 h-4 w-4" aria-hidden />
              スキップ
            </Button>
            <Button
              type="button"
              onClick={onApply}
              disabled={isApplying || proposal.proposed_pfv.length === 0}
              data-testid="full-optimize-popup-apply"
              aria-label="この患者の変更を採用して次へ"
            >
              {isApplying ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <CheckCircle2 className="mr-1 h-4 w-4" aria-hidden />
              )}
              この患者は変更する
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
