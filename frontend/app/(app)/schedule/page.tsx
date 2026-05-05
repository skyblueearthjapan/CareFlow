'use client';

/**
 * /schedule — スケジュール v2 (W7-FE2: Must-fix #7).
 *
 * 3 タブ構成で L1→L2→L3 の v2 フロー全体を 1 画面に統合。
 *
 *   Tab ①: Pool / 15-min Grid (ScheduleGridV2 + PendingRequestPanel)
 *   Tab ②: コース提案 (CourseProposal — Layer 2)
 *   Tab ③: スタッフ割付 (CourseProposal + StaffAssignButton — Layer 3)
 *
 * 進行ステップ:
 *   - Pool 確定済 (patients に weekly_pattern entries あり) → Tab ② のボタン有効
 *   - コース確定済 (course_fixed な Course が存在) → Tab ③ のボタン有効
 *
 * RBAC:
 *   - admin / manager: 全タブ操作可
 *   - staff: ① 閲覧のみ (各 canEdit=false を渡す)
 *
 * 週 state はここで一元管理し、各タブコンポーネントへ isoYear/isoWeek を
 * props として渡すことで、タブ切替時に再 fetch が発生しない (React Query の
 * 同一 queryKey によるキャッシュ共有)。
 */

import { useMemo, useState } from 'react';
import { CheckCircle2, Circle } from 'lucide-react';
import { useSession } from 'next-auth/react';

import { PendingRequestPanel } from '@/components/schedule/v2/PendingRequestPanel';
import { ScheduleGridV2 } from '@/components/schedule/v2/ScheduleGridV2';
import { CourseProposal } from '@/components/schedule/v2/CourseProposal';
import { WeekSelector, toWeekStart } from '@/components/schedule/WeekSelector';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { useCourses } from '@/lib/queries/courses';
import { usePatients } from '@/lib/queries/patients';

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────

/** Date → ISO year/week (ISO 8601). */
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
// Progress step indicator
// ─────────────────────────────────────────────────────────────────────────

interface StepProps {
  done: boolean;
  label: string;
  stepNumber: number;
}

