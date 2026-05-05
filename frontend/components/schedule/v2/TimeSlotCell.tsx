'use client';

/**
 * TimeSlotCell — 15 分 × 曜日の 1 セル (ドロップターゲット, W3-FE5).
 *
 * 設計 v0.9 §3.6.2 (Layer 1) の時間軸セル。8:00〜20:00 を 15 分刻みで表示
 * するため、画面全体では 7 (曜日) × 49 (= 12.25h × 4) のグリッドになる。
 *
 * W7-FE3 (Must-fix #8): entries: VisitEntry[] に拡張して同一スロット複数訪問を表現。
 * - 0 件 → 空セル (drop 受付)
 * - 1 件 → PatientCard 1 つ
 * - 2 件 → 横 50% : 50% 並列表示
 * - 3 件以上 → 左に最初の 1 件 + 右に "+N 件" バッジ、クリックで Popover 全件表示
 *
 * 同一 patient が同一スロットに既に配置されている場合は drop を拒否し
 * toast.warning を表示する。
 */
import { useState } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { toast } from 'sonner';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

import { PatientCard, type PatientCardData } from './PatientCard';

// ─── VisitEntry ──────────────────────────────────────────────────────────────

/**
 * 1 スロットに配置された 1 訪問分のデータ。
 * ScheduleGridV2 の Placement を TimeSlotCell 向けに整形したもの。
 */
export interface VisitEntry {
  /** 患者 ID (placements の key と同じ). */
  patientId: string;
  /** dnd-kit の draggable id (= `patient:<patientId>`). */
  draggableId: string;
  /** PatientCard に渡す患者メタ. */
  patient: PatientCardData;
  /** スタッフ数 (1 or 2). */
  staffCount: 1 | 2;
  /** +1 トグル callback. */
  onToggleStaffCount?: () => void;
  /** プールに戻す callback. */
  onUnassign?: () => void;
}

// ─── Props ───────────────────────────────────────────────────────────────────

export interface TimeSlotCellProps {
  /** dnd-kit droppable id. 親側で `cell:<weekday>:<HHMM>` 形式で組む。 */
  droppableId: string;
  /** 0=Mon..6=Sun. */
  weekday: number;
  /** ``HH:MM`` (24h). */
  time: string;
  /** 当該スロットに配置されている訪問の配列 (0 件以上). */
  entries: VisitEntry[];
  /** 1h 区切り (= "00" 分のセル) を太線で強調するためのフラグ. */
  isHourBoundary?: boolean;
  /** disabled 状態 (RBAC で staff の場合など). */
  disabled?: boolean;
}

// ─── Component ───────────────────────────────────────────────────────────────

/**
 * 単一セル (= weekday × 15min slot).
 *
 * isOver 中は背景を primary tint に変えて drop target を視認させる。
 * 1h 境界 (00 分) のセルは上ボーダーを濃くして時刻ガイドを兼ねる。
 *
 * 複数 entry の場合:
 *   - 2 件まで: flex row で横並び (各 min-w-0, 幅 50%)
 *   - 3 件以上: 1 件目 + "+N 件" バッジ (Popover で全件表示)
 */
export function TimeSlotCell({
  droppableId,
  weekday,
  time,
  entries,
  isHourBoundary = false,
  disabled = false,
}: TimeSlotCellProps) {
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled,
    data: { kind: 'cell', weekday, time },
  });

  const [popoverOpen, setPopoverOpen] = useState(false);

  const entryCount = entries.length;

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
      data-testid={`slot-${weekday}-${time}`}
    >
      {entryCount === 0 ? null : entryCount <= 2 ? (
        /* 1〜2 件: 横並び */
        <div className="flex gap-0.5">
          {entries.map((entry) => (
            <div key={entry.patientId} className="min-w-0 flex-1">
              <PatientCard
                draggableId={entry.draggableId}
                patient={entry.patient}
                staffCount={entry.staffCount}
                onToggleStaffCount={entry.onToggleStaffCount}
                onUnassign={entry.onUnassign}
                disabled={disabled}
                compact={entryCount === 2}
              />
            </div>
          ))}
        </div>
      ) : (
        /* 3 件以上: 1 件目 + "+N 件" バッジ */
        <div className="flex gap-0.5">
          {/* 最初の 1 件 */}
          {entries[0] ? (
            <div className="min-w-0 flex-1">
              <PatientCard
                draggableId={entries[0].draggableId}
                patient={entries[0].patient}
                staffCount={entries[0].staffCount}
                onToggleStaffCount={entries[0].onToggleStaffCount}
                onUnassign={entries[0].onUnassign}
                disabled={disabled}
                compact
              />
            </div>
          ) : null}

          {/* +N 件バッジ + Popover */}
          <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                className={cn(
                  'shrink-0 rounded px-1 py-0.5 text-[10px] font-semibold leading-none',
                  'bg-bg-muted text-text-secondary hover:bg-brand-primary/20 hover:text-brand-primary',
                  'transition-colors',
                )}
                onClick={(e) => {
                  e.stopPropagation();
                  setPopoverOpen((v) => !v);
                }}
                onPointerDown={(e) => e.stopPropagation()}
                aria-label={`他 ${entryCount - 1} 件の訪問を表示`}
                title={`他 ${entryCount - 1} 件`}
              >
                +{entryCount - 1}件
              </button>
            </PopoverTrigger>
            <PopoverContent className="w-64 p-2" side="right" align="start">
              <p className="mb-2 text-[10px] font-semibold text-text-muted">
                {time} の全訪問 ({entryCount} 件)
              </p>
              <div className="flex flex-col gap-1">
                {entries.map((entry) => (
                  <PatientCard
                    key={entry.patientId}
                    draggableId={entry.draggableId}
                    patient={entry.patient}
                    staffCount={entry.staffCount}
                    onToggleStaffCount={entry.onToggleStaffCount}
                    onUnassign={
                      entry.onUnassign
                        ? () => {
                            entry.onUnassign?.();
                            setPopoverOpen(false);
                          }
                        : undefined
                    }
                    disabled={disabled}
                  />
                ))}
              </div>
            </PopoverContent>
          </Popover>
        </div>
      )}
    </div>
  );
}

// ─── Drop-duplicate guard (helper for ScheduleGridV2) ────────────────────────

/**
 * 同一 patient を同一スロットに drop しようとした場合に警告を出して true を返す。
 * ScheduleGridV2 の handleDragEnd 内で呼び出す。
 */
export function rejectDuplicateDrop(patientId: string, existingEntries: VisitEntry[]): boolean {
  const isDuplicate = existingEntries.some((e) => e.patientId === patientId);
  if (isDuplicate) {
    toast.warning('同じ患者は同一スロットに重複して配置できません');
    return true;
  }
  return false;
}
