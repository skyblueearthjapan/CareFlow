'use client';

/**
 * DiffAddDialog — Wave 41 v2 (差分追加モード, 機能 A).
 *
 * 仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §3, §13.5.1
 *
 * フロー:
 *   1. 開くと POST /diff-add で全プール患者の提案を取得 (BE 側で write なし)
 *   2. 候補リスト (患者ごと 1 行) を表示
 *   3. リストから 1 件選択 → Before/After ポップアップ
 *   4. 「採用」押下 → POST /apply-individual で当該患者の固定枠を更新 → リストから除去
 *   5. すべて回ったら閉じる
 *
 * 一括採用ボタンは設けない (Q2 確定: 1 件ずつ採用).
 *
 * RBAC: 呼出側 (CourseDayTablePanel) で admin/manager ガード済み.
 */
import * as React from 'react';
import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  X,
} from 'lucide-react';
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
import {
  useApplyIndividualMutation,
  useDiffAddProposalsMutation,
} from '@/lib/queries/autoScheduleV2';
import type { DiffAddProposal } from '@/lib/schemas/v2/autoScheduleV2';
import { V2_WARNING_CATEGORY_LABEL_JA } from '@/lib/schemas/v2/autoScheduleV2';

import { DiffAddProposalTimeline } from './DiffAddProposalTimeline';
import { formatDelta, formatErr, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function formatSuggestedLine(p: DiffAddProposal): string {
  const wd = WEEKDAY_LABELS[p.suggested.weekday] ?? '?';
  return `${wd} ${trimSeconds(p.suggested.start_time)} ${p.suggested.course_code} コース`;
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface DiffAddDialogProps {
  open: boolean;
  onClose: () => void;
  isoYear: number;
  isoWeek: number;
  /** 単一拠点モード時の対象拠点 ID. null = 全拠点 (BE 側は office_ids 空配列で全拠点扱い). */
  officeId: string | null;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function DiffAddDialog({ open, onClose, isoYear, isoWeek, officeId }: DiffAddDialogProps) {
  const fetchMut = useDiffAddProposalsMutation();
  const applyMut = useApplyIndividualMutation();

  // 候補リスト. apply 採用後にローカルから取り除く (画面差分のみ).
  const [proposals, setProposals] = React.useState<DiffAddProposal[]>([]);
  // ポップアップ対象の提案. null = 一覧表示中.
  const [activeProposal, setActiveProposal] = React.useState<DiffAddProposal | null>(null);
  // 採用済み件数 (UI 表示用).
  const [appliedCount, setAppliedCount] = React.useState(0);
  // タイムライン展開中の proposal_id (1 件のみ展開可: 折り畳み挙動).
  // 候補が多いケースでも一覧の把握を優先し、複数同時展開はしない.
  const [expandedProposalId, setExpandedProposalId] = React.useState<string | null>(null);

  // open のたびに state リセット + 候補取得.
  React.useEffect(() => {
    if (!open) return;
    setProposals([]);
    setActiveProposal(null);
    setAppliedCount(0);
    setExpandedProposalId(null);
    fetchMut.reset();
    applyMut.reset();
    void (async () => {
      try {
        const res = await fetchMut.mutateAsync({
          iso_year: isoYear,
          iso_week: isoWeek,
          office_ids: officeId ? [officeId] : [],
        });
        setProposals(res.proposals);
      } catch (err) {
        toast.error(`候補取得に失敗しました: ${formatErr(err)}`);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isoYear, isoWeek, officeId]);

  const isLoading = fetchMut.isPending;
  const isApplying = applyMut.isPending;
  const isBusy = isLoading || isApplying;

  const handleClose = () => {
    if (isBusy) return;
    onClose();
  };

  const handleApply = async (p: DiffAddProposal) => {
    try {
      // BE は visit_plans の上書き (stateless) を要求. suggested_visits があれば
      // それを、無ければ suggested 1 件を送る.
      const visitPlans = p.suggested_visits.length > 0 ? p.suggested_visits : [p.suggested];
      await applyMut.mutateAsync({
        proposal_id: p.proposal_id,
        patient_id: p.patient_id,
        confirm: true,
        iso_year: isoYear,
        iso_week: isoWeek,
        visit_plans: visitPlans,
      });
      toast.success(`${p.patient_name} の固定枠を更新しました`);
      // ローカルリストから該当行を取り除く + popup を閉じる.
      setProposals((prev) => prev.filter((x) => x.proposal_id !== p.proposal_id));
      setActiveProposal(null);
      setAppliedCount((c) => c + 1);
    } catch (err) {
      toast.error(`採用に失敗しました: ${formatErr(err)}`);
    }
  };

  // ─── Render ──────────────────────────────────────────────────────
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) handleClose();
      }}
    >
      <DialogContent
        className="max-h-[90vh] max-w-3xl overflow-y-auto"
        data-testid="diff-add-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Plus className="h-5 w-5 text-brand-primary" aria-hidden />
            プール投入 - プール患者の候補
          </DialogTitle>
          <DialogDescription>
            固定枠未登録の患者をプール抽出し、既存スケジュールの隙間に最適配置します。 行を
            クリックすると対象コースの 1 日タイムライン (= 既存訪問の流れ + 挿入位置) を
            確認できます。 「採用」は Before/After ポップアップから 1 件ずつ実行してください。
          </DialogDescription>
        </DialogHeader>

        {/* エラー (取得失敗) */}
        {fetchMut.error ? (
          <Alert variant="destructive">
            <AlertTitle>候補の取得に失敗しました</AlertTitle>
            <AlertDescription>{formatErr(fetchMut.error)}</AlertDescription>
          </Alert>
        ) : null}

        {/* ローディング */}
        {isLoading ? (
          <div
            className="flex items-center justify-center gap-2 py-8 text-sm text-text-muted"
            data-testid="diff-add-loading"
          >
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            プール患者から候補を算出中…
          </div>
        ) : (
          <div className="space-y-2 py-2" data-testid="diff-add-list">
            {/* サマリ */}
            <div className="flex items-center justify-between border-b border-border-default pb-2">
              <span className="text-sm font-semibold text-text-primary">
                プール投入 候補: {proposals.length} 件
              </span>
              {appliedCount > 0 ? (
                <Badge variant="secondary" className="text-[10px]">
                  採用済: {appliedCount} 件
                </Badge>
              ) : null}
            </div>

            {/* リスト本体 */}
            {proposals.length === 0 ? (
              <div className="py-6 text-center text-sm text-text-muted">
                {appliedCount > 0
                  ? 'すべての候補を処理しました。'
                  : 'プール患者の候補はありません。'}
              </div>
            ) : (
              <ul className="divide-y divide-border-default rounded border border-border-default">
                {proposals.map((p) => {
                  const isExpanded = expandedProposalId === p.proposal_id;
                  return (
                    <li key={p.proposal_id}>
                      <div
                        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm"
                        data-testid={`diff-add-item-${p.proposal_id}`}
                      >
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedProposalId((cur) =>
                              cur === p.proposal_id ? null : p.proposal_id,
                            )
                          }
                          aria-expanded={isExpanded}
                          aria-label={`${p.patient_name} のタイムラインを${isExpanded ? '閉じる' : '開く'}`}
                          data-testid={`diff-add-item-${p.proposal_id}-toggle`}
                          className="flex flex-1 items-center gap-2 rounded hover:bg-bg-muted focus:bg-bg-muted focus:outline-none"
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4 text-text-muted" aria-hidden />
                          ) : (
                            <ChevronRight className="h-4 w-4 text-text-muted" aria-hidden />
                          )}
                          <div className="flex min-w-0 flex-col">
                            <span className="truncate font-medium text-text-primary">
                              {p.patient_name}
                            </span>
                            <span className="truncate text-xs text-text-muted">
                              {formatSuggestedLine(p)}
                            </span>
                          </div>
                        </button>
                        <div className="flex shrink-0 items-center gap-2">
                          {p.warnings.length > 0 ? (
                            <Badge variant="destructive" className="text-[10px]">
                              警告 {p.warnings.length}
                            </Badge>
                          ) : null}
                          <span className="tnum text-[11px] text-text-muted">
                            {formatDelta(p.delta.distance_km)}
                          </span>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => setActiveProposal(p)}
                            data-testid={`diff-add-item-${p.proposal_id}-apply`}
                            className="h-7 px-2 text-xs"
                          >
                            採用へ
                            <ArrowRight className="ml-1 h-3 w-3" aria-hidden />
                          </Button>
                        </div>
                      </div>
                      {isExpanded ? (
                        <div className="overflow-x-auto border-t border-border-default bg-bg-muted/20">
                          <DiffAddProposalTimeline
                            proposal={p}
                            isoYear={isoYear}
                            isoWeek={isoWeek}
                            visits={
                              p.suggested_visits.length > 0 ? p.suggested_visits : [p.suggested]
                            }
                          />
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={handleClose} disabled={isBusy}>
            閉じる
          </Button>
        </DialogFooter>
      </DialogContent>

      {/* Before/After ポップアップ (子 Dialog) */}
      {activeProposal ? (
        <ProposalPopup
          proposal={activeProposal}
          isApplying={isApplying}
          onCancel={() => setActiveProposal(null)}
          onApply={() => handleApply(activeProposal)}
        />
      ) : null}
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// ProposalPopup (Before/After)
//
// 仕様書 §13.5.1 のワイヤフレームに対応.
// ─────────────────────────────────────────────────────────────────────────

interface ProposalPopupProps {
  proposal: DiffAddProposal;
  isApplying: boolean;
  onCancel: () => void;
  onApply: () => void;
}

function ProposalPopup({ proposal, isApplying, onCancel, onApply }: ProposalPopupProps) {
  return (
    <Dialog open onOpenChange={(o) => (!o ? onCancel() : undefined)}>
      <DialogContent className="max-w-md" data-testid={`diff-add-popup-${proposal.proposal_id}`}>
        <DialogHeader>
          <DialogTitle className="text-base">{proposal.patient_name} 様 (新規)</DialogTitle>
          <DialogDescription>提案: {formatSuggestedLine(proposal)}</DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-2 text-xs">
          {/* Before */}
          <div className="rounded border border-border-default bg-bg-muted p-2">
            <div className="text-[10px] font-semibold text-text-muted">Before</div>
            <div className="tnum mt-1">
              <div>訪問数: {proposal.before_summary.course_visits_count}</div>
              <div>距離: {proposal.before_summary.distance_km.toFixed(1)} km</div>
            </div>
          </div>
          {/* After */}
          <div className="rounded border border-brand-primary/40 bg-brand-primary/5 p-2">
            <div className="text-[10px] font-semibold text-brand-primary">After</div>
            <div className="tnum mt-1">
              <div>訪問数: {proposal.after_summary.course_visits_count}</div>
              <div>距離: {proposal.after_summary.distance_km.toFixed(1)} km</div>
            </div>
          </div>
        </div>

        <div className="rounded border border-border-default p-2 text-xs">
          <div className="font-semibold text-text-primary">影響</div>
          <ul className="ml-4 mt-1 list-disc space-y-0.5 text-text-secondary">
            <li>距離: {formatDelta(proposal.delta.distance_km)}</li>
            {proposal.delta.capacity ? <li>容量: {proposal.delta.capacity}</li> : null}
          </ul>
        </div>

        {proposal.warnings.length > 0 ? (
          <Alert variant="warning">
            <AlertTitle className="text-xs">警告</AlertTitle>
            <AlertDescription>
              <ul className="ml-0 list-none space-y-1 text-xs">
                {proposal.warnings.map((w, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-1">
                    {/* Wave 4 (Phase C): category バッジ. */}
                    <Badge
                      variant="outline"
                      className="text-[10px] text-text-secondary"
                      data-testid={`diff-add-warning-category-badge-${w.category}`}
                    >
                      {V2_WARNING_CATEGORY_LABEL_JA[w.category]}
                    </Badge>
                    <span>{w.message}</span>
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onCancel}
            disabled={isApplying}
            data-testid="diff-add-popup-cancel"
          >
            <X className="mr-1 h-4 w-4" aria-hidden />
            変更しない
          </Button>
          <Button
            type="button"
            onClick={onApply}
            disabled={isApplying}
            data-testid="diff-add-popup-apply"
          >
            {isApplying ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
            ) : (
              <CheckCircle2 className="mr-1 h-4 w-4" aria-hidden />
            )}
            この患者を採用
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
