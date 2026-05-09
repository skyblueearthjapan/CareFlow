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
import { buildStaffEventsMap, useWeekStaffEvents } from '@/lib/queries/staff-events';
import { useDeleteVisit, useVisits } from '@/lib/queries/visits';
import { capacityForWeekday, type CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import {
  SEX_RESTRICTION_LABEL,
  coerceWeeklyPattern,
  formatPreferredTimeLabel,
  normalizePatientSexRestriction,
  type PatientRead,
} from '@/lib/schemas/patient';

import { AcceptanceLegend } from './AcceptanceLayer';
import {
  CourseDayTable,
  floorToCourseSlot,
  parseCourseDayCellId,
  parseVisitDraggableId,
  type CourseGridVisit,
} from './CourseDayTable';
import { CourseWeekOverview, type WeekOverviewVisit } from './CourseWeekOverview';
import { PartnerCourseDialog } from './PartnerCourseDialog';
import { PatientCard } from './PatientCard';
import {
  POOL_DROPPABLE_ID,
  PoolGroupedByWeekday,
  buildPoolDraggableId,
  parsePoolDraggableId,
} from './PoolPanel';
import type { SlotIndex } from '@/lib/schemas/v2/patient_fixed_visit';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

/** 表示曜日 (月〜土の 6 つ). 日曜は除外. */
export const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

// ─────────────────────────────────────────────────────────────────────────
// dnd-kit helpers (プール用 draggable id)
//
// W37 Phase 3-B: slot 番号付き id (`pool-patient:{id}:slot:0|1`) も解釈する.
// 構築は `buildPoolDraggableId(id, slot)` (PoolPanel から re-export)、
// 解析は `parsePoolDraggableId` を経由して旧形式と新形式を統一する。
// `parsePatientDraggableId` は patient_id だけが欲しい既存の drag end handler
// 用に薄いラッパとして残す (slot 情報は handleDragEnd で活用する Phase 3-C
// で `parsePoolDraggableId` を直接呼ぶ想定)。
// ─────────────────────────────────────────────────────────────────────────

function parsePatientDraggableId(id: string): string | null {
  const parsed = parsePoolDraggableId(id);
  return parsed ? parsed.patientId : null;
}

// ─────────────────────────────────────────────────────────────────────────
// Error helpers
// ─────────────────────────────────────────────────────────────────────────

function formatErr(err: unknown): string {
  if (err instanceof ApiError) return `${err.status} ${err.message}`;
  if (err instanceof Error) return err.message;
  return '不明なエラー';
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

  // ─── 曜日タブ state (Wave 18 Phase B-6: 'week' = 週間ビュー) ─────
  const [activeTab, setActiveTab] = useState<number | 'week'>(0);
  const activeWeekday = typeof activeTab === 'number' ? activeTab : 0;

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

  // Wave 18 Codex-fix 中-2: ``templatesQueries`` は ``useQueries`` が毎回新しい
  // 配列インスタンスを返すため、そのまま deps に置くと毎レンダーで useMemo が
  // 再評価され、結果として ``templates`` の参照が安定しない (= 下流の
  // ``courseTablesForActiveDay`` 等のキャッシュも壊れる)。
  //
  // データ識別子 (dataUpdatedAt + status) を join した stable key を deps に
  // することで、各 query の状態が変わったときだけ再計算するようにする。
  // ``q.data`` は TanStack Query が refetch ごとに新しい参照を返すため、
  // dataUpdatedAt のほうが安定 dep として正しい。
  const templatesDepKey = templatesQueries.map((q) => `${q.dataUpdatedAt}:${q.status}`).join(',');
  const templates = useMemo<CourseTemplateRead[]>(() => {
    return templatesQueries.flatMap((q) => q.data ?? []).filter((t) => !t.deleted_at);
    // ESLint exhaustive-deps を満たしつつ stable identity を維持するため、
    // ``templatesQueries`` ではなく派生 stable key (``templatesDepKey``) を deps に置く。
    // ``templatesQueries`` 自体は毎レンダー新インスタンスだが、中身が同じ
    // (= 同 dataUpdatedAt / status) なら計算結果も同じなので問題ない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templatesDepKey]);

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

  // ─── Wave 27 Phase B-1: 当週全スタッフの events バッチ取得 ─────────────
  const allStaffIds = useMemo(() => allStaff.map((s) => s.id), [allStaff]);
  const weekStartDate = useMemo(() => weekStart, [weekStart]);
  const weekEndDate = useMemo(() => addDays(weekStart, 6), [weekStart]);
  const { data: staffEventsData } = useWeekStaffEvents(allStaffIds, weekStartDate, weekEndDate);

  /** staffId → EventRead[] のルックアップ Map */
  const staffEventsByStaff = useMemo(
    () => buildStaffEventsMap(allStaffIds, staffEventsData),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [allStaffIds.join(','), staffEventsData],
  );

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

  // ─── Wave 18 Phase B-6 / Wave 37 P3-C: course_id → course_template_id の逆引き ──
  // (元 line 467 から移設: visitsByCourse / partner ラベル解決から参照されるため上に移動)
  const courseTemplateByCourseId = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) {
      // course.code (大文字 1 文字) と template.label の頭文字を office_id + 大文字一致で結ぶ
      const tpl = templates.find(
        (t) =>
          t.office_id === c.office_id &&
          (t.label || '').trim().slice(0, 1).toUpperCase() === String(c.code).toUpperCase(),
      );
      if (tpl) m.set(c.id, tpl.id);
    }
    return m;
  }, [courses, templates]);

  // ─── Wave 37 Phase 3-C / W37 hotfix M-3: course_id → course.code (A/B/C..) マップ ──
  // 同 group 内 visit を course.code 文字順で sort して slot 0/1 を決定論的に割当てる
  // (id 文字列順だと UUID 辞書順となりユーザーの「コース 1=A, 2=B」選択順と無関係に
  // ①/② が入れ替わるため). visit_group_id を持たない visit や course_id=null の
  // 場合はキーが取れず後段でフォールバック.
  const courseCodeByCourseId = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) {
      m.set(c.id, String(c.code ?? ''));
    }
    return m;
  }, [courses]);

  // ─── Wave 37 Phase 3-C: visit_group_id → 同 group 内 visit[] のマップ ──
  // 同じ visit_group_id を持つ 2 visit が BE Phase 2-A で作成される (slot 0/1)。
  // W37 hotfix M-3: 各 group 内は course.code (A/B/C..) 順で sort.
  // course.code 同値 / 取得不能なら visit.id 昇順でフォールバック (決定論を維持).
  const visitsByGroupId = useMemo(() => {
    const m = new Map<string, typeof weekVisits>();
    for (const v of weekVisits) {
      const gid = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      if (!gid) continue;
      const arr = m.get(gid) ?? [];
      arr.push(v);
      m.set(gid, arr);
    }
    const codeOf = (v: (typeof weekVisits)[number]): string => {
      const cid = v.course_id ?? null;
      return cid ? (courseCodeByCourseId.get(cid) ?? '') : '';
    };
    for (const [k, arr] of m.entries()) {
      m.set(
        k,
        [...arr].sort((a, b) => {
          const ca = codeOf(a);
          const cb = codeOf(b);
          if (ca !== cb) return ca.localeCompare(cb);
          return a.id.localeCompare(b.id);
        }),
      );
    }
    return m;
  }, [weekVisits, courseCodeByCourseId]);

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
      // Wave 18 Phase B-1: 患者マスタ由来の `requires_multiple_staff` を読む。
      // BE Phase 0+A 完成前は欠落可。安全に false にフォールバック。
      const requiresMulti =
        (patient as { requires_multiple_staff?: boolean | null } | undefined)
          ?.requires_multiple_staff === true;
      // Wave 18 Phase B-2: 「条件」表示の sex_restriction ラベル。
      const sexRestrict = normalizePatientSexRestriction(
        patient?.sex_restriction as string | null | undefined,
      );
      const sexLabel = sexRestrict ? SEX_RESTRICTION_LABEL[sexRestrict] : null;

      // Wave 37 Phase 3-C: visit_group_id 経由で slot 番号 (1/2) と partner ラベルを解決
      const groupId = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      let groupSlotLabel: 1 | 2 | undefined = undefined;
      let partnerLabel: string | null = null;
      let partnerMissing = false;
      if (groupId) {
        const groupVisits = visitsByGroupId.get(groupId) ?? [];
        if (groupVisits.length === 2) {
          const idx = groupVisits.findIndex((gv) => gv.id === v.id);
          groupSlotLabel = (idx === 0 ? 1 : 2) as 1 | 2;
          const partner = groupVisits[idx === 0 ? 1 : 0];
          if (partner) {
            const partnerPatient = patientById.get(partner.patient_id);
            const pname = partnerPatient?.name ?? partner.patient_name ?? partner.patient_id;
            const partnerCid = partner.course_id ?? null;
            const partnerTplId = partnerCid
              ? (courseTemplateByCourseId.get(partnerCid) ?? null)
              : null;
            const partnerTpl = partnerTplId ? templates.find((t) => t.id === partnerTplId) : null;
            const partnerOffice = partnerTpl
              ? (offices.find((o) => o.id === partnerTpl.office_id)?.name ?? '')
              : '';
            const partnerCourseLabel = partnerTpl
              ? `${partnerOffice ? partnerOffice + '-' : ''}${partnerTpl.label} コース`
              : '';
            const partnerSlotMark = idx === 0 ? '②' : '①';
            partnerLabel = partnerCourseLabel
              ? `${pname} ${partnerSlotMark} (${partnerCourseLabel})`
              : `${pname} ${partnerSlotMark}`;
          }
        } else if (groupVisits.length === 1) {
          // group 内に 1 件しかない異常系 (BE で 2 件作成される前提が崩れた場合)
          groupSlotLabel = 1;
          partnerMissing = requiresMulti;
        }
      } else if (requiresMulti) {
        // 2 名体制患者なのに visit_group_id が無い = slot 1 が未配置の片割れ
        partnerMissing = true;
      }

      const arr = m.get(cid) ?? [];
      arr.push({
        id: v.id,
        patient_id: v.patient_id,
        patient_name: patient?.name ?? v.patient_name ?? null,
        patient_address: patient?.address ?? null,
        patient_requires_multiple_staff: requiresMulti,
        patient_sex_restriction_label: sexLabel,
        // 旧フィールド (互換のため保持). 表示判定からは除外.
        required_staff_count: (v.required_staff_count ?? 1) as 1 | 2,
        start_slot: slot,
        visit_group_id: groupId,
        group_slot_label: groupSlotLabel,
        partner_label: partnerLabel,
        partner_missing: partnerMissing,
      });
      m.set(cid, arr);
    }
    return m;
  }, [weekVisits, patientById, visitsByGroupId, courseTemplateByCourseId, templates, offices]);

  // ─── Wave 37 Phase 3-C: 患者ごとの「配置済み slot」マップ ───────────────
  //   - visit_group_id 持ち visit (= ペア配置済) → slot 0 / slot 1 の両方を埋める
  //   - 単独 visit (visit_group_id=null) → slot 0 のみ埋める
  //   - patient.requires_multiple_staff=true で visit が 1 件だけ + group_id なし
  //     → slot 0 のみ埋まり (slot 1 が未配置 = 「複数 ① のみ」表示の対象)
  // PoolGroupedByWeekday 側 (Phase 3-B) で「①/② どちらが空きか」表示するために使う。
  const assignedSlotsByPatient = useMemo(() => {
    const m = new Map<string, Set<SlotIndex>>();
    for (const v of weekVisits) {
      const gid = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      const set = m.get(v.patient_id) ?? new Set<SlotIndex>();
      if (gid) {
        // visit_group_id 持ち → group 内の 2 visit でそれぞれ slot 0/1 を埋める。
        // FE では visitsByGroupId 内の sort 順 (id 昇順) で 0/1 を割当て。
        const groupVisits = visitsByGroupId.get(gid) ?? [];
        const idx = groupVisits.findIndex((gv) => gv.id === v.id);
        set.add(idx === 1 ? 1 : 0);
      } else {
        // 単独 visit は slot 0 を埋める (2 名体制の片割れ初期配置)。
        set.add(0);
      }
      m.set(v.patient_id, set);
    }
    return m;
  }, [weekVisits, visitsByGroupId]);

  // ─── Pool patients (当週いずれの visit にも未配置な active 患者) ─
  // W37 Phase 3-B/3-C: requires_multiple_staff=true 患者は slot 単位で配置判定する
  // ため、ここでは「visit が 1 件でも存在する」だけで除外せず、PoolGroupedByWeekday
  // 側で assignedSlotsByPatient (slot 0/1) を見て両方埋まっていればカード 0 枚に
  // 丸める。通常患者 (フラグ OFF) は従来どおり 1 件配置で pool から除外。
  const placedPatientIds = useMemo(() => {
    const s = new Set<string>();
    const multiStaffIds = new Set(
      allPatients
        .filter((p) => (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff)
        .map((p) => p.id),
    );
    for (const v of weekVisits) {
      if (v.visit_date && !multiStaffIds.has(v.patient_id)) {
        s.add(v.patient_id);
      }
    }
    return s;
  }, [weekVisits, allPatients]);

  const poolPatients = useMemo(
    () =>
      allPatients.filter((p) => p.status === 'active' && !placedPatientIds.has(p.id)).slice(0, 200),
    [allPatients, placedPatientIds],
  );

  // ─── visit lookup (Wave 18 Phase B-5: 配置済みドラッグ用) ──────────
  const visitById = useMemo(() => {
    const m = new Map<string, (typeof weekVisits)[number]>();
    for (const v of weekVisits) m.set(v.id, v);
    return m;
  }, [weekVisits]);

  // ─── Wave 18 Phase B-6: 週間ビュー用 visits (template × weekday に解決) ──
  // courseTemplateByCourseId は上 (visitsByCourse 直前) に移設済み.
  const overviewVisits = useMemo<WeekOverviewVisit[]>(() => {
    const out: WeekOverviewVisit[] = [];
    for (const v of weekVisits) {
      const cid = v.course_id ?? null;
      if (!cid) continue;
      const templateId = courseTemplateByCourseId.get(cid);
      if (!templateId) continue;
      // visit_date → weekday (Mon=0..)
      let wd: number | null = null;
      if (v.visit_date) {
        // visit_date は 'YYYY-MM-DD' 文字列。ローカル時刻として解釈するため明示。
        const d = new Date(v.visit_date + 'T00:00:00');
        wd = (d.getDay() + 6) % 7;
      } else {
        // course_id 経由で逆引き
        const c = courses.find((cc) => cc.id === cid);
        wd = c?.weekday ?? null;
      }
      if (wd == null || wd < 0 || wd > 5) continue;
      const patient = patientById.get(v.patient_id);
      out.push({
        id: v.id,
        patient_id: v.patient_id,
        patient_name: patient?.name ?? v.patient_name ?? null,
        weekday: wd,
        course_template_id: templateId,
        start_time: v.start_time ?? null,
      });
    }
    return out;
  }, [weekVisits, courseTemplateByCourseId, courses, patientById]);

  const officeNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices) m.set(o.id, o.name);
    return m;
  }, [offices]);

  // ─── Wave 32: staffId → StaffRead マップ (CourseWeekOverview 担当名表示用) ──
  const staffMap = useMemo(() => {
    const m = new Map<string, (typeof allStaff)[number]>();
    for (const s of allStaff) m.set(s.id, s);
    return m;
  }, [allStaff]);

  // ─── Wave 28 Phase B-3: (template_id, weekday) → assigned_staff_id マップ ──
  // CourseWeekOverview で担当スタッフ別 event を表示するために必要。
  const assignedStaffByTemplateWeekday = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) {
      if (!c.assigned_staff_id) continue;
      const tpl = templates.find(
        (t) =>
          t.office_id === c.office_id &&
          (t.label || '').trim().slice(0, 1).toUpperCase() === String(c.code).toUpperCase(),
      );
      if (tpl) {
        m.set(`${tpl.id}:${c.weekday}`, c.assigned_staff_id);
      }
    }
    return m;
  }, [courses, templates]);

  // ─── DnD ──────────────────────────────────────────────────────────
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 5 } }),
  );
  const [activePatientId, setActivePatientId] = useState<string | null>(null);
  const [activeVisitId, setActiveVisitId] = useState<string | null>(null);
  const placeAndFixMut = usePlaceAndFix();
  const deleteVisitMut = useDeleteVisit();

  // ─── Wave 37 Phase 3-C: 相方コース選択ダイアログの state ───────────────
  // requires_multiple_staff=true の患者を D&D したときにダイアログを表示する。
  // 確定後に staff_count=2 + course_template_ids: [primary, secondary] で
  // place-and-fix を呼ぶ。キャンセル時は何もしない (drop 取り消し)。
  const [partnerDialogState, setPartnerDialogState] = useState<{
    open: boolean;
    patientId: string;
    primaryTemplate: CourseTemplateRead;
    candidateTemplates: CourseTemplateRead[];
    primaryOfficeName: string;
    weekday: number;
    time: string;
    durationMin: number;
    contextLabel: string;
  } | null>(null);

  const closePartnerDialog = () => {
    setPartnerDialogState((prev) => (prev ? { ...prev, open: false } : null));
    // 完全クリアは少し遅延 (animation 終了後)。実害は無いため即座でも可。
    setTimeout(() => setPartnerDialogState(null), 200);
  };

  const handleDragStart = (e: DragStartEvent) => {
    const id = String(e.active.id);
    setActivePatientId(parsePatientDraggableId(id));
    setActiveVisitId(parseVisitDraggableId(id));
  };

  /**
   * Wave 18 Phase B-5:
   *   - pool-patient → cell:   既存の place-and-fix を呼ぶ。
   *   - visit → cell:           移動 = delete + place-and-fix の 2 段階で代替実装
   *                             (atomic 化は Wave 19 BE PATCH で対応)。
   *   - visit → pool:           visit を delete (= プールに戻る)。
   */
  const handleDragEnd = async (e: DragEndEvent) => {
    setActivePatientId(null);
    setActiveVisitId(null);
    const { active, over } = e;
    if (!over) return;
    const activeId = String(active.id);
    const overId = String(over.id);

    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }

    const patientId = parsePatientDraggableId(activeId);
    const visitId = parseVisitDraggableId(activeId);
    const cell = parseCourseDayCellId(overId);
    const isPoolDrop = overId === POOL_DROPPABLE_ID;

    // ─── プール患者 → セル ─────────────────────────────────────────
    // Wave 37 Phase 3-C: patient.requires_multiple_staff=true なら相方コース
    //   選択ダイアログを開き、確定後に staff_count=2 で place-and-fix を呼ぶ。
    //   従来通常患者 (false) は staff_count=1 + course_template_id (旧形式) で呼ぶ。
    if (patientId && cell) {
      const patient = patientById.get(patientId);
      const wp = (patient?.weekly_pattern ?? null) as { service_minutes?: number } | null;
      const durationMin = Math.max(1, Number(wp?.service_minutes ?? 60));
      const requiresMulti =
        (patient as { requires_multiple_staff?: boolean | null } | undefined)
          ?.requires_multiple_staff === true;

      if (requiresMulti) {
        // 2 名体制: 相方コース選択ダイアログを表示
        const primaryTemplate = templates.find((t) => t.id === cell.courseTemplateId);
        if (!primaryTemplate) {
          toast.error('drop 先のコーステンプレートが見つかりません');
          return;
        }
        // 候補: 同じ office_id + 当該 weekday に capacity > 0 + primary を除外
        const candidates = templates.filter(
          (t) =>
            t.id !== primaryTemplate.id &&
            t.office_id === primaryTemplate.office_id &&
            capacityForWeekday(t, cell.weekday) > 0,
        );
        const officeName = offices.find((o) => o.id === primaryTemplate.office_id)?.name ?? '';
        const wdLabel = ['月', '火', '水', '木', '金', '土', '日'][cell.weekday] ?? '';
        setPartnerDialogState({
          open: true,
          patientId,
          primaryTemplate,
          candidateTemplates: candidates,
          primaryOfficeName: officeName,
          weekday: cell.weekday,
          time: cell.time,
          durationMin,
          contextLabel: `${wdLabel} ${cell.time}`,
        });
        return;
      }

      // 通常患者 (1 名体制): 従来挙動 = staff_count=1 + course_template_id (旧形式)
      try {
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
        toast.error(`配置に失敗しました: ${formatErr(err)}`);
      }
      return;
    }

    // ─── 配置済み visit ドラッグ ─────────────────────────────────
    if (visitId) {
      const v = visitById.get(visitId);
      if (!v) return;

      // Wave 37 Phase 3-C / W37 hotfix M-1: visit_group_id 持ち visit (= 2 名体制ペア) の
      // D&D 操作 (セル間 move + プール戻し両方) は禁止. プール戻しは BE
      // useDeleteVisit の default cascade_partner=true でペア両方が削除されてしまい、
      // 意図せず 2 visit を消すため. partner との連動移動は将来 Wave 対応.
      // 本フェーズは × ボタン削除 → 再配置の手順をユーザに案内する.
      const visitGroupId = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      if (visitGroupId) {
        toast.warning(
          '2 名体制 (ペア配置済) の visit はプールへ戻せません / 別セルへ移動できません。× ボタンで一括削除してから再配置してください。',
        );
        return;
      }

      // visit → プール: delete のみ (cascade=false; 固定枠は保持)
      if (isPoolDrop) {
        try {
          await deleteVisitMut.mutateAsync({ id: visitId, cascadeFixedVisit: false });
          toast.success(`${v.patient_name ?? v.patient_id} をプールに戻しました`);
        } catch (err) {
          toast.error(`プールへの戻しに失敗しました: ${formatErr(err)}`);
        }
        return;
      }

      // visit → セル: 移動 (delete + place-and-fix). 同一セルへの移動は noop。
      if (cell) {
        // 同一 weekday + 同一 slot の場合は noop (course_template_id は visit に
        // 無いので簡易判定。誤検知低リスク。厳密判定は Wave 19 で BE PATCH 化時に)。
        const visitWeekday = v.visit_date
          ? // visit_date (yyyy-MM-dd) → weekStart からのオフセット (0=Mon..)
            // T00:00:00 を付与してローカル時刻として解釈し、UTCとの境界ズレを防ぐ。
            (() => {
              const d = new Date(v.visit_date + 'T00:00:00');
              const dow = (d.getDay() + 6) % 7; // Mon=0
              return dow;
            })()
          : null;
        const sameSlot = v.start_time != null && floorToCourseSlot(v.start_time) === cell.time;
        // TODO(Wave 19): noop 判定に course_template_id を含めて、同一曜日・同一時刻でも
        // 異なるコース間のドロップは move として扱う。現状 (Wave 18) は delete+place-and-fix
        // の 2 段階のため、course_template_id を含めると不要な delete が走る可能性があり、
        // PATCH /api/v1/visits/{id} (atomic 化) と組合わせて Wave 19 で対応予定。
        if (sameSlot && visitWeekday === cell.weekday) {
          return;
        }
        const patient = patientById.get(v.patient_id);
        const wp = (patient?.weekly_pattern ?? null) as { service_minutes?: number } | null;
        const durationMin = Math.max(1, Number(wp?.service_minutes ?? 60));

        // Wave 18 Codex-fix 中-1 + 重大-2: 中間失敗時の rollback リカバリー.
        // 元位置 (旧 visit の weekday + start_time) を退避しておき、step 2 失敗時に
        // 元セルへ place-and-fix し直す (= delete されたユーザーデータの復元試行).
        const originalWeekday = visitWeekday;
        const originalStartTime = v.start_time != null ? floorToCourseSlot(v.start_time) : null;
        // 元 visit の course_template_id は visit に無いので、courseTemplateByCourseId
        // から逆引き。逆引きできない (course_id 無し) ケースでは復元を試みず
        // ユーザーに手動再配置を促す。
        const originalCourseTemplateId = v.course_id
          ? (courseTemplateByCourseId.get(v.course_id) ?? null)
          : null;

        try {
          // 1) 既存 visit を削除 (重大-2: cascade=true で旧曜日の固定枠も削除)
          await deleteVisitMut.mutateAsync({ id: visitId, cascadeFixedVisit: true });
          // 2) 新セルに place-and-fix
          try {
            await placeAndFixMut.mutateAsync({
              patient_id: v.patient_id,
              course_template_id: cell.courseTemplateId,
              iso_year: isoYear,
              iso_week: isoWeek,
              weekday: cell.weekday,
              start_time: cell.time,
              duration_min: durationMin,
              staff_count: 1,
              fix_pattern: true,
            });
            toast.success(`${patient?.name ?? v.patient_id} を ${cell.time} に移動しました`);
          } catch (e2) {
            // step 2 失敗 → 元セルへの復元を試みる (中-1 リカバリー)
            if (originalCourseTemplateId && originalWeekday != null && originalStartTime) {
              try {
                await placeAndFixMut.mutateAsync({
                  patient_id: v.patient_id,
                  course_template_id: originalCourseTemplateId,
                  iso_year: isoYear,
                  iso_week: isoWeek,
                  weekday: originalWeekday,
                  start_time: originalStartTime,
                  duration_min: durationMin,
                  staff_count: 1,
                  fix_pattern: true,
                });
                toast.warning(
                  `移動先で失敗、元の位置 (${originalStartTime}) に復元しました: ${formatErr(e2)}`,
                );
              } catch (e3) {
                toast.error(`移動も復元も失敗しました。手動で再配置してください: ${formatErr(e3)}`);
              }
            } else {
              toast.error(
                `移動に失敗しました。元位置情報が取得できないため復元できません。手動で再配置してください: ${formatErr(e2)}`,
              );
            }
          }
        } catch (e1) {
          // step 1 (delete) 失敗 → ユーザーデータは無傷
          toast.error(`元 visit の削除に失敗しました: ${formatErr(e1)}`);
        }
        return;
      }
    }
  };

  // ─── Wave 37 Phase 3-C: 相方コース確定ハンドラ ─────────────────────
  // ダイアログで 2 つ目の course_template_id を確定したら、staff_count=2 で
  // place-and-fix を呼び出す。BE Phase 2-A が 2 visit を visit_group_id 共有で作成。
  const handlePartnerConfirm = async (secondaryTemplateId: string) => {
    const ds = partnerDialogState;
    if (!ds) return;
    closePartnerDialog();
    const patient = patientById.get(ds.patientId);
    try {
      await placeAndFixMut.mutateAsync({
        patient_id: ds.patientId,
        // Wave 37 Phase 3-C: 新形式 (course_template_ids) を使う。
        // course_template_id (旧) は省略 (両方指定すると Zod superRefine でエラー).
        course_template_ids: [ds.primaryTemplate.id, secondaryTemplateId],
        iso_year: isoYear,
        iso_week: isoWeek,
        weekday: ds.weekday,
        start_time: ds.time,
        duration_min: ds.durationMin,
        staff_count: 2,
        fix_pattern: true,
      });
      toast.success(`${patient?.name ?? ds.patientId} を ${ds.time} に 2 名体制で固定枠化しました`);
    } catch (err) {
      toast.error(`2 名体制配置に失敗しました: ${formatErr(err)}`);
    }
  };

  // ─── Wave 36: visit × ボタン削除ハンドラ ────────────────────────
  const handleDeleteVisit = async (visitId: string, patientName: string) => {
    if (!window.confirm(`${patientName} の訪問を削除しますか？\n(固定枠は保持されます)`)) return;
    try {
      await deleteVisitMut.mutateAsync({ id: visitId, cascadeFixedVisit: false });
      toast.success(`${patientName} の訪問を削除しました`);
    } catch (err) {
      toast.error(`削除に失敗: ${formatErr(err)}`);
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
                const selected = activeTab === wd;
                return (
                  <button
                    key={wd}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    aria-controls={`course-day-panel-${wd}`}
                    onClick={() => setActiveTab(wd)}
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
              {/* Wave 18 Phase B-6: 「週」タブ */}
              <button
                key="week"
                type="button"
                role="tab"
                aria-selected={activeTab === 'week'}
                aria-controls="course-week-overview-panel"
                onClick={() => setActiveTab('week')}
                data-testid="course-day-tab-week"
                className={`rounded border px-3 py-1 text-xs font-semibold ${
                  activeTab === 'week'
                    ? 'border-brand-primary bg-brand-primary text-white'
                    : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted'
                }`}
              >
                週
              </button>
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

        {/* Wave 19: 2 ペイン レイアウト — メイン (1fr) + プール (320px固定 sticky) */}
        <div
          className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_320px]"
          data-testid="course-day-two-pane"
        >
          {/* 左ペイン: コーステーブル群 */}
          <div className="space-y-3 min-w-0">
            {/* Wave 18 Phase B-6: 「週」タブ選択時は CourseWeekOverview を表示 */}
            {activeTab === 'week' ? (
              <div
                id="course-week-overview-panel"
                role="tabpanel"
                aria-labelledby="course-day-tab-week"
                data-testid="course-week-overview-panel"
              >
                <CourseWeekOverview
                  templates={templates}
                  officeNameById={officeNameById}
                  visits={overviewVisits}
                  onJumpToDay={(wd) => setActiveTab(wd)}
                  staffEventsByStaff={staffEventsByStaff}
                  assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
                  staffMap={staffMap}
                />
              </div>
            ) : (
              /* メイン: 当該曜日のコーステーブル N 個 */
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
                        staffEventsByStaff={staffEventsByStaff}
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
                        onDeleteVisit={(visitId, patientName) => {
                          void handleDeleteVisit(visitId, patientName);
                        }}
                      />
                    );
                  })
                )}
              </div>
            )}

            {/* 受入目安レイヤー凡例 (任意) */}
            {showAcceptanceLayer ? <AcceptanceLegend /> : null}
          </div>

          {/* 右ペイン: 保留プール (sticky で追従) */}
          <aside
            className="sticky top-4 self-start max-h-[calc(100vh-2rem)] overflow-y-auto"
            data-testid="course-day-pool-pane"
            // Wave 37 Phase 3-C: 配置済み slot マップを serialize してテスト・debug 用に露出.
            // 形式: "patientId:slot,...,patientId:slot"
            // Phase 3-B が PoolGroupedByWeekday に assignedSlotsByPatient prop を
            // 追加し次第、ここで { p.id → Set } マップを直接 prop で渡すように切替える。
            data-assigned-slots={Array.from(assignedSlotsByPatient.entries())
              .flatMap(([pid, slots]) => Array.from(slots).map((s) => `${pid}:${s}`))
              .sort()
              .join(',')}
          >
            {/*
              保留プール
              - Wave 18 Phase B-3: 希望曜日別グループ
              - Wave 18 Phase B-4: 希望時間表示
              - W37 Phase 3-B + 3-C: 複数スタッフ対応患者は assignedSlotsByPatient
                に応じて slot 0 / slot 1 の片方のみ未配置のカードを残す。
                draggableId に slot を含む。
            */}
            <PoolGroupedByWeekday
              patients={poolPatients}
              disabled={!canEdit}
              assignedSlotsByPatient={assignedSlotsByPatient}
              renderCard={(p, slotInfo) => {
                const wp = coerceWeeklyPattern(p.weekly_pattern);
                return (
                  <PatientCard
                    draggableId={buildPoolDraggableId(p.id, slotInfo.slotIndex)}
                    patient={{
                      id: p.id,
                      name: p.name,
                      caption: p.kana ?? undefined,
                      preferredTimeLabel: formatPreferredTimeLabel(wp),
                      serviceMinutes: wp.service_minutes ?? undefined,
                      sexRestriction:
                        (p.sex_restriction as 'female_only' | 'male_only' | null | undefined) ??
                        null,
                      requiresMultipleStaff:
                        (p as { requires_multiple_staff?: boolean | null })
                          .requires_multiple_staff ?? null,
                      patientStatus: p.status ?? null,
                      slotIndex: slotInfo.slotIndex,
                      partnerAssigned: slotInfo.partnerAssigned,
                    }}
                    disabled={!canEdit}
                  />
                );
              }}
            />
          </aside>
        </div>

        <DragOverlay>
          {activePatientId ? (
            <div className="rounded border border-brand-primary bg-brand-primary/10 px-2 py-1 text-xs shadow-lg">
              {patientById.get(activePatientId)?.name ?? activePatientId}
            </div>
          ) : null}
          {activeVisitId
            ? (() => {
                const v = visitById.get(activeVisitId);
                const name = v
                  ? (patientById.get(v.patient_id)?.name ?? v.patient_name ?? v.patient_id)
                  : activeVisitId;
                return (
                  <div className="rounded border border-warning bg-warning/10 px-2 py-1 text-xs shadow-lg">
                    {name} <span className="text-text-muted">(移動)</span>
                  </div>
                );
              })()
            : null}
        </DragOverlay>

        {/* Wave 37 Phase 3-C: 相方コース選択ダイアログ */}
        {partnerDialogState ? (
          <PartnerCourseDialog
            open={partnerDialogState.open}
            primaryTemplate={partnerDialogState.primaryTemplate}
            candidateTemplates={partnerDialogState.candidateTemplates}
            primaryOfficeName={partnerDialogState.primaryOfficeName}
            contextLabel={partnerDialogState.contextLabel}
            onConfirm={(secondaryId) => void handlePartnerConfirm(secondaryId)}
            onCancel={() => closePartnerDialog()}
          />
        ) : null}
      </section>
    </DndContext>
  );
}
