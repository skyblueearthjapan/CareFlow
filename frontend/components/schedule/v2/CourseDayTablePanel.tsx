'use client';

/**
 * CourseDayTablePanel — Wave 17 Phase B-2 メインパネル.
 *
 * Excel スケジュール枠組みに完全準拠した 1 画面構造:
 *   ┌─ ヘッダー ────────────────────────────────────────────┐
 *   │  曜日タブ [月][火][水][木][金][土]                     │
 *   │  [ 週を生成 ] [ 自動割付 ]   ← admin/manager only       │
 *   ├──────────────────────────────────────────────────────┤
 *   │  選択曜日のコーステーブル N 個 (縦並び)                 │
 *   │   - 本店 A / B / C / D / E / M + 都賀 A 等             │
 *   │   - 各テーブル 5 列 × 35 行 (15min, 9:30〜18:00)        │
 *   ├──────────────────────────────────────────────────────┤
 *   │  保留プール (DnD ソース)                                │
 *   └──────────────────────────────────────────────────────┘
 *
 * 主な機能:
 *   - 曜日タブで月〜土を切替 (capacity_<wd> > 0 の曜日のみ表示)
 *   - 「週を生成」 = Layer 1 (visits 再構築)
 *   - 「自動割付」 = Layer 3 (スタッフ自動割付)
 *   - 各コーステーブルの担当 dropdown で PATCH /api/v1/courses/{id}
 *   - プールセル → コーステーブル行ドロップ → place-and-fix
 *
 * RBAC:
 *   - admin / manager: 編集可 (ドロップ + 週生成 + 自動割付 + 担当変更)
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
import { useQueries } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { format } from 'date-fns';
import { Loader2, RefreshCw, UserCheck } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { addDays } from '@/components/schedule/WeekSelector';
import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import { useCourses, useUpdateCourse, type CourseV2Read } from '@/lib/queries/courses';
import { useGenerateWeekOnly } from '@/lib/queries/generate_week';
import { useAssignStaffOnly } from '@/lib/queries/assign_staff_only';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useStaffList } from '@/lib/queries/staff';
import { useVisits } from '@/lib/queries/visits';
import { capacityForWeekday, type CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { PatientRead } from '@/lib/schemas/patient';

import { AcceptanceLegend } from './AcceptanceLayer';
import {
  CourseDayTable,
  floorToCourseSlot,
  parseCourseDayCellId,
  type CourseGridVisit,
} from './CourseDayTable';
import { PatientCard } from './PatientCard';
import { PoolPanel } from './PoolPanel';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

/** 表示曜日 (月〜土の 6 つ). 日曜は除外. */
export const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

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
// Course resolution helpers
// ─────────────────────────────────────────────────────────────────────────

/**
 * (course_template, weekday, isoYear, isoWeek) に対応する週次コース行を引く。
 * 一致条件: office_id 一致 + course.code が template.label の頭文字大文字と一致.
 */
export function findCourseForTemplate(args: {
  template: CourseTemplateRead;
  weekday: number;
  isoYear: number;
  isoWeek: number;
  courses: CourseV2Read[];
}): CourseV2Read | null {
  const { template, weekday, isoYear, isoWeek, courses } = args;
  const expectedCode = (template.label || '').trim().slice(0, 1).toUpperCase();
  const found = courses.find(
    (c) =>
      c.office_id === template.office_id &&
      c.iso_year === isoYear &&
      c.iso_week === isoWeek &&
      c.weekday === weekday &&
      String(c.code).toUpperCase() === expectedCode &&
      !c.deleted_at,
  );
  return found ?? null;
}

// ─────────────────────────────────────────────────────────────────────────
// Component props
// ─────────────────────────────────────────────────────────────────────────

export interface CourseDayTablePanelProps {
  weekStart: Date;
  /** null = 全拠点モード, それ以外は単一拠点フィルタ. */
  officeId: string | null;
  canEdit: boolean;
  /** 受入目安レイヤー ON/OFF (フッター凡例のみ). */
  showAcceptanceLayer: boolean;
}

