'use client';

/**
 * PatientCard — 患者 1 名分のドラッグ可能なカード (W3-FE5).
 *
 * 設計仕様書 v0.9 §3.6.2 (Layer 1) で示される「患者を時刻スロットへ」の
 * ドラッグソース。プール側 / 配置済みセル側の双方で使い回す。
 *
 * dnd-kit の `useDraggable` を使い、`active.id` を経由して親の
 * `ScheduleGridV2` が drop 先を判定する。位置情報 (現在どこに配置されているか
 * = `placementId | null`) は親が保持し、本コンポーネントは表示と +1 ボタン
 * UI のみを担当する。
 */
import { useDraggable } from '@dnd-kit/core';
import { CSS } from '@dnd-kit/utilities';
import { Plus, User, Users, X } from 'lucide-react';

import { cn } from '@/lib/utils';

export interface PatientCardData {
  id: string;
  name: string;
  /** 表示用の追加メタ (kana / 保険など). 任意. */
  caption?: string | null;
  /**
   * Wave 18 Phase B-4: プールカードに表示する希望時間.
   * 例: "10:00 (固定)" / "09:30〜10:00 (時間帯)" / "午前". 任意.
   * `caption` の下に小さく表示する。
   */
  preferredTimeLabel?: string | null;
}

export interface PatientCardProps {
  /** dnd-kit draggable id. プール用は `pool:<patient_id>`, 配置済みは
   *  `placement:<placement_id>` のように親側で組み立てる。 */
  draggableId: string;
  patient: PatientCardData;
  /** 配置済みカードでのみ表示する staff_count (1 or 2). undefined の場合は
   *  プール側カードと判断して +1 ボタンを描画しない。 */
  staffCount?: 1 | 2;
  /** +1 / -1 トグル。`staffCount` が定義されているときのみ呼ばれる。 */
  onToggleStaffCount?: () => void;
  /** 配置済みカードの「プールに戻す」(= 配置解除) ボタン. */
  onUnassign?: () => void;
  /** disabled 状態 (RBAC で staff / fix mutation 中など). */
  disabled?: boolean;
  /**
   * コンパクト表示モード (同一スロット複数エントリ時の横並び用).
   * true のとき caption / +1 ボタンを非表示にして幅を節約する。
   */
  compact?: boolean;
}

/**
 * 1 患者カード (drag handle 兼コンテナ).
 *
 * - 高さ: ~28px (15min セル 1 つ分にちょうど収まる)
 * - hover で軽くハイライト + cursor-grab
 * - dragging 中は半透明化 (DragOverlay は親側で描画)
 */
export function PatientCard({
  draggableId,
  patient,
  staffCount,
  onToggleStaffCount,
  onUnassign,
  disabled = false,
  compact = false,
}: PatientCardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: draggableId,
    disabled,
    data: { patientId: patient.id, staffCount },
  });

  const style: React.CSSProperties = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.4 : 1,
  };

  const isPlaced = staffCount !== undefined;

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'group flex items-center justify-between gap-1 rounded border px-2 py-1 text-xs',
        'select-none touch-none',
        isPlaced
          ? 'border-brand-primary/40 bg-brand-primary/5 text-text-primary'
          : 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted',
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-grab active:cursor-grabbing',
      )}
      {...listeners}
      {...attributes}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0">
        <div className="flex items-center gap-1">
          {staffCount === 2 ? (
            <Users className="h-3 w-3 shrink-0 text-brand-primary" aria-hidden />
          ) : (
            <User className="h-3 w-3 shrink-0 text-text-muted" aria-hidden />
          )}
          <span className="truncate font-medium">{patient.name}</span>
          {patient.caption && !compact ? (
            <span className="truncate text-text-muted">({patient.caption})</span>
          ) : null}
        </div>
        {/* Wave 18 Phase B-4: 希望時間 (プールカード) */}
        {patient.preferredTimeLabel && !compact ? (
          <span
            className="tnum truncate pl-4 text-[10px] text-text-muted"
            data-testid={`patient-card-preferred-time-${patient.id}`}
          >
            {patient.preferredTimeLabel}
          </span>
        ) : null}
      </div>

      {isPlaced ? (
        <div className="flex shrink-0 items-center gap-0.5">
          {/* +1 トグル: compact モードでは非表示 (幅を節約) */}
          {onToggleStaffCount && !compact ? (
            <button
              type="button"
              className={cn(
                'rounded px-1 py-0.5 text-[10px] font-semibold leading-none',
                staffCount === 2
                  ? 'bg-brand-primary text-white'
                  : 'bg-bg-muted text-text-secondary hover:bg-brand-primary/20',
              )}
              onClick={(e) => {
                e.stopPropagation();
                onToggleStaffCount();
              }}
              onPointerDown={(e) => e.stopPropagation()}
              disabled={disabled}
              aria-label={staffCount === 2 ? '1 名体制に戻す' : '2 名体制にする'}
              title={staffCount === 2 ? '1 名体制に戻す' : '2 名体制にする'}
            >
              {staffCount === 2 ? '×2' : '+1'}
            </button>
          ) : null}
          {onUnassign ? (
            <button
              type="button"
              className="rounded p-0.5 text-text-muted hover:bg-error/10 hover:text-error"
              onClick={(e) => {
                e.stopPropagation();
                onUnassign();
              }}
              onPointerDown={(e) => e.stopPropagation()}
              disabled={disabled}
              aria-label="プールに戻す"
              title="プールに戻す"
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </div>
      ) : (
        <Plus
          className="h-3 w-3 shrink-0 text-text-muted opacity-0 group-hover:opacity-100"
          aria-hidden
        />
      )}
    </div>
  );
}
