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
 * ドリフト防止 (Pool-detail 統合):
 *   提案カード / 確認モーダル / visit_plans 決定は ``DiffAddProposalCard`` に集約し、
 *   単体表示 (PatientScheduleDetailDialog のプール投入セクション) と共有する。
 *   本ダイアログは「一括取得 + 一覧 + 1 件採用」のオーケストレーションのみ担う。
 *
 * RBAC: 呼出側 (CourseDayTablePanel) で admin/manager ガード済み.
 */
import * as React from 'react';
import { Loader2, Plus } from 'lucide-react';
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

import { ProposalCard, ProposalConfirmModal, adoptedVisitPlans } from './DiffAddProposalCard';
import { formatErr } from './_autoScheduleUtils';

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
      // それを、無ければ suggested 1 件を送る (描画と同一ソース = 共有ヘルパー).
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
