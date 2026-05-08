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
import { useDraggable, useDroppable } from '@dnd-kit/core';
import { X } from 'lucide-react';

import { cn } from '@/lib/utils';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import { capacityForWeekday, capacityKeyForWeekday } from '@/lib/schemas/v2/course_template';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';

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
 * Wave 18 Phase B-5: 配置済み visit の draggable id.
 * 親 (CourseDayTablePanel) は `parseVisitDraggableId` で visit 移動を識別する。
 */
export function visitDraggableId(visitId: string): string {
  return `visit:${visitId}`;
}

export function parseVisitDraggableId(id: string): string | null {
  if (!id.startsWith('visit:')) return null;
  return id.slice('visit:'.length);
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
  /**
   * Wave 18 Phase B-1: 「複数」表示は **患者マスタ** の
   * `patient.requires_multiple_staff` を真値とする (visit.required_staff_count
   * から離脱)。同一スロットに 2 件以上 visit が並ぶ場合も `CourseTimeRow` で
   * 「複数」扱い (重なり = 同時刻 2 件処理) は維持する。
   */
  patient_requires_multiple_staff: boolean;
  /**
   * Wave 18 Phase B-2: 「条件」列に **患者マスタ** の `sex_restriction`
   * (女性のみ / 男性のみ) を template.notes と統合表示するため事前正規化済み
   * のラベル (例: '女性のみ' / null)。
   */
  patient_sex_restriction_label: string | null;
  /**
   * 旧フィールド (互換のため残す。Wave 18 以前の挙動: required_staff_count >= 2
   * を「複数」と扱っていた)。直近のテストやログ確認用に残置するが、表示判定からは
   * 除外する。
   */
  required_staff_count: number;
  /** "HH:MM" (15 分境界に切り下げ済み). */
  start_slot: string;
  /**
   * Wave 37 Phase 3-C: 2 名体制の visit_group_id (BE Phase 2-A で付与).
   * - null  → 単独 visit (= 1 名体制 or 2 名体制 patient で slot 1 が未配置の片割れ)
   * - UUID → 同じ visit_group_id を持つペアが BE 上に存在 (slot 0/1 共有)
   */
  visit_group_id?: string | null;
  /**
   * Wave 37 Phase 3-C: ①/② バッジ用 slot 番号 (1 or 2).
   * 親が同 visit_group_id 内の 2 visit を sort し、先頭=1 / 後尾=2 を割当てる。
   * visit_group_id=null のときは undefined。
   */
  group_slot_label?: 1 | 2;
  /**
   * Wave 37 Phase 3-C: 同 visit_group_id 内の partner visit の表示ラベル (tooltip 用).
   * 例: "佐藤 花子 ② (本店-B コース)"
   */
  partner_label?: string | null;
  /**
   * Wave 37 Phase 3-C: patient.requires_multiple_staff=true なのに同 visit_group_id
   * の partner が存在しない (= slot 1 が未配置) ときに true。
   * 「複数 ① のみ」と警告色で表示する。
   */
  partner_missing?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────
// Wave 28: event_type → 日本語ラベル変換
// ─────────────────────────────────────────────────────────────────────────

/**
 * DB の event type 文字列 (または EventRead.type) を日本語ラベルに変換。
 * EventRead.type は既に日本語 ('研修' / 'イベント') の場合はそのまま返す。
 */
export const EVENT_TYPE_LABEL: Record<string, string> = {
  training: '研修',
  meeting: '会議',
  event: 'イベント',
  errand: '役所同行',
  interview: '初回面談',
  office: '事務',
  other: 'その他',
  // 既存の日本語 type (EventRead.type) もパスルックアップで対応
  研修: '研修',
  イベント: 'イベント',
};

/** EventRead.type (日本語 or DB snake_case) を日本語ラベルに変換するヘルパー。 */
export function eventTypeLabel(type: string): string {
  return EVENT_TYPE_LABEL[type] ?? type;
}

/**
 * Wave 30: EventRead を「種別: タイトル HH:MM-HH:MM」または「種別 HH:MM-HH:MM」形式に整形。
 * title が存在する場合は「種別: タイトル 開始-終了」、ない場合は「種別 開始-終了」。
 */
export function formatEventLabel(e: {
  type: string;
  title?: string | null;
  start_time: string;
  end_time: string;
}): string {
  const type = eventTypeLabel(e.type);
  const time = `${e.start_time}-${e.end_time}`;
  return e.title ? `${type}: ${e.title} ${time}` : `${type} ${time}`;
}

/**
 * Wave 31: CourseWeekOverview の 2 行表示用ヘルパー。
 * 1 行目: 種別 + タイトル (例: "研修: 接遇マナー")
 * 2 行目: 時刻範囲 (例: "14:00-16:00")
 */
export function formatEventLabelLines(e: {
  type: string;
  title?: string | null;
  start_time: string;
  end_time: string;
}): { title: string; time: string } {
  const type = eventTypeLabel(e.type);
  return {
    title: e.title ? `${type}: ${e.title}` : type,
    time: `${e.start_time}-${e.end_time}`,
  };
}

// ─────────────────────────────────────────────────────────────────────────
// Wave 27 Phase B: Event conflict helpers
// ─────────────────────────────────────────────────────────────────────────

/**
 * 指定スタッフが当該曜日に event を持つ場合、そのラベル文字列を返す。
 * 複数 event がある場合は最初の 1 件のみ使用。
 * weekday: 0=Mon, 1=Tue, ..., 5=Sat (JS getDay: 0=Sun, 1=Mon → 変換必要)
 */
export function getStaffEventsForWeekday(
  staffId: string,
  weekday: number,
  staffEventsByStaff: Map<string, EventRead[]>,
  weekDayDate?: Date,
): EventRead[] {
  const events = staffEventsByStaff.get(staffId) ?? [];
  return events.filter((ev) => {
    const evDate = new Date(ev.date + 'T00:00:00');
    // weekday: 0=Mon → JS getDay: 1; 5=Sat → 6; 6=Sun → 0
    const jsDay = (weekday + 1) % 7;
    // If we have the actual date, match exactly. Otherwise match by weekday.
    if (weekDayDate) {
      return ev.date === weekDayDate.toISOString().slice(0, 10);
    }
    return evDate.getDay() === jsDay;
  });
}

/**
 * visit の start_time と event の start_time/end_time の重複チェック。
 * visit の start_slot (HH:MM) が event の時間帯内に入るかどうか判定。
 */
export function hasEventConflict(visitStartSlot: string, events: EventRead[]): EventRead | null {
  for (const ev of events) {
    if (visitStartSlot >= ev.start_time && visitStartSlot < ev.end_time) {
      return ev;
    }
  }
  return null;
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
  /**
   * Wave 27 Phase B-2/B-3: staffId → EventRead[] のマップ。
   * 担当 dropdown の option に event ラベルを付与し、セル level の warning バッジを出す。
   */
  staffEventsByStaff?: Map<string, EventRead[]>;
  canEdit: boolean;
  /** 担当 dropdown 変更時のハンドラ. course が null のときは null 渡し (新規 case). */
  onChangeAssignedStaff: (staffId: string | null) => void;
  /** dropdown が pending 中 (PATCH /courses/{id} 中) は disabled に. */
  isStaffMutating: boolean;
  /**
   * Wave 36: visit の × ボタンクリック時のハンドラ.
   * canEdit=true のときのみ表示される。
   */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
}

export function CourseDayTable({
  weekday,
  template,
  course,
  officeName,
  visits,
  staffOptions,
  staffEventsByStaff,
  canEdit,
  onChangeAssignedStaff,
  isStaffMutating,
  onDeleteVisit,
}: CourseDayTableProps) {
  const eventsMap = staffEventsByStaff ?? new Map<string, EventRead[]>();
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

  const capKey = capacityKeyForWeekday(weekday);
  // capacity が 0 の曜日 (例: 日曜) は描画しない (親が事前に判定するが念のため)
  if (!capKey) return null;

  const headerLabel = officeName
    ? `${officeName}-${template.label} コース (6)`
    : `${template.label} コース (6)`;

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
            {staffOptions.map((s) => {
              const dayEvents = getStaffEventsForWeekday(s.id, weekday, eventsMap);
              const ev = dayEvents[0];
              const label = ev ? `${s.name} [${ev.type} ${ev.start_time}-${ev.end_time}]` : s.name;
              return (
                <option key={s.id} value={s.id}>
                  {label}
                </option>
              );
            })}
          </select>
        </label>
      </div>

      {/* 5 列テーブル: 時間帯 / 氏名 / 住所 / 複数 / 条件 */}
      <div className="overflow-x-auto">
        <div
          className="grid border-t border-border-default text-[11px]"
          style={{
            gridTemplateColumns: '60px minmax(80px, 0.6fr) minmax(120px, 1fr) 70px 100px',
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
              time={time}
              occupants={occupants.get(time) ?? []}
              canEdit={canEdit}
              assignedStaffId={assignedStaffId}
              staffEventsByStaff={eventsMap}
              onDeleteVisit={onDeleteVisit}
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
  time: string;
  occupants: CourseGridVisit[];
  canEdit: boolean;
  /** Wave 27 Phase B-3: 担当スタッフ ID (course.assigned_staff_id). */
  assignedStaffId?: string | null;
  /** Wave 27 Phase B-3: staffId → EventRead[] のマップ. */
  staffEventsByStaff?: Map<string, EventRead[]>;
  /** Wave 36: visit × ボタンクリックハンドラ. */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
}

function CourseTimeRow({
  weekday,
  templateId,
  time,
  occupants,
  canEdit,
  assignedStaffId,
  staffEventsByStaff,
  onDeleteVisit,
}: CourseTimeRowProps) {
  // Wave 27 Phase B-3: 担当スタッフがこのスロット時間帯にイベントを持つか判定
  const eventsMap = staffEventsByStaff ?? new Map<string, EventRead[]>();
  const staffDayEvents = assignedStaffId
    ? getStaffEventsForWeekday(assignedStaffId, weekday, eventsMap)
    : [];
  const conflictingEvent =
    assignedStaffId && occupants.length > 0 ? hasEventConflict(time, staffDayEvents) : null;

  // Wave 28 Phase B-1: このスロット (15分) にかかる event を抽出 (visit の有無に関わらず表示)
  const eventsAtSlot = staffDayEvents.filter((e) => time >= e.start_time && time < e.end_time);
  const droppableId = courseDayCellDroppableId(weekday, templateId, time);
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled: !canEdit,
    data: { kind: 'course-day-cell', weekday, courseTemplateId: templateId, time },
  });

  // 30 分境界 (HH:00 / HH:30) は時刻ラベル強調. それ以外は薄く.
  const showLabel = time.endsWith(':00') || time.endsWith(':30');

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
        data-occupant-count={occupants.length}
        className={cn(
          'col-span-4 grid grid-cols-subgrid border-t border-border-default/40 transition-colors',
          isOver && canEdit ? 'bg-brand-primary/10 ring-1 ring-brand-primary ring-inset' : '',
        )}
        style={{ gridColumn: 'span 4 / span 4' }}
      >
        {occupants.length === 0 ? (
          // ── 空セル: 4 列を空のまま描画 (droppable hit-area 維持) ─────
          // Wave 28 B-1: 担当スタッフの event があれば氏名列に表示
          <>
            <div className="border-r border-border-default/40 px-1 py-0.5 text-[11px] leading-tight text-text-primary truncate">
              {eventsAtSlot.length > 0 ? (
                <div
                  className="text-[10px] text-yellow-800 bg-yellow-100 px-1 rounded ring-1 ring-yellow-300 truncate"
                  data-testid="event-slot-row"
                  title={eventsAtSlot.map((e) => formatEventLabel(e)).join(', ')}
                >
                  {eventsAtSlot.map((e, i) => (
                    <span key={e.id}>
                      {i > 0 ? ', ' : ''}
                      {formatEventLabel(e)}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="border-r border-border-default/40 px-1 py-0.5 text-[10px] leading-tight text-text-secondary truncate" />
            <div className="border-r border-border-default/40 px-1 py-0.5 text-center text-[10px] leading-tight text-text-secondary" />
            <div className="px-1 py-0.5 text-[10px] leading-tight text-text-secondary truncate" />
          </>
        ) : (
          // ── 1 件以上: 各 occupant を縦積みで列ごとに描画 ────────────
          // 同一スロットに 2 件以上の visit がある場合、氏名 / 住所 / 複数 / 条件 を
          // visit ごとに改行ベースで縦積みする (Excel の「複数同時」表現に追従)。
          <>
            <div className="border-r border-border-default/40 px-1 py-0.5 text-[11px] leading-tight text-text-primary">
              <div className="flex flex-col gap-0.5">
                {occupants.map((o) => (
                  <OccupantNameDraggable
                    key={`name-${o.id}`}
                    visitId={o.id}
                    label={o.patient_name ?? o.patient_id}
                    canEdit={canEdit}
                    onDeleteVisit={onDeleteVisit}
                    groupSlotLabel={o.group_slot_label}
                    partnerLabel={o.partner_label ?? null}
                  />
                ))}
                {/* Wave 27 Phase B-3: 担当スタッフのイベント重複 warning バッジ */}
                {conflictingEvent ? (
                  <span
                    className="inline-flex items-center gap-0.5 rounded bg-yellow-100 px-1 py-0.5 text-[10px] font-semibold text-yellow-800 ring-1 ring-yellow-300"
                    data-testid="event-conflict-badge"
                    title={`担当者: ${conflictingEvent.type} ${conflictingEvent.start_time}-${conflictingEvent.end_time}`}
                  >
                    ⚠ 担当不可
                  </span>
                ) : null}
                {/* Wave 28 B-1: 担当スタッフのイベント行 (visit と並んで表示) */}
                {eventsAtSlot.length > 0 ? (
                  <div
                    className="text-[10px] text-yellow-800 bg-yellow-100 px-1 rounded ring-1 ring-yellow-300 truncate"
                    data-testid="event-slot-row"
                    title={eventsAtSlot.map((e) => formatEventLabel(e)).join(', ')}
                  >
                    {eventsAtSlot.map((e, i) => (
                      <span key={e.id}>
                        {i > 0 ? ', ' : ''}
                        {formatEventLabel(e)}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="border-r border-border-default/40 px-1 py-0.5 text-[10px] leading-tight text-text-secondary">
              <div className="flex flex-col gap-0.5">
                {occupants.map((o) => (
                  <div key={`addr-${o.id}`} className="truncate" title={o.patient_address ?? ''}>
                    {o.patient_address ?? ''}
                  </div>
                ))}
              </div>
            </div>
            <div className="border-r border-border-default/40 px-1 py-0.5 text-center text-[10px] leading-tight text-text-secondary">
              <div className="flex flex-col gap-0.5">
                {occupants.map((o) => {
                  // Wave 23: 「複数」= patient.requires_multiple_staff のみ。
                  // sameSlotMulti (同一スロット 2 件以上) は判定から除外。
                  const isMulti = o.patient_requires_multiple_staff === true;
                  // Wave 37 Phase 3-C:
                  //   - visit_group_id 持ち + group_slot_label あり → "複数 ①" / "複数 ②"
                  //   - patient.requires_multiple_staff=true なのに partner 不在 (partner_missing=true)
                  //     → "複数 ① のみ" を text-warning で表示
                  let label: string = isMulti ? '複数' : '';
                  let warning = false;
                  if (isMulti && o.group_slot_label) {
                    label = `複数 ${o.group_slot_label === 1 ? '①' : '②'}`;
                  } else if (isMulti && o.partner_missing) {
                    label = '複数 ① のみ';
                    warning = true;
                  }
                  return (
                    <div
                      key={`multi-${o.id}`}
                      aria-hidden={!isMulti}
                      data-testid={`course-occupant-multi-${o.id}`}
                      className={warning ? 'text-warning font-semibold' : ''}
                      title={o.partner_label ?? undefined}
                    >
                      {label}
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="px-1 py-0.5 text-[10px] leading-tight text-text-secondary">
              <div className="flex flex-col gap-0.5">
                {occupants.map((o) => {
                  // Wave 23: 「条件」= patient.sex_restriction のみ。
                  // template.notes は条件列に表示しない。
                  const text = o.patient_sex_restriction_label ?? '';
                  return (
                    <div
                      key={`cond-${o.id}`}
                      className="truncate"
                      title={text}
                      data-testid={`course-occupant-condition-${o.id}`}
                    >
                      {text}
                    </div>
                  );
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Sub-components — Wave 18 Phase B-5: 配置済み visit を draggable に
// ─────────────────────────────────────────────────────────────────────────

interface OccupantNameDraggableProps {
  visitId: string;
  label: string;
  canEdit: boolean;
  /** Wave 36: visit × ボタンクリックハンドラ. canEdit=true のときのみ表示. */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /** Wave 37 Phase 3-C: 2 名体制 ① / ② バッジ (visit_group_id 内の slot 番号). */
  groupSlotLabel?: 1 | 2;
  /** Wave 37 Phase 3-C: ペア相手の表示用ラベル (tooltip 用). */
  partnerLabel?: string | null;
}

/**
 * 配置済み visit の氏名セル (drag handle).
 *
 * - draggableId = `visit:${visitId}` で親が「visit 移動」を識別。
 * - canEdit=false (staff) のときは draggable 無効化。
 * - dragging 中は半透明 + cursor-grabbing。
 *
 * ドロップ先 (course-day-cell:* / pool) は親 onDragEnd で分岐し、
 * delete + place-and-fix の 2 段階で実装する (Wave 19 で atomic 化予定)。
 *
 * Wave 37 Phase 3-C:
 *   - groupSlotLabel が指定されたら患者名右に "①" / "②" バッジを付与する。
 *   - partnerLabel が指定されたら tooltip (title) に "ペア: ..." を表示する。
 */
function OccupantNameDraggable({
  visitId,
  label,
  canEdit,
  onDeleteVisit,
  groupSlotLabel,
  partnerLabel,
}: OccupantNameDraggableProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: visitDraggableId(visitId),
    disabled: !canEdit,
    data: { kind: 'placed-visit', visitId },
  });

  const tooltip = partnerLabel ? `${label} (ペア: ${partnerLabel})` : label;
  const badge = groupSlotLabel === 1 ? '①' : groupSlotLabel === 2 ? '②' : null;

  return (
    <div
      className={cn(
        'group flex items-center justify-between gap-0.5',
        isDragging ? 'opacity-40' : '',
      )}
    >
      <div
        ref={setNodeRef}
        data-testid={`course-occupant-name-${visitId}`}
        data-draggable-visit-id={visitId}
        data-visit-group-slot={groupSlotLabel ?? ''}
        title={tooltip}
        {...listeners}
        {...attributes}
        className={cn(
          'truncate select-none touch-none flex-1',
          canEdit ? 'cursor-grab active:cursor-grabbing' : '',
        )}
      >
        {label}
        {badge ? (
          <span
            className="ml-1 inline-flex items-center text-[10px] font-semibold text-brand-primary"
            data-testid={`course-occupant-group-badge-${visitId}`}
            aria-label={`スロット ${groupSlotLabel}`}
          >
            {badge}
          </span>
        ) : null}
      </div>
      {canEdit && onDeleteVisit && (
        <button
          type="button"
          className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 hover:bg-red-100 rounded flex-shrink-0"
          onClick={(e) => {
            e.stopPropagation();
            onDeleteVisit(visitId, label);
          }}
          aria-label={`${label} の訪問を削除`}
          title="この訪問を削除"
          data-testid={`delete-visit-btn-${visitId}`}
        >
          <X className="h-3 w-3 text-red-500" />
        </button>
      )}
    </div>
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