// ─────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────

export function CourseDayTablePanel({
  weekStart,
  officeId,
  canEdit,
  showAcceptanceLayer,
}: CourseDayTablePanelProps) {
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // ─── 曜日タブ state ────────────────────────────────────────────────
  const [activeWeekday, setActiveWeekday] = useState<number>(0);

  // ─── Master data ────────────────────────────────────────────────────
  const officesQuery = useOffices({ limit: 50 });
  const offices = officesQuery.allOffices;

  const staffListQuery = useStaffList({ limit: 200 });
  const allStaff = useMemo(() => staffListQuery.data ?? [], [staffListQuery.data]);

  // ─── 表示拠点 (course_templates / staff dropdown 用) ───────────────
  // 単一拠点モードならその office のみ。全拠点モードは全 office を対象。
  const officeIdsToFetch = useMemo(() => {
    if (officeId) return [officeId];
    return offices.map((o) => o.id);
  }, [officeId, offices]);

  const { data: session, status: sessionStatus } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  // 各拠点のテンプレートを並列 fetch.
  const templatesQueries = useQueries({
    queries: officeIdsToFetch.map((oid) => ({
      queryKey: ['course-templates', 'list', oid],
      enabled: sessionStatus === 'authenticated' && Boolean(oid),
      queryFn: () =>
        fetcher<CourseTemplateRead[]>(
          `/api/v1/course-templates?office_id=${encodeURIComponent(oid)}`,
          { accessToken, refreshToken },
        ),
    })),
  });

  const templates = useMemo<CourseTemplateRead[]>(
    () => templatesQueries.flatMap((q) => q.data ?? []).filter((t) => !t.deleted_at),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [templatesQueries],
  );

  // ─── Patients (プール + 氏名 / 住所解決) ──────────────────────────
  const patientsQuery = usePatients({ limit: 500 });
  const allPatients = useMemo(() => patientsQuery.data?.items ?? [], [patientsQuery.data]);
  const patientById = useMemo(() => {
    const m = new Map<string, PatientRead>();
    for (const p of allPatients) m.set(p.id, p);
    return m;
  }, [allPatients]);

  // ─── Visits (当週) ────────────────────────────────────────────────
  const weekStartStr = format(weekStart, 'yyyy-MM-dd');
  const weekEndStr = format(addDays(weekStart, 6), 'yyyy-MM-dd');
  const visitsQuery = useVisits({ week_start: weekStartStr, week_end: weekEndStr });
  const weekVisits = useMemo(() => visitsQuery.data?.items ?? [], [visitsQuery.data]);

  // ─── Courses (当週: course_template の逆引き / 担当 dropdown 用) ──
  const coursesQuery = useCourses({ iso_year: isoYear, iso_week: isoWeek, limit: 200 });
  const courses = useMemo(() => coursesQuery.data ?? [], [coursesQuery.data]);

  // ─── 拠点別 active staff (担当 dropdown の選択肢) ───────────────
  const staffByOffice = useMemo(() => {
    const m = new Map<string, typeof allStaff>();
    for (const s of allStaff) {
      if (s.status !== 'active') continue;
      const oid = s.primary_office_id;
      if (!oid) continue;
      const arr = m.get(oid) ?? [];
      arr.push(s);
      m.set(oid, arr);
    }
    // 拠点ごとに kana / 氏名でソート
    for (const [k, arr] of m.entries()) {
      m.set(
        k,
        [...arr].sort((a, b) => (a.kana ?? a.name).localeCompare(b.kana ?? b.name, 'ja')),
      );
    }
    return m;
  }, [allStaff]);

  // ─── 表示するコース一覧: 「拠点 × テンプレート」を活性曜日 capacity > 0 でフィルタ ──
  const courseTablesForActiveDay = useMemo(() => {
    const list: Array<{
      template: CourseTemplateRead;
      officeName: string;
    }> = [];
    for (const t of templates) {
      const cap = capacityForWeekday(t, activeWeekday);
      if (cap <= 0) continue;
      const officeName = offices.find((o) => o.id === t.office_id)?.name ?? '';
      list.push({ template: t, officeName });
    }
    // 表示順: 拠点名 → label (A/B/C/.../M)
    list.sort((a, b) => {
      const oa = a.officeName.localeCompare(b.officeName, 'ja');
      if (oa !== 0) return oa;
      return (a.template.label || '').localeCompare(b.template.label || '', 'ja');
    });
    return list;
  }, [templates, offices, activeWeekday]);

  // ─── visits を (course_id, slot) → CourseGridVisit[] にバケット化 ──
  // course_id 経由で template に逆引きする (BE Layer 1 が visits.course_id を埋める前提)。
  const visitsByCourse = useMemo(() => {
    const m = new Map<string, CourseGridVisit[]>();
    for (const v of weekVisits) {
      const cid = v.course_id ?? null;
      if (!cid) continue;
      const slot = floorToCourseSlot(v.start_time ?? '');
      if (!slot) continue;
      const patient = patientById.get(v.patient_id);
      const arr = m.get(cid) ?? [];
      arr.push({
        id: v.id,
        patient_id: v.patient_id,
        patient_name: patient?.name ?? v.patient_name ?? null,
        patient_address: patient?.address ?? null,
        // BE `_serialize_visit` が常に返す (default 1, 2 名体制で 2)。
        // 古い payload で欠落しても 1 にフォールバックする。
        required_staff_count: (v.required_staff_count ?? 1) as 1 | 2,
        start_slot: slot,
      });
      m.set(cid, arr);
    }
    return m;
  }, [weekVisits, patientById]);

  // ─── Pool patients (当週いずれの visit にも未配置な active 患者) ─
  const placedPatientIds = useMemo(() => {
    const s = new Set<string>();
    for (const v of weekVisits) {
      if (v.visit_date) {
        // 該当週の visit を持つ患者は 1 度でも配置されているので除外
        s.add(v.patient_id);
      }
    }
    return s;
  }, [weekVisits]);

  const poolPatients = useMemo(
    () =>
      allPatients.filter((p) => p.status === 'active' && !placedPatientIds.has(p.id)).slice(0, 200),
    [allPatients, placedPatientIds],
  );

  // ─── DnD ──────────────────────────────────────────────────────────
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
    const cell = parseCourseDayCellId(String(over.id));
    if (!cell) return;
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }

    try {
      const patient = patientById.get(patientId);
      const wp = (patient?.weekly_pattern ?? null) as { service_minutes?: number } | null;
      const durationMin = Math.max(1, Number(wp?.service_minutes ?? 60));
      await placeAndFixMut.mutateAsync({
        patient_id: patientId,
        course_template_id: cell.courseTemplateId,
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

  // ─── 「週を生成」 (Layer 1) / 「自動割付」 (Layer 3) ────────────
  const generateWeekMut = useGenerateWeekOnly();
  const assignStaffOnlyMut = useAssignStaffOnly();

  const handleGenerateWeek = async () => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      const res = await generateWeekMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
      });
      toast.success(`週次 visits を生成しました (visits=${res.visits_created})`);
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

  const handleAssignStaff = async () => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      const res = await assignStaffOnlyMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_id: officeId,
      });
      toast.success(`スタッフを自動割付しました (courses=${res.courses_assigned})`);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`自動割付に失敗しました: ${msg}`);
    }
  };

  // ─── 担当 dropdown 変更 (PATCH /courses/{id}) ───────────────────
  const updateCourseMut = useUpdateCourse();

  const handleChangeAssignedStaff = async (courseId: string, staffId: string | null) => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    try {
      await updateCourseMut.mutateAsync({
        id: courseId,
        patch: { assigned_staff_id: staffId },
      });
      toast.success(staffId ? '担当を更新しました' : '担当を未割当にしました');
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`担当の更新に失敗しました: ${msg}`);
    }
  };

  // ─── Render ──────────────────────────────────────────────────────
  if (officesQuery.isLoading || staffListQuery.isLoading) {
    return (
      <div className="space-y-2" data-testid="course-day-table-loading">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <section className="space-y-3" data-testid="course-day-table-panel">
        {/* ヘッダー: 曜日タブ + 「週を生成」「自動割付」 */}
        <Card className="space-y-2 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            {/* 曜日タブ */}
            <div
              role="tablist"
              aria-label="曜日タブ"
              className="flex gap-1"
              data-testid="course-day-tabs"
            >
              {DISPLAY_WEEKDAYS.map((wd) => {
                const selected = activeWeekday === wd;
                return (
                  <button
                    key={wd}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    aria-controls={`course-day-panel-${wd}`}
                    onClick={() => setActiveWeekday(wd)}
                    data-testid={`course-day-tab-${wd}`}
                    className={`rounded border px-3 py-1 text-xs font-semibold ${
                      selected
                        ? 'border-brand-primary bg-brand-primary text-white'
                        : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted'
                    }`}
                  >
                    {WEEKDAY_LABELS[wd]}{' '}
                    <span className="tnum text-[10px] opacity-80">
                      {format(addDays(weekStart, wd), 'M/d')}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="flex items-center gap-2">
              <span className="tnum text-[11px] text-text-muted">{isoWeekLabel}</span>
              {canEdit ? (
                <>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={handleGenerateWeek}
                    disabled={generateWeekMut.isPending}
                    data-testid="generate-week-button"
                  >
                    {generateWeekMut.isPending ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                    ) : (
                      <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                    )}
                    週を生成
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleAssignStaff}
                    disabled={assignStaffOnlyMut.isPending}
                    data-testid="assign-staff-only-button"
                  >
                    {assignStaffOnlyMut.isPending ? (
                      <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                    ) : (
                      <UserCheck className="mr-1 h-4 w-4" aria-hidden />
                    )}
                    自動割付
                  </Button>
                </>
              ) : null}
            </div>
          </div>
        </Card>

        {/* メイン: 当該曜日のコーステーブル N 個 */}
        <div
          id={`course-day-panel-${activeWeekday}`}
          role="tabpanel"
          aria-labelledby={`course-day-tab-${activeWeekday}`}
          className="space-y-3"
          data-testid="course-day-table-list"
        >
          {courseTablesForActiveDay.length === 0 ? (
            <Card className="p-4 text-sm text-text-muted">
              {WEEKDAY_LABELS[activeWeekday]}曜日の表示対象コースがありません。 拠点マスタの
              コーステンプレート (定員) を確認してください。
            </Card>
          ) : (
            courseTablesForActiveDay.map(({ template, officeName }) => {
              const course = findCourseForTemplate({
                template,
                weekday: activeWeekday,
                isoYear,
                isoWeek,
                courses,
              });
              const visits = course ? (visitsByCourse.get(course.id) ?? []) : [];
              const staffOptions = staffByOffice.get(template.office_id) ?? [];
              return (
                <CourseDayTable
                  key={`${template.id}:${activeWeekday}`}
                  weekday={activeWeekday}
                  template={template}
                  course={course}
                  officeName={officeName}
                  visits={visits}
                  staffOptions={staffOptions}
                  canEdit={canEdit}
                  isStaffMutating={updateCourseMut.isPending}
                  onChangeAssignedStaff={(staffId) => {
                    if (!course) {
                      toast.warning(
                        '先に「週を生成」を押してコースを作成してから担当を設定してください',
                      );
                      return;
                    }
                    void handleChangeAssignedStaff(course.id, staffId);
                  }}
                />
              );
            })
          )}
        </div>

        {/* 受入目安レイヤー凡例 (任意) */}
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