function Step({ done, label, stepNumber }: StepProps) {
  return (
    <div className="flex items-center gap-1.5 text-xs">
      {done ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden />
      ) : (
        <Circle className="h-4 w-4 shrink-0 text-text-muted" aria-hidden />
      )}
      <span className={'font-medium ' + (done ? 'text-success' : 'text-text-muted')}>
        {stepNumber}. {label}
      </span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────

export default function SchedulePage() {
  const { data: session } = useSession();
  const role = session?.user?.role ?? 'staff';
  const canEdit = role === 'admin' || role === 'manager';

  // 週 state — ScheduleGridV2 の内部 state と独立して管理し、
  // 週切替を全タブで共有する。ScheduleGridV2 自体も内部で weekStart を
  // 管理しているが、CourseProposal へ同じ週を渡すためにここでも持つ。
  const [weekStart, setWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const { isoYear, isoWeek } = useMemo(() => toIsoYearWeek(weekStart), [weekStart]);

  // ─────── Step 1 判定: Pool 確定済 (週訪問パターン登録済の患者あり) ───────
  // 患者マスタを参照して「1 件でも weekly_pattern が登録された患者が居れば確定済」と見なす。
  // WeeklyPattern は frequency_per_week / preferred_weekdays を持つので、それを判定材料とする。
  // 厳密な Layer 1 状態は BE に無いため UI 上の近似判定。
  const patientsQuery = usePatients({ limit: 500 });
  const step1Done = useMemo(() => {
    const patients = patientsQuery.data?.items ?? [];
    return patients.some((p) => {
      const wp = p.weekly_pattern as
        | { frequency_per_week?: unknown; preferred_weekdays?: unknown }
        | null
        | undefined;
      if (!wp || typeof wp !== 'object') return false;
      const freq = typeof wp.frequency_per_week === 'number' ? wp.frequency_per_week : 0;
      const days = Array.isArray(wp.preferred_weekdays) ? wp.preferred_weekdays.length : 0;
      return freq > 0 && days > 0;
    });
  }, [patientsQuery.data]);

  // ─────── Step 2 判定: コース確定済 (当該週に course_fixed な Course が存在) ───────
  const coursesQuery = useCourses({
    iso_year: isoYear,
    iso_week: isoWeek,
    course_status: 'course_fixed',
    limit: 10,
  });
  const step2Done = useMemo(() => (coursesQuery.data ?? []).length > 0, [coursesQuery.data]);

  // ─────── Step 3 判定: スタッフ割付済 (当該週に staff_assigned な Course が存在) ───────
  const coursesAssignedQuery = useCourses({
    iso_year: isoYear,
    iso_week: isoWeek,
    course_status: 'staff_assigned',
    limit: 10,
  });
  const step3Done = useMemo(
    () => (coursesAssignedQuery.data ?? []).length > 0,
    [coursesAssignedQuery.data],
  );

  const isoWeekLabel = `${isoYear}-W${String(isoWeek).padStart(2, '0')}`;

  return (
    <section className="space-y-4">
      <header>
        <h1 className="font-serif text-2xl font-bold text-text-primary">スケジュール</h1>
        <p className="text-sm text-text-secondary">
          L1 (Pool 配置) → L2 (コース提案・確定) → L3 (スタッフ割付) の v2 フローを 1
          画面で操作できます。
        </p>
      </header>

      {/* 進行ステップ表示 */}
      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-border-default bg-bg-muted px-4 py-2">
        <span className="text-xs font-semibold text-text-muted">{isoWeekLabel}</span>
        <div className="flex flex-wrap items-center gap-4">
          <Step done={step1Done} label="Pool 確定" stepNumber={1} />
          <Step done={step2Done} label="コース確定" stepNumber={2} />
          <Step done={step3Done} label="スタッフ割付済" stepNumber={3} />
        </div>
      </div>

      {/* 3 タブ */}
      <Tabs defaultValue="pool" className="w-full">
        <TabsList className="mb-2">
          <TabsTrigger value="pool">
            {step1Done ? '① Pool / 15-min Grid ✓' : '① Pool / 15-min Grid'}
          </TabsTrigger>
          <TabsTrigger value="course">{step2Done ? '② コース提案 ✓' : '② コース提案'}</TabsTrigger>
          <TabsTrigger value="staff">
            {step3Done ? '③ スタッフ割付 ✓' : '③ スタッフ割付'}
          </TabsTrigger>
        </TabsList>

        {/* ─── Tab ①: Pool / 15-min Grid ─── */}
        <TabsContent value="pool">
          <ScheduleGridV2
            canEdit={canEdit}
            showPendingPanel
            pendingPanelSlot={<PendingRequestPanel />}
            weekStart={weekStart}
            onWeekChange={setWeekStart}
          />
        </TabsContent>

        {/* ─── Tab ②: コース提案 (Layer 2) ─── */}
        <TabsContent value="course">
          <div className="mb-3">
            <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
          </div>
          {!step1Done && canEdit ? (
            <p className="mb-3 text-sm text-text-muted">
              先に <strong>① Pool / 15-min Grid</strong> タブで週レイアウトを固定してください。
            </p>
          ) : null}
          <CourseProposal isoYear={isoYear} isoWeek={isoWeek} canEdit={canEdit && step1Done} />
        </TabsContent>

        {/* ─── Tab ③: スタッフ割付 (Layer 3) ─── */}
        <TabsContent value="staff">
          <div className="mb-3">
            <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
          </div>
          {!step2Done && canEdit ? (
            <p className="mb-3 text-sm text-text-muted">
              先に <strong>② コース提案</strong> タブでコース構成を確定してください。
            </p>
          ) : null}
          <CourseProposal isoYear={isoYear} isoWeek={isoWeek} canEdit={canEdit && step2Done} />
        </TabsContent>
      </Tabs>
    </section>
  );
}
