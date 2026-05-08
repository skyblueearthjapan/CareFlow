'use client';

/**
 * CourseDayTable — Wave 17 Phase B-2: 1 つの (曜日 × コース) テーブル.
 *
 * Excel スケジュール枠組みに完全準拠した 5 列構成:
 *   - 時間帯 (固定): 09:30, 09:45, ... 18:00 の 35 行 (15 分刻み)
 *   - 氏名 / 住所 / 複数 / 条件
 *
 * ヘッダー:
 *   - 「{office}-{label} コース ({capacity})」
 *   - 担当スタッフ dropdown (admin/manager only)
 *
 * dnd-kit:
 *   - 各行の 4 つの「データ列セル」全体が 1 つの droppable.
 *   - id = `course-day-cell:${weekday}:${course_template_id}:${HH:MM}`
 *   - 親 (CourseDayTablePanel) が DragEnd を受け取り place-and-fix を呼ぶ。
 */
import { useMemo } from 'react';
import { useDroppable } from '@dnd-kit/core';

import { cn } from '@/lib/utils';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import { capacityForWeekday, capacityKeyForWeekday } from '@/lib/schemas/v2/course_template';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { StaffRead } from '@/lib/schemas/staff';

// ─────────────────────────────────────────────────────────────────────────
// Constants — 時刻軸 (B-2 / Excel 完全準拠)
// ─────────────────────────────────────────────────────────────────────────

/** 表示時刻範囲: 09:30〜18:00 (両端含む). 15 分刻み → 35 スロット. */
export const TIME_SLOT_START_HOUR = 9;
export const TIME_SLOT_START_MINUTE = 30;
export const TIME_SLOT_END_HOUR = 18;
export const TIME_SLOT_END_MINUTE = 0;
export const TIME_SLOT_MINUTES = 15;

/** 35 行の時刻ラベル ("HH:MM"). */
export function buildCourseTimeSlots(): string[] {
  const out: string[] = [];
  const startTotal = TIME_SLOT_START_HOUR * 60 + TIME_SLOT_START_MINUTE;
  const endTotal = TIME_SLOT_END_HOUR * 60 + TIME_SLOT_END_MINUTE;
  for (let m = startTotal; m <= endTotal; m += TIME_SLOT_MINUTES) {
    const hh = Math.floor(m / 60);
    const mm = m % 60;
    out.push(`${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`);
  }
  return out;
}

const TIME_SLOTS = buildCourseTimeSlots();

// ─────────────────────────────────────────────────────────────────────────
// dnd-kit ID helpers
// ─────────────────────────────────────────────────────────────────────────

export function courseDayCellDroppableId(
  weekday: number,
  courseTemplateId: string,
  time: string,
): string {
  return `course-day-cell:${weekday}:${courseTemplateId}:${time}`;
}

/**
 * "course-day-cell:weekday:course_template_id:HH:MM" を分解.
 * UUID は ':' を含まないので weekday + UUID + hh + mm の 4 セグメント。
 */
export function parseCourseDayCellId(id: string): {
  weekday: number;
  courseTemplateId: string;
  time: string;
} | null {
  if (!id.startsWith('course-day-cell:')) return null;
  const rest = id.slice('course-day-cell:'.length);
  const parts = rest.split(':');
  if (parts.length < 4) return null;
  const weekday = Number.parseInt(parts[0]!, 10);
  const courseTemplateId = parts[1]!;
  const time = `${parts[2]}:${parts[3]}`;
  if (Number.isNaN(weekday) || weekday < 0 || weekday > 6) return null;
  if (!courseTemplateId) return null;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return null;
  return { weekday, courseTemplateId, time };
}

// ─────────────────────────────────────────────────────────────────────────
// Types — 1 行分の表示用 visit データ
// ─────────────────────────────────────────────────────────────────────────

