'use client';

/**
 * PoolOverviewPane — Stage P-2 保留プール俯瞰パネル.
 *
 * PoolGroupedByWeekday を薄くラップし、以下の俯瞰機能を追加する:
 *   - 最上段の「⭐特別訪問週間」セクション (§6-2・`SpecialVisitPoolSection`。
 *     表示中の週のチケットが 0 件なら何も描かない)
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
import { AlertTriangle, Layers, Loader2, Sparkles } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { usePoolOverviewMutation } from '@/lib/queries/poolOverview';
import { EXCLUDED_REASON_LABEL } from './PoolCandidateList';
import { SpecialVisitPoolSection } from './SpecialTicketPlacePanel';
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

export interface PoolOverviewPaneProps extends Omit<PoolGroupedByWeekdayProps, 'headerAction'> {
  isoYear: number;
  isoWeek: number;
  officeId: string | null;
  /**
   * 「一括投入」ボタン押下ハンドラ (BulkPoolInsertDialog を親で開く)。
   * 未指定ならボタンを描画しない (後方互換)。
   */
  onBulkInsert?: () => void;
  /**
   * W-3: 希望未登録 (weekly_pattern 未設定 / frequency_per_week<=0) の active 患者一覧。
   * 1 名以上のとき「希望未登録 N名」チップを表示する。未指定 → 非表示。
   */
  unregisteredPatients?: PatientRead[];
  /**
   * W-3: 希望未登録チップ内の患者名クリック時のハンドラ。
   * handleOpenPoolPatientDetail と同じ導線 (patient_id を渡す)。
   */
  onClickUnregisteredPatient?: (patientId: string) => void;
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
  function PoolOverviewPane(
    {
      patients,
      renderCard,
      disabled,
      assignedSlotsByPatient,
      partnerLocationByPatientSlot,
      isoYear,
      isoWeek,
      officeId,
      onBulkInsert,
      unregisteredPatients = [],
      onClickUnregisteredPatient,
    }: PoolOverviewPaneProps,
    ref,
  ) {
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

    /** ヘッダーに注入するアクション領域 (「効果を表示」の隣に「一括投入」・「希望未登録」を小さく併置). */
    const headerAction = (
      <div className="flex flex-wrap gap-1">
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
        {/* RB (PO決定 2026-07-08): 全ロール同一表示。ハンドラ未配線 (staff) でも
          disabled ボタンとして描画し、構成差を無くす。 */}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onBulkInsert}
          disabled={disabled || !onBulkInsert || patients.length === 0}
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
        {/* W-3: 希望未登録チップ (N>=1 のときのみ表示). */}
        {unregisteredPatients.length > 0 ? (
          <Popover>
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-6 border-warning px-2 text-[11px] text-warning hover:bg-warning-bg"
                data-testid="pool-unregistered-chip"
              >
                <AlertTriangle className="mr-1 h-3 w-3" aria-hidden />
                希望未登録 {unregisteredPatients.length}名
              </Button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              side="bottom"
              sideOffset={4}
              className="w-72 p-3"
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => e.stopPropagation()}
              data-testid="pool-unregistered-popover"
            >
              <p className="mb-2 text-xs text-text-muted">
                希望訪問スケジュール未登録のため保留プールに表示されない患者が{' '}
                <span className="font-semibold text-warning">{unregisteredPatients.length}名</span>{' '}
                います
              </p>
              <ul className="space-y-0.5">
                {unregisteredPatients.map((p) => (
                  <li key={p.id}>
                    {onClickUnregisteredPatient ? (
                      <button
                        type="button"
                        className="w-full rounded px-2 py-0.5 text-left text-xs text-text-primary hover:bg-bg-muted focus:bg-bg-muted focus:outline-none"
                        onClick={() => onClickUnregisteredPatient(p.id)}
                        data-testid={`pool-unregistered-patient-${p.id}`}
                      >
                        {p.name}
                      </button>
                    ) : (
                      <span className="px-2 py-0.5 text-xs text-text-primary">{p.name}</span>
                    )}
                  </li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>
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
        topSection={
          // 特別訪問週間 (§6-2): 表示中の週のチケットを最上段に。0 件なら何も描かない。
          <SpecialVisitPoolSection
            isoYear={isoYear}
            isoWeek={isoWeek}
            officeId={officeId}
            canEdit={!disabled}
          />
        }
      />
    );
  },
);
PoolOverviewPane.displayName = 'PoolOverviewPane';
