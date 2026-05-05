'use client';

/**
 * CourseRow — Layer 2 コース 1 行 (W4-FE8).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.3 (Layer 2
 * コース分け) の「縦軸 1 コース」を表現する UI コンポーネント。
 *
 * レイアウト:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │ [A] | 患A 患B 患C 患D 患E 患F | [スタッフslot] (W4-FE9)│
 *   └─────────────────────────────────────────────────────────┘
 *
 * 機能:
 *   - 1 行 = 1 コース (A/B/C/D/M)
 *   - そのコースに属する患者カードを横並びで描画
 *   - 行全体が dnd-kit の droppable ターゲット (= コース間移動の drop 先)
 *   - 患者カードは PatientCard (W3-FE5) を再利用してドラッグソースに
 *
 * 所有外への配慮:
 *   - PatientCard / dnd-kit は既存資産を import して使うのみ。
 *     変更は加えない。
 *   - W4-FE9 (スタッフ割付実行) で本行に「割付済みスタッフ表示」を
 *     差し込めるよう `assignedStaffSlot` prop を予約してある。
 */
import { useDroppable } from '@dnd-kit/core';

import { cn } from '@/lib/utils';
import type { CourseCodeV2 } from '@/lib/queries/courses';

import { PatientCard, type PatientCardData } from './PatientCard';

// ─────────────────────────────────────────────────────────────────────────
// Public droppable id helpers (CourseProposal と共有)
// ─────────────────────────────────────────────────────────────────────────

const COURSE_DROPPABLE_PREFIX = 'course:';

/** "course:A" / "course:M" 形式の droppable id を組む. */
export function courseDroppableId(code: CourseCodeV2): string {
  return `${COURSE_DROPPABLE_PREFIX}${code}`;
}

/** 逆変換. 不正な id は null. */
export function parseCourseDroppableId(id: string): CourseCodeV2 | null {
  if (!id.startsWith(COURSE_DROPPABLE_PREFIX)) return null;
  const code = id.slice(COURSE_DROPPABLE_PREFIX.length);
  if (code === 'A' || code === 'B' || code === 'C' || code === 'D' || code === 'M') {
    return code;
  }
  return null;
}

/** 患者 1 人分の draggable id (CourseProposal と共有). */
export function coursePatientDraggableId(patientId: string): string {
  return `course-patient:${patientId}`;
}

/** `course-patient:<id>` から patient_id を復元. 不正なら null. */
export function parseCoursePatientDraggableId(id: string): string | null {
  const prefix = 'course-patient:';
  if (!id.startsWith(prefix)) return null;
  return id.slice(prefix.length);
}

// ─────────────────────────────────────────────────────────────────────────
// Color tone per course code (ScheduleGridV2 のコース行と整合)
// ─────────────────────────────────────────────────────────────────────────

const COURSE_TONES: Record<CourseCodeV2, { badge: string; ring: string; label: string }> = {
  M: {
    badge: 'bg-warning/10 text-warning border-warning/40',
    ring: 'ring-warning/20',
    label: 'M (マネージャー)',
  },
  A: {
    badge: 'bg-brand-primary/10 text-brand-primary border-brand-primary/40',
    ring: 'ring-brand-primary/20',
    label: 'A コース',
  },
  B: {
    badge: 'bg-success/10 text-success border-success/40',
    ring: 'ring-success/20',
    label: 'B コース',
  },
  C: {
    badge: 'bg-info/10 text-info border-info/40',
    ring: 'ring-info/20',
    label: 'C コース',
  },
  D: {
    badge: 'bg-bg-muted text-text-secondary border-border-default',
    ring: 'ring-border-default',
    label: 'D コース',
  },
};

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface CourseRowProps {
  /** コース記号. */
  code: CourseCodeV2;
  /**
   * このコースに属する患者カードのメタ情報 (描画順 = 配列順).
   * 親 (CourseProposal) が patient_ids → PatientCardData に解決して渡す。
   */
  patients: PatientCardData[];
  /** 1 コースあたりの上限 (§3.6.3). 通常コースは 6 名、M は別枠で上限なし. */
  capacity?: number;
  /** RBAC: admin/manager 以外は drag を無効化. */
  disabled?: boolean;
  /**
   * W4-FE9 用 slot. スタッフ割付実行後に「担当: S2 (山田)」のような
   * チップを差し込む想定。本チケット (W4-FE8) では未指定で、
   * W4-FE9 が外側から要素を渡す。
   */
  assignedStaffSlot?: React.ReactNode;
  /** 行クリック時のハンドラ (現状未使用、将来コース詳細パネルへの遷移用). */
  onClick?: () => void;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

/**
 * 1 コース分の横長帯。dnd-kit の droppable として登録され、
 * 他のコース行から PatientCard が drop されると親が onDragEnd で
 * code 移動を実行する。
 */
export function CourseRow({
  code,
  patients,
  capacity,
  disabled = false,
  assignedStaffSlot,
  onClick,
}: CourseRowProps) {
  const tone = COURSE_TONES[code];
  const { isOver, setNodeRef } = useDroppable({
    id: courseDroppableId(code),
    disabled,
    data: { kind: 'course', code },
  });

  const overflow = capacity !== undefined && patients.length > capacity;

  return (
    <div
      ref={setNodeRef}
      onClick={onClick}
      className={cn(
        'flex items-stretch gap-2 rounded-md border border-border-default bg-bg-base p-2',
        'transition-colors',
        isOver && !disabled ? 'ring-2' : '',
        isOver && !disabled ? tone.ring : '',
        disabled ? 'opacity-60' : '',
      )}
      data-course-code={code}
      role="group"
      aria-label={`${tone.label} (${patients.length} 名)`}
    >
      {/* バッジ列: コース記号 + 件数 */}
      <div
        className={cn(
          'flex w-20 shrink-0 flex-col items-center justify-center rounded border px-1 py-1 text-center',
          tone.badge,
        )}
      >
        <span className="text-base font-bold leading-none">{code}</span>
        <span className="tnum mt-0.5 text-[10px] font-medium leading-none">
          {patients.length}
          {capacity !== undefined ? `/${capacity}` : ''} 名
        </span>
      </div>

      {/* 患者カード列 */}
      <div className="flex min-h-[40px] flex-1 flex-wrap items-start gap-1">
        {patients.length === 0 ? (
          <p className="self-center px-2 text-xs text-text-muted">（未割当）ここへドロップ</p>
        ) : (
          patients.map((p) => (
            <PatientCard
              key={p.id}
              draggableId={coursePatientDraggableId(p.id)}
              patient={p}
              disabled={disabled}
            />
          ))
        )}
        {overflow ? (
          <span className="self-center text-[10px] font-semibold text-error">
            上限超過 ({patients.length} / {capacity})
          </span>
        ) : null}
      </div>

      {/* W4-FE9 用 スタッフ割付 slot. */}
      {assignedStaffSlot !== undefined ? (
        <div className="flex w-28 shrink-0 items-center justify-end">{assignedStaffSlot}</div>
      ) : null}
    </div>
  );
}
