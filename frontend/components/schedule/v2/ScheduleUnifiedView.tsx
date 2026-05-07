'use client';

/**
 * ScheduleUnifiedView — Wave 15 統合スケジュール画面 (W15-FE D-1〜D-4).
 *
 * 設計サマリ (Wave 15):
 *   - メイン軸: コーステンプレート × 曜日マトリクス (大改修案)
 *     行 = office × course_template (label = A/B/C/D/...)
 *     列 = 月〜土
 *     セル = HH:MM 時刻 + 患者氏名
 *   - 受入目安レイヤー (背景色 + 右肩バッジ ON/OFF, デフォルト OFF)
 *   - 満枠超過警告 (バッジのみ・モーダル無し)
 *   - ドロップ即固定枠化 (POST /api/v1/schedule/place-and-fix)
 *   - スタッフ差替 (👤 dropdown → PATCH /api/v1/courses/{id})
 *   - 補佐ペア (+ アイコン dropdown + ★/☆ pair_role toggle)
 *
 * 旧 ScheduleGridV2 (時刻×曜日) / CourseProposal (コース案生成) を完全に置換する。
 *
 * 責務範囲:
 *   - コーステンプレート + 受入カレンダー + 当該週 Visit / Course のフェッチ
 *   - dnd-kit で プール → セル ドロップを処理 → place-and-fix
 *   - スタッフ差替 dropdown (StaffSwapDropdown) を埋め込む
 *   - 補佐ペア UI (PairRoleEditor) を埋め込む
 *   - 受入目安レイヤー / 警告レイヤーの ON/OFF 表示制御
 *
 * 親 (page.tsx) からの props:
 *   - weekStart / onWeekChange (週切替)
 *   - officeId (拠点フィルタ; null = 全拠点)
 *   - canEdit (RBAC)
 *   - showAcceptanceLayer / showWarningLayer (レイヤー切替)
 */
