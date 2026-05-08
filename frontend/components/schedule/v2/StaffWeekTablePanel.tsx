'use client';

/**
 * StaffWeekTablePanel — Wave 16 Phase B-1〜B-5: スタッフ別週次スケジュール画面.
 *
 * 旧 Wave 15 ScheduleUnifiedView (コース×曜日マトリクス) を完全に置換し、
 * 「スタッフ別テーブル N 個縦並び」の構造を提供する。
 *
 * 表示構造:
 *   ┌─ 川名 千恵 (本店マネージャー) [今週: M コース] ──────────────┐
 *   │ 時刻×曜日 (9:00-19:00 / 15min / 月-土 6 列)               │
 *   └────────────────────────────────────────────────┘
 *   ┌─ 宇田川 優莉 (本店スタッフ) [今週: 月=A 火=B 水=A …] ───────┐
 *   │ 時刻×曜日 (同形式)                                       │
 *   └────────────────────────────────────────────────┘
 *   …
 *
 * 主な機能:
 *   - 全 active スタッフを 1 人 1 テーブルで縦並び (B-1)
 *   - 時刻軸 9:00〜19:00 / 15 分刻み / 41 行 (B-2)
 *   - 「週を生成」ボタンで POST /api/v1/schedule/generate-and-assign を叩く (B-3)
 *   - プールからセルへドロップで place-and-fix 連携
 *     (course_template_id は当該スタッフ × 当該曜日の Course 行から逆引き) (B-4)
 *   - 旧コース行 UI / StaffSwapDropdown / PairRoleEditor は削除 (B-5)
 *
 * RBAC:
 *   - admin / manager: 編集可 (ドロップ + 週を生成)
 *   - staff: 閲覧のみ
 */
import { useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { format } from 'date-fns';
import { Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { addDays } from '@/components/schedule/WeekSelector';
import { ApiError } from '@/lib/api-client';
import { useCourses, type CourseV2Read } from '@/lib/queries/courses';
import { useCourseTemplates } from '@/lib/queries/course_templates';
import { useGenerateAndAssign } from '@/lib/queries/generate_and_assign';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useStaffList } from '@/lib/queries/staff';
import { useVisits } from '@/lib/queries/visits';
import type { StaffRead } from '@/lib/schemas/staff';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { PatientRead } from '@/lib/schemas/patient';

import { AcceptanceLegend } from './AcceptanceLayer';
import { PatientCard } from './PatientCard';
import { PoolPanel } from './PoolPanel';
import { DISPLAY_WEEKDAYS, StaffTimeGrid, parseStaffCellId } from './StaffTimeGrid';

// ─────────────────────────────────────────────────────────────────────────
// Re-exports for tests / sibling components
// ─────────────────────────────────────────────────────────────────────────

/** 1 セル分の visit 集計用最小フィールド. */
export interface StaffGridVisit {
  id: string;
  patient_id: string;
  patient_name?: string | null;
  visit_date: string;
  start_time: string | null;
  primary_staff_id: string | null;
}

const WEEKDAY_LABELS_SHORT = ['月', '火', '水', '木', '金', '土', '日'] as const;

// ─────────────────────────────────────────────────────────────────────────
// dnd-kit helpers (プール用 draggable id)
// ─────────────────────────────────────────────────────────────────────────

function patientDraggableId(patientId: string): string {
  return `pool-patient:${patientId}`;
}

function parsePatientDraggableId(id: string): string | null {
  if (!id.startsWith('pool-patient:')) return null;
  return id.slice('pool-patient:'.length);
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
// Course template resolution helpers (B-4)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 当該スタッフが当週・当該曜日に担当しているコースから course_template_id を逆引きする。
 *
 * 解決順序:
 *   1. courses から (assigned_staff_id, iso_year, iso_week, weekday) を満たす行を探す
 *   2. CourseV2Read には template_id が露出しないため、Course.code (A/B/C/D/M) +
 *      Course.office_id を用いて CourseTemplate.label / office_id でマッチング
 *   3. ヒットしたテンプレートの id を返す。失敗したら null.
 *
 * 失敗ケース:
 *   - 当該スタッフがその曜日にコース割付されていない (= 「週を生成」未実行 or 休み)
 *   - テンプレートが拠点に登録されていない
 *   - course.code がテンプレート.label の頭文字と一致しない
 */
export function resolveCourseTemplateForStaff(args: {
  staffId: string;
  weekday: number;
  isoYear: number;
  isoWeek: number;
  courses: CourseV2Read[];
  templates: CourseTemplateRead[];
}): { templateId: string; officeId: string } | null {
  const { staffId, weekday, isoYear, isoWeek, courses, templates } = args;
  const course = courses.find(
    (c) =>
      c.assigned_staff_id === staffId &&
      c.iso_year === isoYear &&
      c.iso_week === isoWeek &&
      c.weekday === weekday &&
      !c.deleted_at,
  );
  if (!course) return null;
  const code = String(course.code);
  // CourseTemplate.label は通常 1 文字 (A/B/C/D/M…) だが、最大 8 文字まで許容される
  // (course_template スキーマ v2)。Course.code との対応は label の先頭 1 文字を
  // 大文字化したものが優先。同 office 内で複数候補があれば最初の一致を採用。
  const tpl = templates.find(
    (t) =>
      t.office_id === course.office_id && (t.label || '').trim().slice(0, 1).toUpperCase() === code,
  );
  if (!tpl) return null;
  return { templateId: tpl.id, officeId: tpl.office_id };
}

// ─────────────────────────────────────────────────────────────────────────
// Component props
// ─────────────────────────────────────────────────────────────────────────

export interface StaffWeekTablePanelProps {
  weekStart: Date;
  /** null = 全拠点モード, それ以外は単一拠点フィルタ. */
  officeId: string | null;
  canEdit: boolean;
  /** 受入目安レイヤー ON/OFF (フッター凡例の表示制御のみ). */
  showAcceptanceLayer: boolean;
}

// ─────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────

export function StaffWeekTablePanel({
  weekStart,
  officeId,
  canEdit,
  showAcceptanceLayer,
}: StaffWeekTablePanelProps) {
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // ─── Master data ────────────────────────────────────────────────────
  const officesQuery = useOffices({ limit: 50 });
  const offices = officesQuery.allOffices;

  const staffListQuery = useStaffList({ limit: 200 });
  const allStaff = useMemo(() => staffListQuery.data ?? [], [staffListQuery.data]);

  // 表示対象スタッフ: status='active' && (officeId が指定されていれば primary_office_id 一致)
  const displayStaff = useMemo(() => {
    let xs = allStaff.filter((s) => s.status === 'active');
    if (officeId) {
      xs = xs.filter((s) => s.primary_office_id === officeId);
    }
    // 表示順: マネージャー先頭 → 拠点別 → 氏名カナ
    return [...xs].sort((a, b) => {
      const ra = roleRank(a.role);
      const rb = roleRank(b.role);
      if (ra !== rb) return ra - rb;
      const ka = a.kana ?? a.name;
      const kb = b.kana ?? b.name;
      return ka.localeCompare(kb, 'ja');
    });
  }, [allStaff, officeId]);

  // ─── Course templates (全表示拠点ぶん) ────────────────────────────
  // Course → Template の逆引きには office_id 別のテンプレートが必要なので、
  // 表示対象拠点ごとに並列取得する。useCourseTemplates は単一 office_id 用なので
  // ここでは「全拠点モードでも displayStaff の primary_office_id 集合分だけ」
  // 取得する。多数 office があるケースは MVP では想定しないため簡易実装。
  const officeIdsToFetch = useMemo(() => {
    if (officeId) return [officeId];
    const set = new Set<string>();
    for (const s of displayStaff) {
      if (s.primary_office_id) set.add(s.primary_office_id);
    }
    return Array.from(set);
  }, [officeId, displayStaff]);

  // 単一 useCourseTemplates 呼び出しを 1 office に絞る MVP 実装. 全拠点モード時は
  // 先頭拠点のみテンプレート取得し、複数拠点合算が必要な場合は別 wave で対応.
  const primaryOfficeId = officeIdsToFetch[0] ?? null;
  const templatesQuery = useCourseTemplates({ office_id: primaryOfficeId });
  const templates = useMemo(() => templatesQuery.data ?? [], [templatesQuery.data]);

  // ─── Patients (プール + 氏名解決) ─────────────────────────────────
  const patientsQuery = usePatients({ limit: 500 });
  const allPatients = useMemo(() => patientsQuery.data?.items ?? [], [patientsQuery.data]);
  const patientById = useMemo(() => {
    const m = new Map<string, PatientRead>();
    for (const p of allPatients) m.set(p.id, p);
    return m;
  }, [allPatients]);

  // ─── Visits (当週) ─────────────────────────────────────────────
  const weekStartStr = format(weekStart, 'yyyy-MM-dd');
  const weekEndStr = format(addDays(weekStart, 6), 'yyyy-MM-dd');
  const visitsQuery = useVisits({ week_start: weekStartStr, week_end: weekEndStr });
  const weekVisits = useMemo(() => visitsQuery.data?.items ?? [], [visitsQuery.data]);

  // visits を staff_id で索引化 (StaffTimeGrid に渡すため)
  const visitsByStaffId = useMemo(() => {
    const m = new Map<string, StaffGridVisit[]>();
    for (const v of weekVisits) {
      const sid = v.primary_staff_id ?? null;
      if (!sid) continue;
      const arr = m.get(sid) ?? [];
      arr.push({
        id: v.id,
        patient_id: v.patient_id,
        patient_name: patientById.get(v.patient_id)?.name ?? v.patient_name ?? null,
        visit_date: v.visit_date,
        start_time: v.start_time ?? null,
        primary_staff_id: sid,
      });
      m.set(sid, arr);
    }
    return m;
  }, [weekVisits, patientById]);

  // ─── Courses (当週: スタッフ→曜日→コース割当の逆引き用) ────────
  const coursesQuery = useCourses({ iso_year: isoYear, iso_week: isoWeek, limit: 200 });
  const courses = useMemo(() => coursesQuery.data ?? [], [coursesQuery.data]);

  // 当週コース割付: staffId → (weekday → code)
  const staffWeekCourses = useMemo(() => {
    const m = new Map<string, Map<number, string>>();
    for (const c of courses) {
      if (!c.assigned_staff_id) continue;
      if (c.iso_year !== isoYear || c.iso_week !== isoWeek) continue;
      const inner = m.get(c.assigned_staff_id) ?? new Map<number, string>();
      inner.set(c.weekday, String(c.code));
      m.set(c.assigned_staff_id, inner);
    }
    return m;
  }, [courses, isoYear, isoWeek]);

  // ─── Pool patients ────────────────────────────────────────────
  // 当該週に visit (≠ 当該スタッフ別フィルタ) が無い active 患者をプールに入れる.
  const placedPatientIds = useMemo(() => {
    const s = new Set<string>();
    for (const v of weekVisits) s.add(v.patient_id);
    return s;
  }, [weekVisits]);

  const poolPatients = useMemo(
    () =>
      allPatients.filter((p) => p.status === 'active' && !placedPatientIds.has(p.id)).slice(0, 200),
    [allPatients, placedPatientIds],
  );

  // ─── DnD ─────────────────────────────────────────────────────
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 5 } }),
  );
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
    const cell = parseStaffCellId(String(over.id));
    if (!cell) return;
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }

    // 当該スタッフ × 当該曜日 のコースから course_template_id を逆引き
    const resolved = resolveCourseTemplateForStaff({
      staffId: cell.staffId,
      weekday: cell.weekday,
      isoYear,
      isoWeek,
      courses,
      templates,
    });
    if (!resolved) {
      toast.warning('先に「週を生成」を押してスタッフを割り付けてください');
      return;
    }

    try {
      const patient = patientById.get(patientId);
      const wp = (patient?.weekly_pattern ?? null) as { service_minutes?: number } | null;
      const durationMin = Math.max(1, Number(wp?.service_minutes ?? 60));
      await placeAndFixMut.mutateAsync({
        patient_id: patientId,
        course_template_id: resolved.templateId,
        iso_year: isoYear,
        iso_week: isoWeek,
        weekday: cell.weekday,
        start_time: cell.time,
        duration_min: durationMin,
        staff_count: 1,
        fix_pattern: true,
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

  // ─── 「週を生成」 (B-3) ────────────────────────────────────────
  const generateMut = useGenerateAndAssign();
  const handleGenerate = async () => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      const res = await generateMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
      });
      toast.success(
        `週次スケジュールを生成しました (visits=${res.visits_created}, courses=${res.courses_assigned})`,
      );
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`週の生成に失敗しました: ${msg}`);
    }
  };

  // ─── Render ──────────────────────────────────────────────────
  if (officesQuery.isLoading || staffListQuery.isLoading) {
    return (
      <div className="space-y-2" data-testid="staff-week-table-loading">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  const officeName = (id: string | null | undefined): string => {
    if (!id) return '';
    return offices.find((o) => o.id === id)?.name ?? '';
  };

  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <section className="space-y-3" data-testid="staff-week-table-panel">
        {/* ヘッダー: 「週を生成」ボタン (admin / manager only) */}
        <Card className="flex flex-wrap items-center justify-between gap-2 p-3">
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-text-primary">
              スタッフ別週次スケジュール
            </span>
            <span className="text-[11px] text-text-muted">
              ISO {isoYear}-W{String(isoWeek).padStart(2, '0')} ・ 表示スタッフ{' '}
              {displayStaff.length} 名
            </span>
          </div>
          {canEdit ? (
            <Button
              type="button"
              size="sm"
              onClick={handleGenerate}
              disabled={generateMut.isPending}
              data-testid="generate-and-assign-button"
            >
              {generateMut.isPending ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
              )}
              週を生成
            </Button>
          ) : null}
        </Card>

        {/* 全スタッフテーブル N 個縦並び */}
        <div className="space-y-3" data-testid="staff-week-table-list">
          {displayStaff.length === 0 ? (
            <Card className="p-4 text-sm text-text-muted">
              対象スタッフがいません。スタッフマスタで active なスタッフを登録してください。
            </Card>
          ) : (
            displayStaff.map((staff) => (
              <StaffPanelCard
                key={staff.id}
                staff={staff}
                officeName={officeName(staff.primary_office_id)}
                weekStart={weekStart}
                visits={visitsByStaffId.get(staff.id) ?? []}
                weekCourses={staffWeekCourses.get(staff.id) ?? null}
                canEdit={canEdit}
              />
            ))
          )}
        </div>

        {/* 受入目安レイヤー凡例 (任意表示) */}
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
// 1 スタッフ分のカード (ヘッダー + StaffTimeGrid)
// ─────────────────────────────────────────────────────────────────────────

interface StaffPanelCardProps {
  staff: StaffRead;
  officeName: string;
  weekStart: Date;
  visits: StaffGridVisit[];
  weekCourses: Map<number, string> | null;
  canEdit: boolean;
}

function StaffPanelCard({
  staff,
  officeName,
  weekStart,
  visits,
  weekCourses,
  canEdit,
}: StaffPanelCardProps) {
  // ヘッダー右側に当週コース割当を曜日別バッジで表示.
  // マネージャー (role='manager') は M コース固定として表示.
  const isManager = staff.role === 'manager';
  const fixedManagerLabel = isManager ? 'M コース (固定)' : null;

  return (
    <Card className="overflow-hidden p-0" data-testid={`staff-panel-${staff.id}`}>
      {/* スタッフヘッダー */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-default bg-bg-muted/40 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-text-primary">{staff.name}</span>
          <span className="text-[10px] text-text-muted">
            {officeName ? `${officeName} ・ ` : ''}
            {staff.role === 'admin'
              ? '管理者'
              : staff.role === 'manager'
                ? 'マネージャー'
                : 'スタッフ'}
          </span>
        </div>
        <div
          className="flex flex-wrap items-center gap-1 text-[10px] text-text-secondary"
          data-testid={`staff-panel-courses-${staff.id}`}
        >
          {fixedManagerLabel ? (
            <span className="rounded border border-warning/40 bg-warning/10 px-1.5 py-px font-semibold text-warning">
              {fixedManagerLabel}
            </span>
          ) : weekCourses && weekCourses.size > 0 ? (
            DISPLAY_WEEKDAYS.map((wd) => {
              const code = weekCourses.get(wd) ?? null;
              return (
                <span
                  key={wd}
                  className={`rounded border px-1 py-px font-medium ${
                    code
                      ? 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
                      : 'border-border-default bg-bg-muted/30 text-text-muted'
                  }`}
                  title={
                    code
                      ? `${WEEKDAY_LABELS_SHORT[wd]} = ${code}`
                      : `${WEEKDAY_LABELS_SHORT[wd]} 未割当`
                  }
                >
                  {WEEKDAY_LABELS_SHORT[wd]}={code ?? '—'}
                </span>
              );
            })
          ) : (
            <span className="text-text-muted">未割当 (「週を生成」で自動配置)</span>
          )}
        </div>
      </div>

      {/* 時刻×曜日テーブル */}
      <StaffTimeGrid staffId={staff.id} weekStart={weekStart} visits={visits} canEdit={canEdit} />
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

function roleRank(role: StaffRead['role']): number {
  switch (role) {
    case 'admin':
      return 0;
    case 'manager':
      return 1;
    case 'staff':
    default:
      return 2;
  }
}
