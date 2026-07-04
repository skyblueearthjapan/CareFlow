'use client';

/**
 * PoolOverviewPane — Stage P-2 保留プール俯瞰パネル.
 *
 * PoolGroupedByWeekday を薄くラップし、以下の俯瞰機能を追加する:
 *   - ヘッダーの「効果を表示」ボタン (POST /v2/pool-overview を on-demand 実行・緑)
 *   - 各患者行への delta バッジ (+N分) と「投入先なし」バッジ
 *   - 結果表示後は自動で効果順に並ぶ (best_delta_minutes 昇順; null 末尾、
 *     candidate_count=0 最後尾。ソートトグルは PO 指示 2026-07-03 で廃止)
 *
 * 設計上のルール:
 *   - 自動発火なし: ボタン押下でのみ pool-overview を実行する
 *   - 患者クリック → 既存の PatientScheduleDetailDialog 導線は変更しない
 *   - 50 件超過の場合は先頭 50 件に切って toast で通知
 *
 * テスト容易性のため表示ロジック部分を本ファイルで完結させる。
 * (CourseDayTablePanel 内部だと単体テストが困難なため切り出し)
 */
import * as React from 'react';
import { Layers, Loader2, Sparkles } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { usePoolOverviewMutation } from '@/lib/queries/poolOverview';
import { EXCLUDED_REASON_LABEL } from './PoolCandidateList';
import {
  PoolGroupedByWeekday,
  type PoolGroupedByWeekdayProps,
  type PoolCardSlotInfo,
} from './PoolPanel';
import type { PatientRead } from '@/lib/schemas/patient';
import type { PoolOverviewItem } from '@/lib/schemas/v2/poolOverview';

/** pool-overview API 上限 */
const POOL_OVERVIEW_LIMIT = 50;

/** pool-overview 除外理由の日本語ラベル (EXCLUDED_REASON_LABEL 共用) */
function overviewExcludedLabel(reason: string | null | undefined): string {
  if (!reason) return '';
  return EXCLUDED_REASON_LABEL[reason] ?? reason;
}

/**
 * 「効果順」ソート比較関数.
 *   1. candidate_count=0 → 末尾
 *   2. best_delta_minutes null → その手前の末尾
 *   3. best_delta_minutes 昇順 (小さいほど効果大)
 */
function compareByEffect(
  a: PatientRead,
  b: PatientRead,
  overviewByPatient: Map<string, PoolOverviewItem>,
): number {
  const ia = overviewByPatient.get(a.id);
  const ib = overviewByPatient.get(b.id);

  const countA = ia?.candidate_count ?? 1; // 未計算は「有効」として手前に
  const countB = ib?.candidate_count ?? 1;
  const noSlotA = countA === 0 ? 1 : 0;
  const noSlotB = countB === 0 ? 1 : 0;
  if (noSlotA !== noSlotB) return noSlotA - noSlotB;

  const deltaA = ia?.best_delta_minutes ?? null;
  const deltaB = ib?.best_delta_minutes ?? null;
  if (deltaA === null && deltaB === null) return 0;
  if (deltaA === null) return 1; // null は後ろ
  if (deltaB === null) return -1;
  return deltaA - deltaB;
}

// ─────────────────────────────────────────────────────────────────────────
// DeltaBadge / NoSlotBadge — PatientCard 下に差し込む表示要素
// ─────────────────────────────────────────────────────────────────────────

/** delta バッジ: PoolCandidateList の pool-candidate-delta-badge と同じ視覚言語 */
function DeltaBadge({ minutes }: { minutes: number }) {
  const label =
    Math.round(minutes) <= 0 ? 'コースの移動 ±0分' : `コースの移動 +${Math.round(minutes)}分`;
  return (
    <Badge
      variant="secondary"
      className="text-[10px]"
      data-testid="pool-overview-delta-badge"
      title="診断・改善提案と同じ物差し（厳密限界コスト: コース全体の移動増分）"
    >
      {label}
    </Badge>
  );
}