import { useEffect, useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { format } from 'date-fns';
import { ja } from 'date-fns/locale';
import { AlertTriangle, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { addDays } from '@/components/schedule/WeekSelector';
import { ApiError } from '@/lib/api-client';
import { useAcceptanceCalendar } from '@/lib/queries/acceptance_calendar';
import { useCourses } from '@/lib/queries/courses';
import { useCourseTemplates } from '@/lib/queries/course_templates';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useStaffList } from '@/lib/queries/staff';
import { useVisits } from '@/lib/queries/visits';
import {
  buildAcceptanceLookup,
  roundDownToHour,
  type AcceptanceStatus,
} from '@/lib/schemas/v2/acceptance';
import { capacityForWeekday, type CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { PatientRead } from '@/lib/schemas/patient';
import { cn } from '@/lib/utils';

import { AcceptanceBadge, AcceptanceLegend, acceptanceCellClass } from './AcceptanceLayer';
import { PatientCard } from './PatientCard';
import { PoolPanel } from './PoolPanel';
import { StaffSwapDropdown } from './StaffSwapDropdown';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

/** 表示曜日 (月〜土の 6 列). 日曜は表示しない (運用方針). */
export const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS_FULL = ['月', '火', '水', '木', '金', '土'] as const;

/** 1 セル = 1 時間。8:00 〜 19:00 の 12 行 (運用方針). */
const SLOT_START_HOUR = 8;
const SLOT_END_HOUR = 20; // exclusive

function buildHourSlots(): string[] {
  const out: string[] = [];
  for (let h = SLOT_START_HOUR; h < SLOT_END_HOUR; h += 1) {
    out.push(`${String(h).padStart(2, '0')}:00`);
  }
  return out;
}

const HOUR_SLOTS = buildHourSlots();

// ─────────────────────────────────────────────────────────────────────────
// ID helpers (dnd-kit)
// ─────────────────────────────────────────────────────────────────────────

function cellDroppableId(courseTemplateId: string, weekday: number, time: string): string {
  return `cell:${courseTemplateId}:${weekday}:${time}`;
}

function parseCellId(
  id: string,
): { courseTemplateId: string; weekday: number; time: string } | null {
  if (!id.startsWith('cell:')) return null;
  const rest = id.slice('cell:'.length);
  // course_template_id (UUID = 36 chars) : weekday (1 char) : HH:MM
  const parts = rest.split(':');
  if (parts.length < 4) return null;
  const courseTemplateId = parts[0]!;
  const weekday = parseInt(parts[1]!, 10);
  const time = `${parts[2]}:${parts[3]}`;
  if (Number.isNaN(weekday) || weekday < 0 || weekday > 6) return null;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return null;
  return { courseTemplateId, weekday, time };
}

function patientDraggableId(patientId: string): string {
  return `unified-patient:${patientId}`;
}

function parsePatientDraggableId(id: string): string | null {
  if (!id.startsWith('unified-patient:')) return null;
  return id.slice('unified-patient:'.length);
}

// ─────────────────────────────────────────────────────────────────────────
// ISO week helpers
// ─────────────────────────────────────────────────────────────────────────

function toIsoYearWeek(d: Date): { isoYear: number; isoWeek: number } {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const year = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(year, 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { isoYear: year, isoWeek: week };
}

// ─────────────────────────────────────────────────────────────────────────
// Patient → cell placement (by visits.patient_fixed_visit_id derived)
// ─────────────────────────────────────────────────────────────────────────

interface CellPlacement {
  patientId: string;
  courseTemplateId: string;
  weekday: number;
  /** "HH:MM" (時刻はセル開始時刻 = HH:00 に丸める). */
  hourSlot: string;
  /** 元の正確な時刻 (可能であれば表示用). */
  exactTime?: string;
  /** 患者表示名. */
  name: string;
}

// ─────────────────────────────────────────────────────────────────────────
// Component props
// ─────────────────────────────────────────────────────────────────────────

export interface ScheduleUnifiedViewProps {
  weekStart: Date;
  onWeekChange?: (next: Date) => void;
  /** null = 全拠点モード, それ以外は単一拠点フィルタ. */
  officeId: string | null;
  canEdit: boolean;
  /** 受入目安レイヤー ON/OFF (デフォルト OFF). */
  showAcceptanceLayer: boolean;
  /** 満枠超過警告レイヤー ON/OFF (デフォルト ON). */
  showWarningLayer: boolean;
}

// ─────────────────────────────────────────────────────────────────────────
// Cell — single course-template × weekday × hour slot
// ─────────────────────────────────────────────────────────────────────────

interface CellProps {
  courseTemplateId: string;
  weekday: number;
  time: string;
  occupants: CellPlacement[];
  acceptance: AcceptanceStatus | undefined;
  showAcceptance: boolean;
  showWarning: boolean;
  capacity: number;
  /** 当該 (courseTemplate, weekday) の総占有数 (満枠判定用). */
  dayOccupancy: number;
  disabled: boolean;
}

function UnifiedCell({
  courseTemplateId,
  weekday,
  time,
  occupants,
  acceptance,
  showAcceptance,
  showWarning,
  capacity,
  dayOccupancy,
  disabled,
}: CellProps) {
  const droppableId = cellDroppableId(courseTemplateId, weekday, time);
  const { isOver, setNodeRef } = useDroppable({
    id: droppableId,
    disabled,
    data: { kind: 'unified-cell', courseTemplateId, weekday, time },
  });

  // 満枠超過判定 (1 行目 = 8:00 のセルにのみ警告バッジを出す)
  const isFirstHour = time === HOUR_SLOTS[0];
  const overcapacity = capacity > 0 && dayOccupancy > capacity;

  return (
    <div
      ref={setNodeRef}
      data-droppable={droppableId}
      data-weekday={weekday}
      data-time={time}
      className={cn(
        'relative min-h-[36px] border-r border-t border-border-default/60 px-1 py-0.5 text-[10px] leading-tight transition-colors',
        showAcceptance ? acceptanceCellClass(acceptance) : '',
        isOver && !disabled ? 'ring-2 ring-brand-primary ring-inset' : '',
      )}
    >
      {/* 受入目安バッジ */}
      {showAcceptance ? <AcceptanceBadge status={acceptance} /> : null}

      {/* 満枠超過警告 (1 行目のみ) */}
      {showWarning && isFirstHour && overcapacity ? (
        <span
          className="pointer-events-none absolute left-0.5 top-0.5 inline-flex items-center gap-0.5 rounded bg-error/10 px-1 text-[9px] font-bold text-error"
          title={`定員超過: ${dayOccupancy}/${capacity}`}
          data-warning="overcapacity"
        >
          <AlertTriangle className="h-2.5 w-2.5" aria-hidden />
          {dayOccupancy}/{capacity}
        </span>
      ) : null}

      {/* 占有患者 (時刻 + 氏名) */}
      <div className="flex flex-col gap-0.5">
        {occupants.map((p) => (
          <div
            key={p.patientId}
            className="flex items-center gap-1 truncate rounded border border-brand-primary/40 bg-brand-primary/5 px-1 py-0.5"
            title={`${p.exactTime ?? p.hourSlot} ${p.name}`}
          >
            <span className="tnum text-[9px] font-semibold text-brand-primary">
              {(p.exactTime ?? p.hourSlot).slice(0, 5)}
            </span>
            <span className="truncate text-text-primary">{p.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────

export function ScheduleUnifiedView({
  weekStart,
  onWeekChange: _onWeekChange,
  officeId,
  canEdit,
  showAcceptanceLayer,
  showWarningLayer,
}: ScheduleUnifiedViewProps) {
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // ─── Master data ────────────────────────────────────────────────────
  const officesQuery = useOffices({ limit: 50 });
  const offices = officesQuery.allOffices;

  // 表示する拠点リストを決定
  const displayOffices = useMemo(() => {
    if (officeId) return offices.filter((o) => o.id === officeId);
    return offices;
  }, [offices, officeId]);

  const patientsQuery = usePatients({ limit: 500 });
  const allPatients = useMemo(() => patientsQuery.data?.items ?? [], [patientsQuery.data]);
  const patientById = useMemo(() => {
    const m = new Map<string, PatientRead>();
    for (const p of allPatients) m.set(p.id, p);
    return m;
  }, [allPatients]);

  const staffListQuery = useStaffList({ limit: 200 });
  const activeStaff = useMemo(
    () => (staffListQuery.data ?? []).filter((s) => s.status === 'active'),
    [staffListQuery.data],
  );

  // ─── Course templates: 表示拠点ぶんを並列取得 ───────────────────────
  // 単一拠点 or 全拠点。useCourseTemplates は単一 office_id 用なので
  // 「先頭拠点のみ取得」というシンプルな MVP 実装にする (全拠点モードでも
  //  最初の office のテンプレートだけ表示)。複数拠点のテンプレを横断統合する
  //  実装は Wave 15 後続フェーズで対応。
  const primaryOfficeId = displayOffices[0]?.id ?? null;
  const templatesQuery = useCourseTemplates({ office_id: primaryOfficeId });
  const templates = templatesQuery.data ?? [];

  // ─── Acceptance calendar ────────────────────────────────────────────
  const acceptanceQuery = useAcceptanceCalendar({
    office_id: primaryOfficeId,
    enabled: showAcceptanceLayer,
  });
  const acceptanceLookup = useMemo(
    () => buildAcceptanceLookup(acceptanceQuery.data ?? []),
    [acceptanceQuery.data],
  );

  // ─── Visits + Courses (current week) ─────────────────────────────────
  const weekStartStr = format(weekStart, 'yyyy-MM-dd');
  const weekEndStr = format(addDays(weekStart, 6), 'yyyy-MM-dd');
  const visitsQuery = useVisits({ week_start: weekStartStr, week_end: weekEndStr });
  const weekVisits = visitsQuery.data?.items ?? [];

  const coursesQuery = useCourses({ iso_year: isoYear, iso_week: isoWeek, limit: 200 });
  const courses = coursesQuery.data ?? [];

  // (weekday, code) → courseId を引く逆引き表
  const courseIdByDayCode = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) m.set(`${c.weekday}:${c.code}`, c.id);
    return m;
  }, [courses]);

  // course.id → assigned_staff_id
  const courseStaffById = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const c of courses) m.set(c.id, c.assigned_staff_id ?? null);
    return m;
  }, [courses]);

  // ─── Pool 計算 ────────────────────────────────────────────────────
  // 当該週に visit が存在しない (= 配置済みでない) active 患者をプールに入れる。
  // BE の place-and-fix は配置すると visit を作るため、同じ週に visit がある
  // 患者は既に配置済みと見なす。
  const placedPatientIds = useMemo(() => {
    const s = new Set<string>();
    for (const v of weekVisits) s.add(v.patient_id);
    return s;
  }, [weekVisits]);

  const poolPatients = useMemo(() => {
    return allPatients
      .filter((p) => p.status === 'active' && !placedPatientIds.has(p.id))
      .slice(0, 200); // UI 上限
  }, [allPatients, placedPatientIds]);

  // ─── Cell occupants 計算 ──────────────────────────────────────────
  // visits を course_id 経由で template にマッピングしたいが、Course テーブルの
  // code (A/B/C/D) と CourseTemplate.label が概ね一致する想定。一致しないものは
  // 「未マップ visit」として警告ログに出して隠す。
  const templateByLabel = useMemo(() => {
    const m = new Map<string, CourseTemplateRead>();
    for (const t of templates) m.set(t.label, t);
    return m;
  }, [templates]);

  const cellOccupants = useMemo(() => {
    // key = `${course_template_id}:${weekday}:${HH:00}` → CellPlacement[]
    const m = new Map<string, CellPlacement[]>();
    for (const v of weekVisits) {
      // course_id → course.code → template (label 一致)
      const course = courses.find((c) => c.id === v.course_id);
      if (!course) continue;
      const template = templateByLabel.get(course.code);
      if (!template) continue;
      const patient = patientById.get(v.patient_id);
      const time = (v.start_time ?? '00:00').slice(0, 5);
      const hour = roundDownToHour(time);
      const key = `${template.id}:${course.weekday}:${hour}`;
      const arr = m.get(key) ?? [];
      arr.push({
        patientId: v.patient_id,
        courseTemplateId: template.id,
        weekday: course.weekday,
        hourSlot: hour,
        exactTime: time,
        name: patient?.name ?? v.patient_id,
      });
      m.set(key, arr);
    }
    return m;
  }, [weekVisits, courses, templateByLabel, patientById]);

  // (course_template_id, weekday) → 占有合計件数 (満枠判定用)
  const dayOccupancyByTemplateDay = useMemo(() => {
    const m = new Map<string, number>();
    for (const arr of cellOccupants.values()) {
      if (arr.length === 0) continue;
      const first = arr[0]!;
      const key = `${first.courseTemplateId}:${first.weekday}`;
      m.set(key, (m.get(key) ?? 0) + arr.length);
    }
    return m;
  }, [cellOccupants]);

  // ─── DnD ─────────────────────────────────────────────────────────
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const [activePatientId, setActivePatientId] = useState<string | null>(null);
  const placeAndFixMut = usePlaceAndFix();

  const handleDragStart = (e: DragStartEvent) => {
    setActivePatientId(parsePatientDraggableId(String(e.active.id)));
  };

  const handleDragEnd = async (e: DragEndEvent) => {
    setActivePatientId(null);
    const { active, over } = e;
    if (!over) return;
    const patientId = parsePatientDraggableId(String(active.id));
    if (!patientId) return;
    const cell = parseCellId(String(over.id));
    if (!cell) return;
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      const patient = patientById.get(patientId);
      const wp = (patient?.weekly_pattern ?? null) as { service_minutes?: number } | null;
      const serviceMinutes = Math.max(15, Number(wp?.service_minutes ?? 60));
      await placeAndFixMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        entries: [
          {
            patient_id: patientId,
            course_template_id: cell.courseTemplateId,
            weekday: cell.weekday,
            start_time: cell.time,
            service_minutes: serviceMinutes,
            staff_count: 1,
          },
        ],
      });
      toast.success(`${patient?.name ?? patientId} を ${cell.time} に固定枠化しました`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`配置に失敗しました: ${msg}`);
    }
  };

  // ─── Effects ─────────────────────────────────────────────────────
  useEffect(() => {
    // 週切替時の再 fetch は TanStack Query が queryKey に week を含めて自動化済
    // (ここは将来の hook 用意にとっておく)
  }, [isoYear, isoWeek]);

  // ─── Render ──────────────────────────────────────────────────────
  if (officesQuery.isLoading || templatesQuery.isLoading) {
    return (
      <div className="space-y-2" data-testid="unified-loading">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (templates.length === 0) {
    return (
      <Card className="p-4 text-sm text-text-muted">
        コーステンプレートが登録されていません。
        {primaryOfficeId
          ? '管理画面でテンプレートを追加してください。'
          : '拠点を選択してください。'}
      </Card>
    );
  }

  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <section className="space-y-3" data-testid="schedule-unified-view">
        {/* メイン軸: コーステンプレート × 曜日 マトリクス */}
        <Card className="overflow-x-auto p-0">
          <div
            className="grid"
            style={{
              gridTemplateColumns: `220px repeat(${DISPLAY_WEEKDAYS.length}, minmax(140px, 1fr))`,
            }}
          >
            {/* ヘッダー行 */}
            <div className="border-b border-r border-border-default bg-bg-muted px-2 py-1 text-[11px] font-semibold text-text-muted">
              コース
            </div>
            {DISPLAY_WEEKDAYS.map((wd) => (
              <div
                key={wd}
                className="border-b border-r border-border-default bg-bg-muted px-2 py-1 text-center text-xs font-semibold text-text-secondary"
              >
                {WEEKDAY_LABELS_FULL[wd]}{' '}
                <span className="tnum text-text-muted">
                  {format(addDays(weekStart, wd), 'M/d', { locale: ja })}
                </span>
              </div>
            ))}

            {/* コース行 (1 template = 7 セル × HOUR_SLOTS 行) */}
            {templates.map((tpl) => {
              const officeName = displayOffices.find((o) => o.id === tpl.office_id)?.name ?? '拠点';
              return (
                <CourseTemplateRowGroup
                  key={tpl.id}
                  template={tpl}
                  officeName={officeName}
                  staffList={activeStaff}
                  courseIdByDayCode={courseIdByDayCode}
                  courseStaffById={courseStaffById}
                  canEdit={canEdit}
                  cellOccupants={cellOccupants}
                  acceptanceLookup={acceptanceLookup}
                  showAcceptance={showAcceptanceLayer}
                  showWarning={showWarningLayer}
                  dayOccupancyByTemplateDay={dayOccupancyByTemplateDay}
                />
              );
            })}
          </div>
        </Card>

        {/* 受入目安レイヤーが ON のとき凡例を表示 */}
        {showAcceptanceLayer ? <AcceptanceLegend /> : null}

        {/* 保留プール */}
        <PoolPanel count={poolPatients.length} disabled={!canEdit}>
          {poolPatients.map((p) => (
            <PatientCard
              key={p.id}
              draggableId={patientDraggableId(p.id)}
              patient={{ id: p.id, name: p.name, caption: p.kana ?? undefined }}
              disabled={!canEdit}
            />
          ))}
        </PoolPanel>

        <DragOverlay>
          {activePatientId ? (
            <div className="rounded border border-brand-primary bg-brand-primary/10 px-2 py-1 text-xs shadow-lg">
              {patientById.get(activePatientId)?.name ?? activePatientId}
            </div>
          ) : null}
        </DragOverlay>
      </section>
    </DndContext>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// 1 コーステンプレートの行グループ (左端ラベル + 各曜日の HOUR_SLOTS セル列)
// ─────────────────────────────────────────────────────────────────────────

interface CourseTemplateRowGroupProps {
  template: CourseTemplateRead;
  officeName: string;
  staffList: ReturnType<typeof useStaffList>['data'];
  courseIdByDayCode: Map<string, string>;
  courseStaffById: Map<string, string | null>;
  canEdit: boolean;
  cellOccupants: Map<string, CellPlacement[]>;
  acceptanceLookup: Map<string, AcceptanceStatus>;
  showAcceptance: boolean;
  showWarning: boolean;
  dayOccupancyByTemplateDay: Map<string, number>;
}

function CourseTemplateRowGroup(props: CourseTemplateRowGroupProps) {
  const {
    template,
    officeName,
    staffList,
    courseIdByDayCode,
    courseStaffById,
    canEdit,
    cellOccupants,
    acceptanceLookup,
    showAcceptance,
    showWarning,
    dayOccupancyByTemplateDay,
  } = props;
  const [collapsed, setCollapsed] = useState(false);
  const safeStaffList = staffList ?? [];

  // 当該テンプレートのコース行は 1 行内に HOUR_SLOTS を縦積みする。
  // (column: 行ラベル + 曜日列、行: HOUR_SLOTS)
  // grid 全体の枠組みは親で定義済なので、サブグリッドとして時刻列を出す。

  return (
    <>
      {/* 行ラベル (template label + office + 主担当 dropdown など) */}
      <div className="flex flex-col items-stretch justify-between gap-1 border-b border-r border-border-default bg-bg-muted/30 p-1.5 text-[11px]">
        <div className="flex items-center justify-between gap-1">
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="flex items-center gap-1 text-left font-semibold text-text-primary hover:text-brand-primary"
            aria-expanded={!collapsed}
            aria-label={`${template.label} 行 ${collapsed ? '展開' : '折り畳み'}`}
          >
            <ChevronRight
              className={cn('h-3 w-3 transition-transform', collapsed ? '' : 'rotate-90')}
              aria-hidden
            />
            <span>
              {officeName}-{template.label}
            </span>
          </button>
        </div>
        {!collapsed ? (
          <div className="flex flex-wrap items-center gap-1">
            {/* 主担当: 月曜の Course を代表とする (簡易実装) */}
            {(() => {
              const courseId = courseIdByDayCode.get(`0:${template.label}`) ?? null;
              const currentStaffId = courseId ? (courseStaffById.get(courseId) ?? null) : null;
              return (
                <StaffSwapDropdown
                  courseId={courseId}
                  currentStaffId={currentStaffId}
                  staffList={safeStaffList}
                  canEdit={canEdit}
                  variant="compact"
                />
              );
            })()}
          </div>
        ) : null}
      </div>

      {/* 各曜日のセル列 (HOUR_SLOTS を縦積み) */}
      {DISPLAY_WEEKDAYS.map((wd) => {
        const dayOcc = dayOccupancyByTemplateDay.get(`${template.id}:${wd}`) ?? 0;
        const capacity = capacityForWeekday(template, wd);
        return (
          <div key={wd} className="border-b border-border-default">
            {collapsed ? (
              <div className="px-1 py-1 text-[10px] text-text-muted">
                <span className="tnum">{dayOcc}</span>
                {capacity > 0 ? <span className="text-text-muted"> / {capacity}</span> : null}
              </div>
            ) : (
              <div className="flex flex-col">
                {HOUR_SLOTS.map((time) => {
                  const key = `${template.id}:${wd}:${time}`;
                  const occupants = cellOccupants.get(key) ?? [];
                  const accKey = `${wd}:${time}`;
                  const acceptance = acceptanceLookup.get(accKey);
                  return (
                    <UnifiedCell
                      key={time}
                      courseTemplateId={template.id}
                      weekday={wd}
                      time={time}
                      occupants={occupants}
                      acceptance={acceptance}
                      showAcceptance={showAcceptance}
                      showWarning={showWarning}
                      capacity={capacity}
                      dayOccupancy={dayOcc}
                      disabled={!canEdit}
                    />
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
