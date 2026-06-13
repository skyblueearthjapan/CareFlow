'use client';

/**
 * DiffAddDialog — Wave 41 v2 (差分追加モード, 機能 A) / Phase G-92 FE 改修.
 *
 * 仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2 §3, §13.5.1
 *
 * Phase G-92 FE: 「自動スタッフ割付」 レビュー (AssignWarningDialog) と同じ
 * コースカード様式に作り替えた。 患者ごとに対象コースの 1 日タイムライン
 * (= 既存訪問の流れ + 黄色のゴースト差し込み) を内包したカードを並べ、
 * proposal_source で色分け / 文言を出し分ける:
 *   - 'fixed'                    → ✅緑系「固定枠で入れられます」.
 *   - 'fixed_fallback_preferred' → 🔴赤系で固定枠不可理由 + 🟡黄系で希望枠案の 2 段.
 *   - 'preferred'                → 🟡黄系「希望枠で入れられます」.
 *
 * フロー (Phase G-92 でも非破壊):
 *   1. 開くと POST /diff-add で全プール患者の提案を取得 (BE 側で write なし).
 *   2. 患者ごとのコースカードを表示 (各カードに 1 日タイムライン内蔵).
 *   3. カードの「この枠で採用」 → 確認モーダル → POST /apply-individual で
 *      当該患者の固定枠を更新 → カードを除去.
 *   4. すべて回ったら閉じる.
 *
 * 一括採用ボタンは設けない (Q2 確定: 1 件ずつ採用).
 *
 * RBAC: 呼出側 (CourseDayTablePanel) で admin/manager ガード済み.
 */
import * as React from 'react';
import { CheckCircle2, Loader2, Plus, X } from 'lucide-react';
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
import type { DiffAddProposal, DiffAddProposalSource } from '@/lib/schemas/v2/autoScheduleV2';
import {
  V2_DIFF_ADD_FIXED_UNAVAILABLE_REASON_LABEL_JA,
  V2_WARNING_CATEGORY_LABEL_JA,
} from '@/lib/schemas/v2/autoScheduleV2';
import { cn } from '@/lib/utils';

