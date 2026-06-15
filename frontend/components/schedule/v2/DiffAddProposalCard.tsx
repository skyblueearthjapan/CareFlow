'use client';

/**
 * DiffAddProposalCard — プール投入 (diff-add) 提案の「表示」「採用」共有部品.
 *
 * 目的 (ドリフト防止):
 *   プール投入の提案カード / 確認モーダル / visit_plans 決定ロジックを 1 か所に集約し、
 *   一括表示 (DiffAddDialog) と単体表示 (PatientScheduleDetailDialog のプール投入セクション)
 *   の両方が **同じ部品** を描画するようにする。これにより
 *     - 表示 (proposal_source 色分け / 固定枠不可理由 / タイムライン)
 *     - 採用 (suggested_visits → visit_plans 変換)
 *   のどちらかだけが片方で更新されて取り残される事態を構造的に防ぐ。
 *
 *   提案の「算出」自体は BE の単一エンドポイント (/v2/diff-add → run_v2_pipeline) が
 *   唯一のソースであり、本ファイルは描画と採用 payload 生成のみを担う。
 *
 * 元は DiffAddDialog.tsx 内のローカル定義 (Phase G-92). 共有化に伴い切り出し。
 */
import * as React from 'react';
import { CheckCircle2, Loader2, X } from 'lucide-react';

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
import type {
  DiffAddProposal,
  DiffAddProposalSource,
  V2VisitPlan,
} from '@/lib/schemas/v2/autoScheduleV2';
import {
  V2_DIFF_ADD_FIXED_UNAVAILABLE_REASON_LABEL_JA,
  V2_WARNING_CATEGORY_LABEL_JA,
} from '@/lib/schemas/v2/autoScheduleV2';
import { cn } from '@/lib/utils';

import { DiffAddProposalTimeline } from './DiffAddProposalTimeline';
import { formatDelta, trimSeconds } from './_autoScheduleUtils';

// ─────────────────────────────────────────────────────────────────────────
// Helpers (DiffAddDialog から移設)
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

function fmtWeekday(weekday: number): string {
  return WEEKDAY_LABELS[weekday] ?? '?';
}

export function formatSuggestedLine(p: DiffAddProposal): string {
  const wd = fmtWeekday(p.suggested.weekday);
  return `${wd} ${trimSeconds(p.suggested.start_time)} ${p.suggested.course_code} コース`;
}

/**
 * 採用 / タイムライン描画に使う visit_plans を決定する単一ソース.
 * suggested_visits があればそれを、無ければ suggested 1 件を使う.
 * BE は visit_plans の上書き (stateless) を要求するため採用 payload と描画で共有.
 */
export function adoptedVisitPlans(p: DiffAddProposal): V2VisitPlan[] {
  return p.suggested_visits.length > 0 ? p.suggested_visits : [p.suggested];
}

/** proposal_source 別の見た目 (色 / バッジ / 見出しアイコン + 文言). */
interface SourceStyle {
  cardClass: string;
  badgeVariant: 'success' | 'warning' | 'destructive';
  badgeLabel: string;
  icon: string;
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

// ─────────────────────────────────────────────────────────────────────────
// ProposalCard — 患者 1 人 = 1 コースカード (タイムライン内蔵)
//
// AssignWarningDialog の ReviewCard と同じ視覚言語 (色分け枠 / ヘッダーバッジ /
// 原因マーク). proposal_source で色 / 文言を出し分ける.
// ─────────────────────────────────────────────────────────────────────────

export interface ProposalCardProps {
  proposal: DiffAddProposal;
  isoYear: number;
  isoWeek: number;
  isBusy: boolean;
  /** 「この枠で採用」 押下 (= 確認モーダルを開く). */
  onAdopt: () => void;
  /**
   * 採用ボタンを描画するか. RBAC で閲覧専用のときは false にして
   * 「採用」アクションを隠す (BE 側でも 403 で担保).
   */
  showAdopt?: boolean;
}

export function ProposalCard({
  proposal,
  isoYear,
  isoWeek,
  isBusy,
  onAdopt,
  showAdopt = true,
}: ProposalCardProps) {
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
      {showAdopt ? (
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
      ) : null}
    </li>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// ProposalConfirmModal — 採用確認モーダル (1 回)
//
// 仕様書 §13.5.1 + AssignWarningDialog の確認モーダル様式.
// ─────────────────────────────────────────────────────────────────────────

export interface ProposalConfirmModalProps {
  proposal: DiffAddProposal;
  isApplying: boolean;
  onCancel: () => void;
  onApply: () => void;
}

export function ProposalConfirmModal({
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