/** CourseDayTable の 1 セル分の visit 表示用データ. */
export interface CourseGridVisit {
  id: string;
  patient_id: string;
  patient_name: string | null;
  patient_address: string | null;
  /** required_staff_count >= 2 で「複数」表示. */
  required_staff_count: number;
  /** "HH:MM" (15 分境界に切り下げ済み). */
  start_slot: string;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export interface CourseDayTableProps {
  weekday: number;
  template: CourseTemplateRead;
  /** 当該 (template, weekday) に紐づく週次コース行 (assigned_staff 等の表示用). null = 未生成. */
  course: CourseV2Read | null;
  officeName: string;
  /** 当該 (template_id, weekday) でこの曜日に該当する visits. */
  visits: CourseGridVisit[];
  /** 拠点に属する active staff (担当 dropdown 用). */
  staffOptions: StaffRead[];
  canEdit: boolean;
  /** 担当 dropdown 変更時のハンドラ. course が null のときは null 渡し (新規 case). */
  onChangeAssignedStaff: (staffId: string | null) => void;
  /** dropdown が pending 中 (PATCH /courses/{id} 中) は disabled に. */
  isStaffMutating: boolean;
}

export function CourseDayTable({
  weekday,
  template,
  course,
  officeName,
  visits,
  staffOptions,
  canEdit,
  onChangeAssignedStaff,
  isStaffMutating,
}: CourseDayTableProps) {
  // visits を slot ("HH:MM") → CourseGridVisit[] にバケット化.
  const occupants = useMemo(() => {
    const m = new Map<string, CourseGridVisit[]>();
    for (const v of visits) {
      const arr = m.get(v.start_slot) ?? [];
      arr.push(v);
      m.set(v.start_slot, arr);
    }
    return m;
  }, [visits]);

  const capacity = capacityForWeekday(template, weekday);
  const capKey = capacityKeyForWeekday(weekday);
  // capacity が 0 の曜日 (例: 日曜) は描画しない (親が事前に判定するが念のため)
  if (!capKey) return null;

  const headerLabel = officeName
    ? `${officeName}-${template.label} コース (${capacity})`
    : `${template.label} コース (${capacity})`;

  const assignedStaffId = course?.assigned_staff_id ?? null;
  const courseExists = course != null;

  return (
    <section
      className="overflow-hidden rounded border border-border-default"
      data-testid={`course-day-table-${weekday}-${template.id}`}
      data-course-template-id={template.id}
      data-weekday={weekday}
    >
      {/* ヘッダー: コース名 + 担当 dropdown */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-default bg-bg-muted/40 px-3 py-2">
        <span className="text-sm font-semibold text-text-primary">{headerLabel}</span>
        <label className="flex items-center gap-1 text-[11px] text-text-secondary">
          <span aria-hidden>👤</span>
          <span>担当:</span>
          <select
            value={assignedStaffId ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onChangeAssignedStaff(v === '' ? null : v);
            }}
            disabled={!canEdit || !courseExists || isStaffMutating}
            className="rounded border border-border-default bg-bg-base px-1.5 py-0.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
            data-testid={`course-staff-select-${weekday}-${template.id}`}
            aria-label={`${headerLabel} の担当スタッフ`}
            title={
              courseExists
                ? '担当スタッフを変更します'
                : '「週を生成」を実行するとコースが作成され担当を変更できます'
            }
          >
            <option value="">未割当</option>
            {staffOptions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 5 列テーブル: 時間帯 / 氏名 / 住所 / 複数 / 条件 */}
      <div className="overflow-x-auto">
        <div
          className="grid border-t border-border-default text-[11px]"
          style={{
            gridTemplateColumns: '64px minmax(140px, 1.4fr) minmax(180px, 2fr) 56px 80px',
          }}
        >
          {/* 列ヘッダー */}
          <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
            時間帯
          </div>
          <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
            氏名
          </div>
          <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
            住所
          </div>
          <div className="border-b border-r border-border-default bg-bg-muted px-1.5 py-1 text-center text-[10px] font-semibold text-text-muted">
            複数
          </div>
          <div className="border-b border-border-default bg-bg-muted px-1.5 py-1 text-[10px] font-semibold text-text-muted">
            条件
          </div>

          {/* 35 行 */}
          {TIME_SLOTS.map((time) => (
            <CourseTimeRow
              key={time}
              weekday={weekday}
              templateId={template.id}
              templateNotes={template.notes ?? null}
              time={time}
              occupants={occupants.get(time) ?? []}
              canEdit={canEdit}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────────

interface CourseTimeRowProps {
  weekday: number;
  templateId: string;
  templateNotes: string | null;
  time: string;
  occupants: CourseGridVisit[];
  canEdit: boolean;
}

function CourseTimeRow({
  weekday,
  templateId,
  templateNotes,
  time,
  occupants,
  canEdit,
}: CourseTimeRowProps) {
  const droppableId = courseDayCellDroppableId(weekday, templateId, time);
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !canEdit,
    data: { kind: 'course-day-cell', weekday, courseTemplateId: templateId, time },
  });

  // 30 分境界 (HH:00 / HH:30) は時刻ラベル強調. それ以外は薄く.
  const showLabel = time.endsWith(':00') || time.endsWith(':30');
  const isMulti = occupants.some((o) => (o.required_staff_count ?? 1) >= 2);
  const firstName =
    occupants
      .map((o) => o.patient_name ?? o.patient_id)
      .filter(Boolean)
      .join('・') || '';
  const firstAddress = occupants.find((o) => o.patient_address)?.patient_address ?? '';

  return (
    <>
      {/* 時間帯列 (固定 / 全スロットラベル表示) */}
      <div
        className={cn(
          'border-r border-t border-border-default/60 px-1.5 py-0.5 text-right tnum',
          showLabel
            ? 'bg-bg-muted/30 text-[10px] font-semibold text-text-secondary'
            : 'text-[10px] text-text-muted',
        )}
      >
        {time}
      </div>

      {/* 氏名 / 住所 / 複数 / 条件 — まとめて 1 つの droppable で囲む */}
      <div
        ref={setNodeRef}
        data-droppable={droppableId}
        data-weekday={weekday}
        data-time={time}
        data-course-template-id={templateId}
        className={cn(
          'col-span-4 grid grid-cols-subgrid border-t border-border-default/40 transition-colors',
          isOver && canEdit ? 'bg-brand-primary/10 ring-1 ring-brand-primary ring-inset' : '',
        )}
        style={{ gridColumn: 'span 4 / span 4' }}
      >
        <div className="border-r border-border-default/40 px-1 py-0.5 text-[11px] leading-tight text-text-primary truncate">
          {firstName}
        </div>
        <div className="border-r border-border-default/40 px-1 py-0.5 text-[10px] leading-tight text-text-secondary truncate">
          {firstAddress}
        </div>
        <div className="border-r border-border-default/40 px-1 py-0.5 text-center text-[10px] leading-tight text-text-secondary">
          {isMulti ? '複数' : ''}
        </div>
        <div className="px-1 py-0.5 text-[10px] leading-tight text-text-secondary truncate">
          {templateNotes ?? ''}
        </div>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/** "HH:MM[:SS]" → 15 分境界に切り下げた "HH:MM". 範囲外 (9:30 未満 / 18:00 超) は null. */
export function floorToCourseSlot(rawTime: string): string | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)/.exec(rawTime);
  if (!m) return null;
  const hh = Number(m[1]);
  const mm = Number(m[2]);
  const total = hh * 60 + mm;
  const start = TIME_SLOT_START_HOUR * 60 + TIME_SLOT_START_MINUTE;
  const end = TIME_SLOT_END_HOUR * 60 + TIME_SLOT_END_MINUTE;
  if (total < start) return null;
  if (total > end) return null;
  const minutesFromStart = total - start;
  const flooredFromStart = minutesFromStart - (minutesFromStart % TIME_SLOT_MINUTES);
  const flooredTotal = start + flooredFromStart;
  const fhh = Math.floor(flooredTotal / 60);
  const fmm = flooredTotal % 60;
  return `${String(fhh).padStart(2, '0')}:${String(fmm).padStart(2, '0')}`;
}