import { DiffAddProposalTimeline } from './DiffAddProposalTimeline';
import { formatDelta, formatErr, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function fmtWeekday(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? '?';
}

function formatSuggestedLine(p: DiffAddProposal): string {
  const wd = fmtWeekday(p.suggested.weekday);
  return `${wd} ${trimSeconds(p.suggested.start_time)} ${p.suggested.course_code} コース`;
}

/** proposal_source 別の見た目 (色 / バッジ / 見出しアイコン + 文言). */
interface SourceStyle {
  /** カード枠 + 背景 tone. */
  cardClass: string;
  /** ヘッダーバッジ variant. */
  badgeVariant: 'success' | 'warning' | 'destructive';
  /** ヘッダーバッジ文言. */
  badgeLabel: string;
  /** 見出しアイコン (絵文字). */
  icon: string;
  /** 見出し文言 (固定枠/希望枠 配置の説明). */
  headline: string;
}

/**
 * proposal_source ごとの基準スタイル.
 * fixed_fallback_preferred は赤+黄の 2 段表示を別途カード内で描くため、
 * ここではカード枠を赤系 (固定枠不可が主因) にしておく.
 */
function sourceStyle(source: DiffAddProposalSource, suggestedLine: string): SourceStyle {
  if (source === 'fixed') {
    return {
      cardClass: 'border-success/40 bg-success/5',
      badgeVariant: 'success',
      badgeLabel: '固定枠',
      icon: '✅',
      headline: `固定枠で入れられます (${suggestedLine})`,
    };
  }
  if (source === 'fixed_fallback_preferred') {
    return {
      cardClass: 'border-error/40 bg-error/5',
      badgeVariant: 'destructive',
      badgeLabel: '固定枠NG',
      icon: '🔴',
      headline: '固定枠では入れられませんでした',
    };
  }
  // 'preferred'
  return {
    cardClass: 'border-warning/40 bg-warning/5',
    badgeVariant: 'warning',
    badgeLabel: '希望枠',
    icon: '🟡',
    headline: `希望枠で入れられます (${suggestedLine})`,
  };
}

/** 固定枠不可理由コード → 日本語ラベル (未知コードはそのまま表示). */
function fmtFixedUnavailableReason(code: string): string {
  return V2_DIFF_ADD_FIXED_UNAVAILABLE_REASON_LABEL_JA[code] ?? code;
}

/**
 * 採用 / タイムライン描画に使う visit_plans を決定する単一ソース.
 * suggested_visits があればそれを、無ければ suggested 1 件を使う.
 * BE は visit_plans の上書き (stateless) を要求するため handleApply と描画で共有.
 */
function adoptedVisitPlans(p: DiffAddProposal) {
  return p.suggested_visits.length > 0 ? p.suggested_visits : [p.suggested];
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
  // 確認モーダル対象の提案. null = 一覧表示中.
  const [confirmTarget, setConfirmTarget] = React.useState<DiffAddProposal | null>(null);
  // 採用済み件数 (UI 表示用).
  const [appliedCount, setAppliedCount] = React.useState(0);

  // open のたびに state リセット + 候補取得.
  React.useEffect(() => {
    if (!open) return;
    setProposals([]);
    setConfirmTarget(null);
    setAppliedCount(0);
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
      // それを、無ければ suggested 1 件を送る (描画と同一ソース).
      const visitPlans = adoptedVisitPlans(p);
      await applyMut.mutateAsync({
        proposal_id: p.proposal_id,
        patient_id: p.patient_id,
        confirm: true,
        iso_year: isoYear,
        iso_week: isoWeek,
        visit_plans: visitPlans,
      });
      toast.success(`${p.patient_name} の固定枠を更新しました`);
      // ローカルリストから該当カードを取り除く + モーダルを閉じる.
      setProposals((prev) => prev.filter((x) => x.proposal_id !== p.proposal_id));
      setConfirmTarget(null);
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
            固定枠未登録の患者をプール抽出し、既存スケジュールの隙間に最適配置します。
            各カードは対象コースの 1 日タイムライン (= 既存訪問の流れ + 黄色の挿入位置) を
            内蔵しています。「この枠で採用」は確認のうえ 1 件ずつ実行してください。
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
          <div className="space-y-3 py-2" data-testid="diff-add-list">
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

            {/* カード本体 */}
            {proposals.length === 0 ? (
              <div className="py-6 text-center text-sm text-text-muted">
                {appliedCount > 0
                  ? 'すべての候補を処理しました。'
                  : 'プール患者の候補はありません。'}
              </div>
            ) : (
              <ul className="space-y-3">
                {proposals.map((p) => (
                  <ProposalCard
                    key={p.proposal_id}
                    proposal={p}
                    isoYear={isoYear}
                    isoWeek={isoWeek}
                    isBusy={isBusy}
                    onAdopt={() => setConfirmTarget(p)}
                  />
                ))}
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

      {/* 採用確認モーダル (1 回; AssignWarningDialog の視覚言語に合わせる). */}
      {confirmTarget ? (
        <ProposalConfirmModal
          proposal={confirmTarget}
          isApplying={isApplying}
          onCancel={() => setConfirmTarget(null)}
          onApply={() => handleApply(confirmTarget)}
        />
      ) : null}
    </Dialog>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// ProposalCard — 患者 1 人 = 1 コースカード (タイムライン内蔵)
//
// AssignWarningDialog の ReviewCard と同じ視覚言語 (色分け枠 / ヘッダーバッジ /
// 原因マーク). proposal_source で色 / 文言を出し分ける.
// ─────────────────────────────────────────────────────────────────────────

interface ProposalCardProps {
  proposal: DiffAddProposal;
  isoYear: number;
  isoWeek: number;
  isBusy: boolean;
  /** 「この枠で採用」 押下 (= 確認モーダルを開く). */
  onAdopt: () => void;
}

function ProposalCard({ proposal, isoYear, isoWeek, isBusy, onAdopt }: ProposalCardProps) {
  const suggestedLine = formatSuggestedLine(proposal);
  const style = sourceStyle(proposal.proposal_source, suggestedLine);
  const isFallback = proposal.proposal_source === 'fixed_fallback_preferred';
  const reasons = proposal.fixed_unavailable_reasons;

  return (
    <li
      className={cn('rounded-md border p-3 text-xs', style.cardClass)}
      data-testid={`diff-add-card-${proposal.proposal_id}`}
      data-source={proposal.proposal_source}
    >
      {/* ヘッダ行: 患者名 + ソースバッジ + 距離差分. */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant={style.badgeVariant} className="text-[10px]">
          {style.badgeLabel}
        </Badge>
        <span className="font-medium text-text-primary">{proposal.patient_name} 様</span>
        {proposal.warnings.length > 0 ? (
          <Badge variant="destructive" className="text-[10px]">
            警告 {proposal.warnings.length}
          </Badge>
        ) : null}
        <span className="tnum ml-auto text-[11px] text-text-muted">
          {formatDelta(proposal.delta.distance_km)}
        </span>
      </div>

      {/* 見出し (固定/希望 の配置説明). */}
      <div
        className="mb-2 flex items-center gap-1.5 font-semibold text-text-primary"
        data-testid={`diff-add-card-${proposal.proposal_id}-headline`}
      >
        <span aria-hidden>{style.icon}</span>
        <span>{style.headline}</span>
      </div>

      {/* fixed_fallback_preferred のみ: 🔴固定枠不可理由 + 🟡希望枠案 の 2 段表示. */}
      {isFallback ? (
        <div className="mb-2 space-y-1.5">
          {/* 🔴 固定枠が入らない理由 (理由不明=空なら中立表示). */}
          <div
            className="rounded border border-error/40 bg-error/5 px-2 py-1"
            data-testid={`diff-add-card-${proposal.proposal_id}-fixed-reason`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span aria-hidden>🔴</span>
              {reasons.length > 0 ? (
                <>
                  <span className="text-text-secondary">固定枠は</span>
                  {[...new Set(reasons)].map((code) => (
                    <Badge
                      key={code}
                      variant="destructive"
                      className="text-[10px]"
                      data-testid={`diff-add-card-${proposal.proposal_id}-reason-${code}`}
                    >
                      {fmtFixedUnavailableReason(code)}
                    </Badge>
                  ))}
                  <span className="text-text-secondary">で入れられません。</span>
                </>
              ) : (
                <span className="text-text-secondary">固定枠に入れられませんでした。</span>
              )}
            </div>
          </div>
          {/* 🟡 希望枠ならこちら. */}
          <div
            className="rounded border border-warning/40 bg-warning/5 px-2 py-1"
            data-testid={`diff-add-card-${proposal.proposal_id}-preferred-suggest`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span aria-hidden>🟡</span>
              <span className="text-text-secondary">希望枠ならこちらに入れられます:</span>
              <span className="font-medium text-text-primary">{suggestedLine}</span>
            </div>
          </div>
        </div>
      ) : null}

      {/* 対象コースの 1 日タイムライン (既存訪問 + 黄色ゴースト差し込み). */}
      <div className="mb-2 overflow-x-auto rounded border border-border-default bg-bg-base">
        <DiffAddProposalTimeline
          proposal={proposal}
          isoYear={isoYear}
          isoWeek={isoWeek}
          visits={adoptedVisitPlans(proposal)}
        />
      </div>

      {/* 警告一覧 (category バッジ + message). */}
      {proposal.warnings.length > 0 ? (
        <ul className="mb-2 space-y-1">
          {proposal.warnings.map((w, i) => (
            <li
              key={i}
              className="flex flex-wrap items-center gap-1 rounded border border-warning/40 bg-warning/5 px-2 py-1"
            >
              <Badge
                variant="outline"
                className="text-[10px] text-text-secondary"
                data-testid={`diff-add-warning-category-badge-${w.category}`}
              >
                {V2_WARNING_CATEGORY_LABEL_JA[w.category]}
              </Badge>
              <span className="text-text-secondary">{w.message}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {/* アクション行. */}
      <div className="flex items-center justify-end">
        <Button
          type="button"
          size="sm"
          onClick={onAdopt}
          disabled={isBusy}
          data-testid={`diff-add-card-${proposal.proposal_id}-adopt`}
          className="h-7 px-3 text-xs"
        >
          この枠で採用
        </Button>
      </div>
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// ProposalConfirmModal — 採用確認モーダル (1 回)
//
// 仕様書 §13.5.1 + AssignWarningDialog の確認モーダル様式.
// ─────────────────────────────────────────────────────────────────────────

interface ProposalConfirmModalProps {
  proposal: DiffAddProposal;
  isApplying: boolean;
  onCancel: () => void;
  onApply: () => void;
}

function ProposalConfirmModal({
  proposal,
  isApplying,
  onCancel,
  onApply,
}: ProposalConfirmModalProps) {
  return (
    <Dialog open onOpenChange={(o) => (!o ? onCancel() : undefined)}>
      <DialogContent className="max-w-md" data-testid={`diff-add-confirm-${proposal.proposal_id}`}>
        <DialogHeader>
          <DialogTitle className="text-base">この枠で採用しますか？</DialogTitle>
          <DialogDescription>
            <span className="font-medium text-text-primary">{proposal.patient_name} 様</span> を{' '}
            {formatSuggestedLine(proposal)} に固定枠として登録します。
          </DialogDescription>
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
                    <Badge
                      variant="outline"
                      className="text-[10px] text-text-secondary"
                      data-testid={`diff-add-confirm-warning-category-badge-${w.category}`}
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
            data-testid="diff-add-confirm-cancel"
          >
            <X className="mr-1 h-4 w-4" aria-hidden />
            変更しない
          </Button>
          <Button
            type="button"
            onClick={onApply}
            disabled={isApplying}
            data-testid="diff-add-confirm-apply"
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