/** 「投入先なし」バッジ: candidate_count=0 のとき表示 */
function NoSlotBadge({ reason }: { reason: string | null | undefined }) {
  const label = overviewExcludedLabel(reason);
  return (
    <Badge
      variant="warning"
      className="text-[10px]"
      data-testid="pool-overview-no-slot-badge"
      title={label || undefined}
    >
      投入先なし
    </Badge>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Props / Handle
// ─────────────────────────────────────────────────────────────────────────

export interface PoolOverviewPaneProps
  extends Omit<PoolGroupedByWeekdayProps, 'headerAction'> {
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
  /**
   * 「一括投入」ボタン押下ハンドラ (BulkPoolInsertDialog を親で開く)。
   * 未指定ならボタンを描画しない (後方互換)。
   */
  onBulkInsert?: () => void;
}

/**
 * Stage P-3: CourseDayTablePanel の「プール投入」ボタンから外部トリガーするための
 * imperative handle。forwardRef 経由で ref に設定される。
 */
export interface PoolOverviewPaneHandle {
  /** 「効果を計算」を外部から起動する（ユーザーがボタンを押した扱い）。 */
  triggerCompute(): void;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export const PoolOverviewPane = React.forwardRef<PoolOverviewPaneHandle, PoolOverviewPaneProps>(
  function PoolOverviewPane({
    patients,
    renderCard,
    disabled,
    assignedSlotsByPatient,
    partnerLocationByPatientSlot,
    isoYear,
    isoWeek,
    officeId,
    onBulkInsert,
  }: PoolOverviewPaneProps, ref) {
  const overviewMut = usePoolOverviewMutation();
  // useCallback の deps には mutate のみを入れる (React Query v5 で参照安定。
  // overviewMut オブジェクト全体は state 変化ごとに参照が変わりメモ化が無効になる).
  const { mutate: computeOverview } = overviewMut;

  /** patient_id → PoolOverviewItem のルックアップ (計算後のみ非 null) */
  const overviewByPatient = React.useMemo<Map<string, PoolOverviewItem>>(() => {
    const m = new Map<string, PoolOverviewItem>();
    for (const item of overviewMut.data?.items ?? []) {
      m.set(item.patient_id, item);
    }
    return m;
  }, [overviewMut.data]);

  /** 計算済みかどうか (ボタン押下後に true) */
  const hasResult = overviewMut.isSuccess;

  /** 計算ボタン押下ハンドラ */
  const handleCompute = React.useCallback(() => {
    const ids = patients.map((p) => p.id);
    let truncated = false;
    let targetIds = ids;
    if (ids.length > POOL_OVERVIEW_LIMIT) {
      targetIds = ids.slice(0, POOL_OVERVIEW_LIMIT);
      truncated = true;
    }
    computeOverview(
      {
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
        patient_ids: targetIds,
      },
      {
        onSuccess: () => {
          if (truncated) {
            toast.warning(
              `プール患者が ${ids.length} 名います。先頭 ${POOL_OVERVIEW_LIMIT} 名のみ計算しました。`,
            );
          }
        },
        onError: () => {
          toast.error('効果計算に失敗しました');
        },
      },
    );
  }, [patients, isoYear, isoWeek, officeId, computeOverview]);

  // Stage P-3: CourseDayTablePanel の「プール投入」ボタンから外部トリガーできるよう
  // imperative handle を設定する。ユーザーがボタンを押した扱いで計算を起動する。
  React.useImperativeHandle(ref, () => ({ triggerCompute: handleCompute }), [handleCompute]);

  /** 効果順ソート適用後の patients (結果があれば常に効果順・トグル廃止 PO 指示 2026-07-03) */
  const sortedPatients = React.useMemo<PatientRead[]>(() => {
    if (!hasResult) return patients;
    return [...patients].sort((a, b) => compareByEffect(a, b, overviewByPatient));
  }, [patients, hasResult, overviewByPatient]);

  /** 各 PatientCard を delta/no-slot バッジでラップした renderCard */
  const augmentedRenderCard = React.useCallback(
    (patient: PatientRead, slotInfo: PoolCardSlotInfo) => {
      const item = overviewByPatient.get(patient.id);
      const showDelta = item != null && item.best_delta_minutes != null;
      const showNoSlot = item != null && item.candidate_count === 0;

      if (!showDelta && !showNoSlot) {
        return renderCard(patient, slotInfo);
      }

      return (
        <div>
          {renderCard(patient, slotInfo)}
          <div className="flex flex-wrap gap-1 px-1 pb-1 pt-0.5">
            {showDelta ? <DeltaBadge minutes={item!.best_delta_minutes!} /> : null}
            {showNoSlot ? <NoSlotBadge reason={item!.top_excluded_reason} /> : null}
          </div>
        </div>
      );
    },
    [renderCard, overviewByPatient],
  );

  /** 一括投入対象がプール上限を超えているか (先頭 50 名のみ対象) */
  const bulkTruncated = patients.length > POOL_OVERVIEW_LIMIT;

  /** ヘッダーに注入するアクション領域 (「効果を表示」の隣に「一括投入」を小さく併置). */
  const headerAction = (
    <div className="flex gap-1">
      <Button
        type="button"
        variant="default"
        size="sm"
        onClick={handleCompute}
        disabled={overviewMut.isPending}
        className="h-6 px-2 text-[11px]"
        data-testid="pool-overview-compute-button"
      >
        {overviewMut.isPending ? (
          <Loader2 className="mr-1 h-3 w-3 animate-spin" aria-hidden />
        ) : (
          <Sparkles className="mr-1 h-3 w-3" aria-hidden />
        )}
        効果を表示
      </Button>
      {onBulkInsert ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onBulkInsert}
          disabled={disabled || patients.length === 0}
          className="h-6 px-2 text-[11px]"
          data-testid="pool-overview-bulk-insert-button"
          title={
            bulkTruncated
              ? `プール患者が ${patients.length} 名います。一括投入は先頭 ${POOL_OVERVIEW_LIMIT} 名が対象です。`
              : '保留プールの患者をまとめて固定訪問週間に投入します'
          }
        >
          <Layers className="mr-1 h-3 w-3" aria-hidden />
          一括投入
        </Button>
      ) : null}
    </div>
  );

  return (
    <PoolGroupedByWeekday
      patients={sortedPatients}
      renderCard={augmentedRenderCard}
      disabled={disabled}
      assignedSlotsByPatient={assignedSlotsByPatient}
      partnerLocationByPatientSlot={partnerLocationByPatientSlot}
      headerAction={headerAction}
    />
  );
});
PoolOverviewPane.displayName = 'PoolOverviewPane';
