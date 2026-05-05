'use client';

/**
 * TimeSlotCell — 15 分 × 曜日の 1 セル (ドロップターゲット, W3-FE5).
 *
 * 設計 v0.9 §3.6.2 (Layer 1) の時間軸セル。8:00〜20:00 を 15 分刻みで表示
 * するため、画面全体では 7 (曜日) × 49 (= 12.25h × 4) のグリッドになる。
 *
 * 配置済み患者がいる場合は本セル内に PatientCard をレンダリングする。
 * 1 セル複数患者の場合は縦に積む（実運用では稀だが衝突を視覚化する目的）。
 */
import { useDroppable } from '@dnd-kit/core';

import { cn } from '@/lib/utils';

export interface TimeSlotCellProps {
  /** dnd-kit droppable id. 親側で `cell:<weekday>:<HHMM>` 形式で組む。 */
  droppableId: string;
  /** 0=Mon..6=Sun. */
  weekday: number;
  /** ``HH:MM`` (24h). */
  time: string;
  /** 当該セルに配置されている子要素 (PatientCard) を render する slot. */
  children?: React.ReactNode;
  /** 1h 区切り (= "00" 分のセル) を太線で強調するためのフラグ. */
  isHourBoundary?: boolean;
  /** disabled 状態 (RBAC で staff の場合など). */
  disabled?: boolean;
}

/**
 * 単一セル (= weekday × 15min slot).
 *
 * isOver 中は背景を primary tint に変えて drop target を視認させる。
 * 1h 境界 (00 分) のセルは上ボーダーを濃くして時刻ガイドを兼ねる。
 */
export function TimeSlotCell({
  droppableId,
  weekday,
  time,
  children,
  isHourBoundary = false,
  disabled = false,
}: TimeSlotCellProps) {
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled,
    data: { kind: 'cell', weekday, time },
  });

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'min-h-[28px] border-r border-border-default px-0.5 py-0.5',
        'transition-colors',
        isHourBoundary ? 'border-t border-t-border-default' : 'border-t border-t-border-default/30',
        isOver && !disabled ? 'bg-brand-primary/15' : 'bg-bg-base',
        disabled ? 'opacity-60' : '',
      )}
      data-weekday={weekday}
      data-time={time}
    >
      <div className="flex flex-col gap-0.5">{children}</div>
    </div>
  );
}
