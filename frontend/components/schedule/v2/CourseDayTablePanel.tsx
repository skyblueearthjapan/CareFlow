'use client';

/**
 * CourseDayTablePanel — Wave 17 Phase B-2 メインパネル.
 *
 * Excel スケジュール枠組みに完全準拠した 1 画面構造
 * (Phase G-43 で Row 1 を flex justify-end 単一 toolbar 化し、主要 4 と固定枠戻を隣接させた):
 *   ┌─ ヘッダー ────────────────────────────────────────────┐
 *   │  Row 1 (右寄せ 1 行 toolbar, admin/manager only):                              │
 *   │     [週を生成][自動スタッフ割付 🟢][全面最適化 🟢][プール投入 🟢] │ [固定枠戻][全件保存] │
 *   │  ─── border-t ────────────────────                                            │
 *   │  Row 2 (曜日タブ + テーブル/リスト + 二次操作):                                  │
 *   │    [月][火][水][木][金][土][週] YYYY-Www                                       │
 *   │    [テーブル | リスト] │ [一斉未割当] │ [🔒][🔓]                                  │
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
 *   - Phase G-41: 「週を生成」「自動スタッフ割付」「全面最適化」「プール投入」 を Row 1 (右寄せ) に再収容.
 *     mutation pending 状態は内部で算出し、二次操作 (固定枠戻 / 一斉未割当) を多重実行から保護する.
 *   - 各コーステーブルの担当 dropdown で PATCH /api/v1/courses/{id}
 *   - プールセル → コーステーブル行ドロップ → place-and-fix
 *
 * RBAC:
 *   - admin / manager: 編集可 (ドロップ + 担当変更 + 主要 4 + 二次操作 + 個別 reset)
 *   - staff: 閲覧のみ
 */
import { useCallback, useMemo, useState } from 'react';
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
import { FlaskConical, HeartPulse, Loader2, Plus, RefreshCw, UserCheck, UserX } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { addDays } from '@/components/schedule/WeekSelector';
import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  useAssignStaffOnly,
  useApplyStaffReview,
  type ReviewItem,
} from '@/lib/queries/assign_staff_only';
import { useCourses, useUpdateCourse, type CourseV2Read } from '@/lib/queries/courses';
import { useGenerateWeekOnly } from '@/lib/queries/generate_week';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useStaffList } from '@/lib/queries/staff';
import {
  buildStaffEventsMap,
  useUpdateEventForDrag,
  useWeekStaffEvents,
} from '@/lib/queries/staff-events';
import type { EventRead } from '@/lib/schemas/staff-events';
import { useDeleteVisit, useVisits } from '@/lib/queries/visits';
import { useBulkPinPfvs, useTogglePfvPin } from '@/lib/queries/g21';
// Phase G-47: PinScope 型 (= 個別 🔒 toggle のスコープ '曜日のみ' / '全曜日').
import type { PinScope } from './PinScopeMenu';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';
import { effectiveCapacity, type CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import { useWeekdayStaffCapacityLookup } from '@/lib/queries/weekday_staff_capacity';
import {
  SEX_RESTRICTION_LABEL,
  coerceWeeklyPattern,
  formatPreferredTimeLabel,
  normalizePatientSexRestriction,
  type PatientRead,
} from '@/lib/schemas/patient';

import {
  WeekdayScheduleCard,
  buildSameAddressKey,
  haversineKm,
  type CourseListItem as ScheduleCourseListItem,
  type VisitListItem as ScheduleVisitListItem,
} from '../WeekdayScheduleCard';
import { AcceptanceLegend } from './AcceptanceLayer';
import { BulkFixToPatternButton } from './BulkFixToPatternButton';
import { BulkPinAllPfvsButton } from './BulkPinAllPfvsButton';
import { DiffAddDialog } from './DiffAddDialog';
import { AssignWarningDialog, type ApprovedReviewItem } from './AssignWarningDialog';
import { FullOptimizeDialog } from './FullOptimizeDialog';
import { StaffSubstituteDialog } from './StaffSubstituteDialog';
import { ProposeNewModal } from './ProposeNewModal';
import { ScheduleHealthDialog } from './ScheduleHealthDialog';
import { ResetToFixedButton } from './ResetToFixedButton';
import { UnassignAllStaffButton } from './UnassignAllStaffButton';
import {
  CourseDayTable,
  floorToCourseSlot,
  parseCourseDayCellId,
  parseEventDraggableId,
  parseVisitDraggableId,
  toMinutes,
  type CourseGridVisit,
  type PartnerLocation,
} from './CourseDayTable';
import { CourseWeekOverview, type WeekOverviewVisit } from './CourseWeekOverview';
import { PartnerCourseDialog } from './PartnerCourseDialog';
import { PatientCard } from './PatientCard';
import { PatientScheduleDetailDialog } from './PatientScheduleDetailDialog';
import {
  POOL_DROPPABLE_ID,
  PoolGroupedByWeekday,
  buildPoolDraggableId,
  parsePoolDraggableId,
} from './PoolPanel';
import type { SlotIndex } from '@/lib/schemas/v2/patient_fixed_visit';
// Phase G-44: 「希望訪問パターン」 vs 「実 visit 数」 の共通 utility.
import { countWeekVisits, getDesiredWeeklyVisitCount } from '@/lib/scheduling/preferred-visits';
// Phase G-55: 空き時間帯 (≥60分) 算出の共有 util (mobile FieldBoard と共通).
import {
  computeFreeGaps,
  businessBlocksFromHours,
  BUSINESS_BLOCKS,
  type FreeGap,
} from '@/lib/scheduling/freeGaps';
// Phase G-88: 営業時間設定を空き枠表示に反映 (取得前/失敗時は既定枠にフォールバック).
import { useSchedulingSettings } from '@/lib/queries/schedulingSettings';

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

/** Wave 39: 通算分 (= H*60+M) → "HH:MM". 範囲外チェックはしない (clamp は呼出側). */
function formatHHMM(totalMinutes: number): string {
  const m = Math.max(0, totalMinutes);
  const hh = Math.floor(m / 60);
  const mm = m % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
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
 *
 * 一致条件 (CareFlow Wave Next 2 [H1] 対応):
 *   1. exact match: template.label (大文字) === course.code (大文字)
 *      → 例: label='A' & code='A', label='M' & code='M', label='M2' & code='M2'
 *   2. M overflow fallback: course.code が M-prefix (M2..M9) かつ
 *      template.label === 'M' の場合 (専用 M2/M3 template が同 office に
 *      seed されていなくても 'M' template に表示できるようにする).
 *   3. legacy 1-char match: code が 1 文字 (A-E, M) で template.label の
 *      先頭 1 文字と一致 (旧運用との後方互換: label='Aコース' → code='A').
 */
export function findCourseForTemplate(args: {
  template: CourseTemplateRead;
  weekday: number;
  isoYear: number;
  isoWeek: number;
  courses: CourseV2Read[];
}): CourseV2Read | null {
  const { template, weekday, isoYear, isoWeek, courses } = args;
  const labelUp = (template.label || '').trim().toUpperCase();
  const labelFirst = labelUp.slice(0, 1);
  const found = courses.find((c) => {
    if (c.office_id !== template.office_id) return false;
    if (c.iso_year !== isoYear || c.iso_week !== isoWeek || c.weekday !== weekday) return false;
    if (c.deleted_at) return false;
    const codeUp = String(c.code).toUpperCase();
    // 1) exact match
    if (codeUp === labelUp) return true;
    // 2) M overflow fallback: code='M2'/'M3' などで template.label='M' に流す.
    //    (M2/M3 専用 template があるならそちらが exact match で先に拾われる.)
    if (labelUp === 'M' && /^M\d+$/.test(codeUp)) {
      return true;
    }
    // 3) legacy 1-char fallback (label='Aコース' → code='A' 等)
    if (codeUp.length === 1 && codeUp === labelFirst) return true;
    return false;
  });
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
  // デフォルトは週ビュー ('week'). 曜日別 (月-土) は各タブで切替.
  const [activeTab, setActiveTab] = useState<number | 'week'>('week');
  const activeWeekday = typeof activeTab === 'number' ? activeTab : 0;

  // ─── 2026-W20: 月-土タブの表示モード ─────────────────────────
  // table = Excel 形式時刻グリッド (既存挙動 / DnD 編集可能).
  // list  = Before/After 形式 (時刻順 visit リスト / 視覚言語統一・閲覧専用).
  const [weekdayViewMode, setWeekdayViewMode] = useState<'table' | 'list'>('table');

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

  // ─── Phase G-21 T4 reviewer C2: visit ↔ PFV 逆引き ──────────────
  // BE visits API は現状 `fixed_visit_id` / `is_pinned` を返さない. FE 側で
  // 当週 visit に出現する患者群の PFV を per-patient `useQueries` で並列 fetch
  // し、 `(patient_id, weekday, start_time, slot_index)` で join して
  // `pfvByVisitKey` を構築する. visitsByCourse builder 側でこの map を引いて
  // `CourseGridVisit.fixed_visit_id` / `is_pinned` を populate する.
  //
  // 並列 fetch 件数は当週出現患者の uniq 数 (= 通常 30 〜 80 件). TanStack Query
  // が同 queryKey で重複 fetch を抑止するため、 PatientFixedVisitsPanel と同じ
  // patient で開いていれば cache hit する.
  const pfvPatientIds = useMemo(() => {
    const s = new Set<string>();
    for (const v of weekVisits) s.add(v.patient_id);
    return Array.from(s).sort();
  }, [weekVisits]);

  const pfvQueries = useQueries({
    queries: pfvPatientIds.map((pid) => ({
      queryKey: ['patients', pid, 'fixed-visits', 'all'] as const,
      enabled: sessionStatus === 'authenticated' && Boolean(pid),
      queryFn: () =>
        fetcher<PatientFixedVisitV2Read[]>(`/api/v1/patients/${pid}/fixed-visits`, {
          accessToken,
          refreshToken,
        }),
      // 5 分 cache (= PFV は頻繁に変わらない / pin toggle 時は invalidate される).
      staleTime: 5 * 60 * 1000,
    })),
  });

  // `(patient_id, weekday, "HH:MM", slot_index) → PFV` のフラット lookup.
  // queries 配列は参照不安定なので dataUpdatedAt + status 派生キーで stable dep.
  const pfvQueriesDepKey = pfvQueries.map((q) => `${q.dataUpdatedAt}:${q.status}`).join(',');
  const pfvByVisitKey = useMemo(() => {
    const m = new Map<string, PatientFixedVisitV2Read>();
    pfvPatientIds.forEach((pid, i) => {
      const list = pfvQueries[i]?.data ?? [];
      for (const pfv of list) {
        // 当週は通常 mode のみ join 対象 (special week 切替は別 layer の責務).
        if (pfv.mode !== 'normal') continue;
        const hhmm = (pfv.start_time ?? '').slice(0, 5);
        const slot = pfv.slot_index ?? 0;
        m.set(`${pid}:${pfv.weekday}:${hhmm}:${slot}`, pfv);
      }
    });
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pfvPatientIds.join(','), pfvQueriesDepKey]);

  // Phase G-21 T4 reviewer C2: 単一 PFV pin toggle hook (panel 全体で 1 instance).
  const togglePfvPin = useTogglePfvPin();
  // Phase G-47: 「全曜日」 スコープ選択時の bulk hook (panel 全体で 1 instance).
  const bulkPinPfvs = useBulkPinPfvs();

  // ─── Courses (当週: course_template の逆引き / 担当 dropdown 用) ──
  const coursesQuery = useCourses({ iso_year: isoYear, iso_week: isoWeek, limit: 200 });
  const courses = useMemo(() => coursesQuery.data ?? [], [coursesQuery.data]);

  // ─── Phase G-88: 営業時間設定 → 空き枠 (取得前/失敗時は既定枠にフォールバック) ──
  const schedulingSettingsQuery = useSchedulingSettings();
  const businessBlocks = useMemo(() => {
    const v = schedulingSettingsQuery.data?.values;
    return businessBlocksFromHours(v?.business_start, v?.business_end) ?? BUSINESS_BLOCKS;
  }, [schedulingSettingsQuery.data]);

  // ─── スタッフ数連動の有効定員 (週ビューのコース「休」/定員を auto-schedule と統一) ──
  // (office_id, weekday) → 稼働スタッフ数. A-E コースは effectiveCapacity で
  // index < min(staffCount, courseCodesMax) なら開講 (定員6), else 休 (定員0).
  // M系は従来の静的 capacity_<曜日> を使う (effectiveCapacity が両方扱う).
  const { staffCountFor, managerCountFor, courseCodesMax } = useWeekdayStaffCapacityLookup({
    iso_year: isoYear,
    iso_week: isoWeek,
    office_id: officeId,
  });

  // Phase G-25: 担当 dropdown は全拠点解放 (= 拠点を超えて配置可能).
  // 自動算出 (run_v2_pipeline) は引き続き拠点内のみだが、 手動 dropdown は全 active staff を表示.
  // 各 option には 「氏名 (拠点名)」 形式で所属を併記 (= CourseDayTable 側で format).
  const staffByOffice = useMemo(() => {
    const allActive = [...allStaff]
      .filter((s) => s.status === 'active')
      .sort((a, b) => {
        // 主担当拠点 → kana/氏名 でソート
        const oa = a.primary_office_id ?? '';
        const ob = b.primary_office_id ?? '';
        if (oa !== ob) return oa.localeCompare(ob);
        return (a.kana ?? a.name).localeCompare(b.kana ?? b.name, 'ja');
      });
    // 全 course_template に同じリストを返す (= office_id で絞らない)
    const m = new Map<string, typeof allStaff>();
    // 既存 callsite が staffByOffice.get(office_id) で取得するため、 全 office_id に対して
    // 同じ list を返す map を構築する.
    const officeIds = new Set<string>();
    for (const s of allStaff) {
      if (s.primary_office_id) officeIds.add(s.primary_office_id);
    }
    for (const o of offices) {
      officeIds.add(o.id);
    }
    for (const oid of officeIds) {
      m.set(oid, allActive);
    }
    return m;
  }, [allStaff, offices]);

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

  /**
   * Wave 39: 全スタッフ events を「event_id → {event, staffId}」にフラット化.
   * D&D で event をドロップした際:
   *   - drop 先 cell の course.assigned_staff_id を「移動先 staff_id」として PATCH
   *   - 元 staff_id (= URL パラメータ) は eventById から逆引きする
   *   - 衝突チェック (案 K) も同 Map を走査して判定する
   */
  const eventById = useMemo(() => {
    const m = new Map<string, { event: EventRead; staffId: string }>();
    for (const [staffId, events] of staffEventsByStaff.entries()) {
      for (const ev of events) {
        m.set(ev.id, { event: ev, staffId });
      }
    }
    return m;
  }, [staffEventsByStaff]);

  // ─── 表示するコース一覧: 「拠点 × テンプレート」を活性曜日 capacity > 0 でフィルタ ──
  const courseTablesForActiveDay = useMemo(() => {
    const list: Array<{
      template: CourseTemplateRead;
      officeName: string;
    }> = [];
    for (const t of templates) {
      // スタッフ数連動: A-E は staff_count で開講判定, M系は静的 capacity.
      const staffCount = staffCountFor(t.office_id, activeWeekday);
      const cap = effectiveCapacity(t, activeWeekday, staffCount, courseCodesMax);
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
  }, [templates, offices, activeWeekday, staffCountFor, courseCodesMax]);

  // ─── Wave 18 Phase B-6 / Wave 37 P3-C: course_id → course_template_id の逆引き ──
  // (元 line 467 から移設: visitsByCourse / partner ラベル解決から参照されるため上に移動)
  //
  // CareFlow Wave Next 2 [H1]: M overflow (M2..M9) を考慮した照合.
  // findCourseForTemplate と同じ規則:
  //   1) exact match (label 大文字 === code 大文字)
  //   2) M overflow fallback (code が M2..M9, label が 'M')
  //   3) legacy 1-char fallback (code 長さ 1 で label 先頭文字一致)
  const courseTemplateByCourseId = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) {
      const codeUp = String(c.code).toUpperCase();
      const tpl =
        // 1) exact label match を最優先
        templates.find(
          (t) => t.office_id === c.office_id && (t.label || '').trim().toUpperCase() === codeUp,
        ) ??
        // 2) M overflow (M2..M9) → 'M' template fallback
        (/^M\d+$/.test(codeUp)
          ? templates.find(
              (t) => t.office_id === c.office_id && (t.label || '').trim().toUpperCase() === 'M',
            )
          : undefined) ??
        // 3) legacy 1-char fallback
        (codeUp.length === 1
          ? templates.find(
              (t) =>
                t.office_id === c.office_id &&
                (t.label || '').trim().slice(0, 1).toUpperCase() === codeUp,
            )
          : undefined);
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

  // ─── Wave 38: 「相方の現在地」マップ ─────────────────────────────────
  //
  // 目的:
  //   - スケジュール側 visit セルに「相方: 本店-B 10:00」を併記する.
  //   - プール側残カードに「① 配置済み: 本店-A 15:00」を併記する.
  //
  // 構築ロジック:
  //   1) visit_group_id 持ち visit (= ペア両方が配置済み) について、
  //      「自身 → 相方の cellLabel + time」の対応を作る.
  //      → partnerLocationByVisit: Map<visit_id, PartnerLocation>
  //      → 相方が visit_group_id 経由で確実に取れるので kind:'cell' のみ.
  //
  //   2) patient.requires_multiple_staff=true で片方のみ配置済みの場合、
  //      pool 残側スロットから見て「相方 = 配置済み slot の cellLabel + time」
  //      を引けるようにする.
  //      → partnerLocationByPatientSlot: Map<`${patientId}:${slotIndex}`, label>
  //      → key の slotIndex は **配置済み側** (= プール側残カードから見ると
  //         partnerSlot, PoolPanel 側で `1 - 残カードslot` で引く).
  //
  // 注意:
  //   - 通常患者 (requires_multiple_staff=false) はこのマップに入れない.
  //   - course/template/office が解決できない (course_id=null 等) はスキップ.
  const courseById = useMemo(() => {
    const m = new Map<string, (typeof courses)[number]>();
    for (const c of courses) m.set(c.id, c);
    return m;
  }, [courses]);

  const officesById = useMemo(() => {
    const m = new Map<string, (typeof offices)[number]>();
    for (const o of offices) m.set(o.id, o);
    return m;
  }, [offices]);

  const templateById = useMemo(() => {
    const m = new Map<string, CourseTemplateRead>();
    for (const t of templates) m.set(t.id, t);
    return m;
  }, [templates]);

  /**
   * 1 visit から「セル位置」(cellLabel + time) を導出する Wave 38 ヘルパー.
   * 例: → `{cellLabel: '本店-A', time: '15:00'}` / 解決不能なら null.
   */
  const resolveCellLocationForVisit = (
    v: (typeof weekVisits)[number],
  ): { cellLabel: string; time: string } | null => {
    const cid = v.course_id ?? null;
    if (!cid) return null;
    const c = courseById.get(cid);
    if (!c) return null;
    const tplId = courseTemplateByCourseId.get(cid);
    const tpl = tplId ? templateById.get(tplId) : null;
    if (!tpl) return null;
    const officeName = officesById.get(tpl.office_id)?.name ?? '';
    const cellLabel = officeName ? `${officeName}-${tpl.label}` : (tpl.label ?? '');
    const startSlot = floorToCourseSlot(v.start_time ?? '');
    if (!startSlot) return null;
    return { cellLabel, time: startSlot };
  };

  /** visit_id → PartnerLocation (= 相方が別セルに配置済み). */
  const partnerLocationByVisit = useMemo(() => {
    const m = new Map<string, PartnerLocation>();
    for (const v of weekVisits) {
      const gid = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      if (!gid) continue;
      const groupVisits = visitsByGroupId.get(gid) ?? [];
      if (groupVisits.length !== 2) continue;
      const partner = groupVisits.find((gv) => gv.id !== v.id);
      if (!partner) continue;
      const loc = resolveCellLocationForVisit(partner);
      if (!loc) continue;
      m.set(v.id, { kind: 'cell', cellLabel: loc.cellLabel, time: loc.time });
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    weekVisits,
    visitsByGroupId,
    courseById,
    templateById,
    officesById,
    courseTemplateByCourseId,
  ]);

  /**
   * `${patientId}:${slotIndex}` → cellLabel+time 文字列マップ.
   * - key の slotIndex は配置済み側 (= プール側残カードから見ると相方).
   * - 用途: PoolGroupedByWeekday に渡し、partnerAssigned=true の残カードに併記する.
   */
  const partnerLocationByPatientSlot = useMemo(() => {
    const m = new Map<string, string>();
    // multi-staff patient で「配置済み visit が 1 件のみ + visit_group_id なし」を抽出.
    // → その visit の cellLabel + time を `${patientId}:${assignedSlot}` で登録.
    // assignedSlotsByPatient の構築ロジック上、visit_group_id なしの単独 visit は
    // 必ず slot 0 を埋める (assignedSlotsByPatient 構築 line ~480 参照).
    const multiStaffPatientIds = new Set(
      allPatients
        .filter((p) => (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff)
        .map((p) => p.id),
    );
    for (const v of weekVisits) {
      if (!multiStaffPatientIds.has(v.patient_id)) continue;
      const gid = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      if (gid) continue; // group 持ちは partnerLocationByVisit 側で処理 (= 両方配置済み)
      // 単独 visit = 片側のみ配置. assignedSlotsByPatient と同様に slot 0 とみなす.
      const loc = resolveCellLocationForVisit(v);
      if (!loc) continue;
      m.set(`${v.patient_id}:0`, `${loc.cellLabel} ${loc.time}`);
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekVisits, allPatients, courseById, templateById, officesById, courseTemplateByCourseId]);

  // Phase G-6: patient_id → 同住所バケット key (lat/lng を 0.001 桁で round).
  // 曜日別テーブル (CourseDayTable) で同住所×同時刻ペア囲み (紫枠) に使う.
  // tolerance 0.001 ≒ 100m. lat/lng なし患者は null.
  const sameAddressKeyByPatientId = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const p of allPatients) {
      const lat = (p as { lat?: number | null }).lat ?? null;
      const lng = (p as { lng?: number | null }).lng ?? null;
      m.set(p.id, buildSameAddressKey(lat, lng));
    }
    return m;
  }, [allPatients]);

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

      // Wave 38: 相方の現在地を導出.
      //  - visit_group_id 持ち → partnerLocationByVisit から cell 情報を引く.
      //  - requires_multiple_staff=true で group なし (orphan = partner pool) → kind:'pool'.
      //  - 通常患者 / multi だが既に上記両方のケースに該当しない → null.
      let partnerLocation: PartnerLocation | null = null;
      if (groupId) {
        partnerLocation = partnerLocationByVisit.get(v.id) ?? null;
      } else if (requiresMulti) {
        partnerLocation = { kind: 'pool' };
      }

      // Phase E-1: 「条件」列に出す time_type / preferred_start / preferred_end
      // (患者リスト / 患者詳細ページと同じ language を曜日別テーブルにも出す).
      const wp = patient?.weekly_pattern as
        | {
            time_type?: string | null;
            preferred_start?: string | null;
            preferred_end?: string | null;
          }
        | null
        | undefined;

      // ─── Phase G-21 T4 reviewer C2: visit → PFV 逆引き ──────────────
      // BE は visit に fixed_visit_id を持たない. 同 (patient_id, weekday, HH:MM)
      // で PFV を探し、 slot は visit_group_id 内 index に合わせる (group なしは 0).
      const visitDate = v.visit_date ? new Date(`${v.visit_date}T00:00:00`) : null;
      // visit_date.getDay(): 0=Sun..6=Sat → PFV.weekday は 0=Mon..6=Sun
      // 変換: (jsDay + 6) % 7 = 0(Mon)..6(Sun)
      const pfvWeekday =
        visitDate !== null && !Number.isNaN(visitDate.getTime())
          ? (visitDate.getDay() + 6) % 7
          : null;
      const visitHHMM = (v.start_time ?? '').slice(0, 5);
      // PFV slot は visit_group 内 index (assignedSlotsByPatient と同じ規則).
      let pfvSlot: 0 | 1 = 0;
      if (groupId) {
        const groupVisits = visitsByGroupId.get(groupId) ?? [];
        const idx = groupVisits.findIndex((gv) => gv.id === v.id);
        pfvSlot = idx === 1 ? 1 : 0;
      }
      let fixedVisitId: string | null = null;
      let isPinned = false;
      if (pfvWeekday !== null && visitHHMM) {
        const pfv = pfvByVisitKey.get(`${v.patient_id}:${pfvWeekday}:${visitHHMM}:${pfvSlot}`);
        if (pfv) {
          fixedVisitId = pfv.id;
          isPinned = pfv.is_pinned === true;
        }
      }
      // BE が将来 visit response に直接 fixed_visit_id / is_pinned を expose した
      // 場合のフォールバック (= 上書きできない PFV lookup より優先).
      const beFixedVisitId = (v as { fixed_visit_id?: string | null }).fixed_visit_id ?? null;
      const beIsPinned = (v as { is_pinned?: boolean | null }).is_pinned ?? null;
      if (beFixedVisitId) fixedVisitId = beFixedVisitId;
      if (beIsPinned !== null) isPinned = beIsPinned === true;

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
        partner_location: partnerLocation,
        patient_time_type: wp?.time_type ?? null,
        patient_preferred_start: wp?.preferred_start ?? null,
        patient_preferred_end: wp?.preferred_end ?? null,
        // Phase G-6: 同住所×同時刻ペア囲み用. 同 start_slot 内に同 key が複数あれば
        // CourseDayTable 側で紫枠装飾を付ける.
        same_address_group_id: sameAddressKeyByPatientId.get(v.patient_id) ?? null,
        // Phase G-21 T4 reviewer C2: PFV lookup 結果 (= null なら 🔒 ボタンは disabled).
        fixed_visit_id: fixedVisitId,
        is_pinned: isPinned,
      });
      m.set(cid, arr);
    }
    return m;
  }, [
    weekVisits,
    patientById,
    visitsByGroupId,
    courseTemplateByCourseId,
    templates,
    offices,
    partnerLocationByVisit,
    sameAddressKeyByPatientId,
    pfvByVisitKey,
  ]);

  // ─── Phase G-55: course_id → 空き時間帯 (≥60分) のマップ ───────────────
  // 親機 (デスクトップ) の日テーブルに「空き時間帯」を出すため、当週 visit の実時刻
  // (start_time/end_time) から営業枠の空きを算出する。mobile FieldBoard と同じ
  // 共有 util (computeFreeGaps) を使い、ロジックを二重化しない。
  // 頭数ゲート (remaining<=0 → 満員) は CourseDayTable 側で capacity を見て行うため、
  // ここでは時間 gap のみを course 単位で持つ。
  const freeGapsByCourse = useMemo(() => {
    const m = new Map<string, FreeGap[]>();
    // course_id → その course の生 visit (start_time/end_time + 同住所キー) を集約。
    // 同住所キーを渡すことで computeFreeGaps が同住所 2 名ペアの 90 分占有を反映する
    // (安永/菅原 16:00 ペア → 空きは 16:35 でなく占有後 17:30 から)。
    const rawByCourse = new Map<
      string,
      Array<{ start_time: string; end_time: string; same_address_key: string | null }>
    >();
    for (const v of weekVisits) {
      const cid = v.course_id ?? null;
      if (!cid) continue;
      const arr = rawByCourse.get(cid) ?? [];
      arr.push({
        start_time: v.start_time ?? '',
        end_time: v.end_time ?? '',
        same_address_key: sameAddressKeyByPatientId.get(v.patient_id) ?? null,
      });
      rawByCourse.set(cid, arr);
    }
    for (const [cid, raw] of rawByCourse.entries()) {
      m.set(cid, computeFreeGaps(raw, businessBlocks));
    }
    return m;
  }, [weekVisits, businessBlocks, sameAddressKeyByPatientId]);

  // ─── Phase G-55: (template_id, weekday) → 空き時間帯 のマップ (週ビュー用) ──
  // freeGapsByCourse は course_id 単位。週ビュー (CourseWeekOverview) は
  // (template, weekday) セル単位で gap を引くため、course → (template_id, weekday)
  // に解決し直す。key 形式は CourseWeekOverview の cellMap と同じ `${tpl.id}:${wd}`。
  const freeGapsByCell = useMemo(() => {
    const m = new Map<string, FreeGap[]>();
    for (const c of courses) {
      const templateId = courseTemplateByCourseId.get(c.id);
      if (!templateId) continue;
      const wd = c.weekday;
      if (wd == null || wd < 0 || wd > 5) continue;
      const gaps = freeGapsByCourse.get(c.id);
      if (gaps && gaps.length > 0) m.set(`${templateId}:${wd}`, gaps);
    }
    return m;
  }, [courses, courseTemplateByCourseId, freeGapsByCourse]);

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

  // ─── Pool patients ────────────────────────────────────────────────
  // Phase G-44: 「希望訪問パターン (= weekly_pattern.frequency_per_week) を
  // Source of Truth」に変更. 二値判定 (= 1 件でも visit があれば除外) ではなく
  // 「希望 N 件 vs 実 X 件」 の数値ベース判定にする.
  //
  //   - 希望未設定 (frequency_per_week=0 / null) → 既存挙動と同じく pool 対象外.
  //   - 不足あり (desired > actual) → pool に残す.
  //   - 過不足なし (actual >= desired) → pool から除外.
  //
  // W37 Phase 3-B/3-C 補完:
  //   - requires_multiple_staff=true 患者は slot 単位で配置判定するため、
  //     ここでは「frequency_per_week 判定」を回避し、PoolGroupedByWeekday 側に
  //     assignedSlotsByPatient (slot 0/1) で両方埋まっていれば 0 枚に丸める判定を
  //     委譲する (= 従来挙動の維持).
  //
  // patientShortageById: 各 patient の不足数 (= 希望 - 配置済) のマップ.
  // PatientCard の不足バッジ表示に直接渡す.
  const patientShortageById = useMemo(() => {
    const m = new Map<string, { desired: number; actual: number; shortage: number }>();
    for (const p of allPatients) {
      const desired = getDesiredWeeklyVisitCount(p);
      if (desired === 0) continue;
      const actual = countWeekVisits(weekVisits, p.id);
      const shortage = Math.max(0, desired - actual);
      m.set(p.id, { desired, actual, shortage });
    }
    return m;
  }, [allPatients, weekVisits]);

  const poolPatients = useMemo(() => {
    return allPatients
      .filter((p) => {
        if (p.status !== 'active') return false;
        const isMultiStaff =
          (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true;
        // 複数体制患者は slot 単位判定 (PoolGroupedByWeekday に委譲). 常に候補.
        if (isMultiStaff) return true;
        const info = patientShortageById.get(p.id);
        // 希望未設定 (frequency_per_week=0) → pool 対象外.
        if (!info) return false;
        // 不足あり (= 希望 > 実) → pool に残す.
        return info.shortage > 0;
      })
      .slice(0, 200);
  }, [allPatients, patientShortageById]);

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
      // Phase G-23 fix: 🔒 toggle 用 PFV id 逆引き
      // visitsByCourse builder と完全同じ key 形式 ({patient_id}:{weekday}:{HH:MM}:{slot})
      // を使う. visitHHMM = start_time の先頭 5 文字, slot は visit_group_id 内 index.
      const visitHHMM = (v.start_time ?? '').slice(0, 5);
      const groupId = (v as { visit_group_id?: string | null }).visit_group_id ?? null;
      let pfvSlot: 0 | 1 = 0;
      if (groupId) {
        const groupVisits = visitsByGroupId.get(groupId) ?? [];
        const idx = groupVisits.findIndex((gv) => gv.id === v.id);
        pfvSlot = idx === 1 ? 1 : 0;
      }
      const pfvHit = visitHHMM
        ? pfvByVisitKey.get(`${v.patient_id}:${wd}:${visitHHMM}:${pfvSlot}`)
        : undefined;
      // BE が将来 fixed_visit_id / is_pinned を直接返した場合のフォールバック
      const beFixedVisitId = (v as { fixed_visit_id?: string | null }).fixed_visit_id ?? null;
      const beIsPinned = (v as { is_pinned?: boolean | null }).is_pinned ?? null;
      out.push({
        id: v.id,
        patient_id: v.patient_id,
        patient_name: patient?.name ?? v.patient_name ?? null,
        weekday: wd,
        course_template_id: templateId,
        start_time: v.start_time ?? null,
        // Phase G-15: 性別制限を週ビューに渡して赤/青色付け
        patient_sex_restriction:
          normalizePatientSexRestriction(patient?.sex_restriction as string | null | undefined) ??
          null,
        // Phase G-23: 週ビュー 🔒 toggle 用
        fixed_visit_id: beFixedVisitId ?? pfvHit?.id ?? null,
        is_pinned: beIsPinned !== null ? beIsPinned === true : pfvHit?.is_pinned === true,
        // 週ビューの距離算出用 (コース合計 + 次までの距離).
        lat: (patient as { lat?: number | null } | undefined)?.lat ?? null,
        lng: (patient as { lng?: number | null } | undefined)?.lng ?? null,
      });
    }
    return out;
  }, [weekVisits, courseTemplateByCourseId, courses, patientById, pfvByVisitKey, visitsByGroupId]);

  const officeNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of offices) m.set(o.id, o.name);
    return m;
  }, [offices]);

  // 距離算出 (1 人目=拠点からの距離) 用の拠点座標 lookup.
  const officeLatLngById = useMemo(() => {
    const m = new Map<string, { lat: number | null; lng: number | null }>();
    for (const o of offices) {
      const lat = (o as { lat?: number | null }).lat ?? null;
      const lng = (o as { lng?: number | null }).lng ?? null;
      m.set(o.id, { lat, lng });
    }
    return m;
  }, [offices]);

  // Phase G-53: 週ビュー曜日ヘッダーの「拠点別 S/M」表示用. (office.code or name)
  // から短縮ラベルを作る (INAGE→稲 / TSUGA→津, それ以外は name 先頭 1 文字).
  // 表示順は offices の並び (= 拠点マスタ順) をそのまま使う.
  const staffSummaryOffices = useMemo(() => {
    const shortLabel = (o: (typeof offices)[number]): string => {
      const code = (o.code ?? '').toUpperCase();
      if (code === 'INAGE') return '稲';
      if (code === 'TSUGA') return '津';
      return (o.name ?? '').slice(0, 1) || code.slice(0, 1) || '?';
    };
    return offices.map((o) => ({ id: o.id, label: shortLabel(o) }));
  }, [offices]);

  // Phase G-6: sameAddressKeyByPatientId は visitsByCourse より前に移動済み.
  // CourseWeekOverview (週ビュー) と CourseDayTable (テーブル表示 同住所×同時刻ペア囲み)
  // の両方で使う.

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
  // Wave 39: D&D で event を移動 (時刻スライド + 担当者変更) するための mutation.
  const updateEventDragMut = useUpdateEventForDrag();

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
    const eventId = parseEventDraggableId(activeId);
    const cell = parseCourseDayCellId(overId);
    const isPoolDrop = overId === POOL_DROPPABLE_ID;

    // ─── Wave 39: スタッフイベント drop (時刻スライド + 担当者変更) ───
    // 案 X (同曜日内のみ) + 案 Q (drop 先 course の assigned_staff_id を新所有者に)
    // + 案 K (衝突時 rollback / 移動禁止) を実装する。
    if (eventId && cell) {
      const eventEntry = eventById.get(eventId);
      if (!eventEntry) {
        toast.error('対象のイベントが見つかりません');
        return;
      }
      const { event: ev, staffId: currentStaffId } = eventEntry;

      // 案 X: drop 先 cell の weekday と event 日付の曜日が一致しない → 拒否
      const evDate = new Date(ev.date + 'T00:00:00');
      const evWeekday = (evDate.getDay() + 6) % 7; // Mon=0
      if (evWeekday !== cell.weekday) {
        toast.warning('別の曜日への移動はできません (同じ曜日内でのみスライド可能)');
        return;
      }

      // 案 Q: drop 先 course の assigned_staff_id を取得
      const targetTemplate = templates.find((t) => t.id === cell.courseTemplateId);
      const targetCourse = targetTemplate
        ? findCourseForTemplate({
            template: targetTemplate,
            weekday: cell.weekday,
            isoYear,
            isoWeek,
            courses,
          })
        : null;
      if (!targetCourse) {
        toast.warning('drop 先のコースが見つかりません (先に「週を生成」してください)');
        return;
      }
      const newStaffId = targetCourse.assigned_staff_id ?? null;
      if (!newStaffId) {
        toast.warning('drop 先コースに担当が未割当です。担当を設定してから移動してください');
        return;
      }

      // duration を維持して新 start/end を計算
      const oldStartMin = toMinutes(ev.start_time);
      const oldEndMin = toMinutes(ev.end_time);
      const newStartMin = toMinutes(cell.time);
      if (oldStartMin == null || oldEndMin == null || newStartMin == null) {
        toast.error('時刻の解析に失敗しました');
        return;
      }
      const durationMin = oldEndMin - oldStartMin;
      const newEndMin = newStartMin + durationMin;
      const newStart = formatHHMM(newStartMin);
      const newEnd = formatHHMM(newEndMin);

      // 案 K: 衝突チェック
      //  - 同 staff (newStaffId) の他 events で時間帯重複 → reject
      //  - 同 staff 担当の visit (= courses で newStaffId が assigned されている
      //    course に紐づく visit) で時間帯重複 → reject
      const newStaffEvents = staffEventsByStaff.get(newStaffId) ?? [];
      const sameDateEvents = newStaffEvents.filter((e) => e.id !== ev.id && e.date === ev.date);
      const hasEventOverlap = sameDateEvents.some((e) => {
        const oS = toMinutes(e.start_time) ?? 0;
        const oE = toMinutes(e.end_time) ?? 0;
        return newStartMin < oE && oS < newEndMin;
      });
      if (hasEventOverlap) {
        toast.warning('移動先のスタッフは同時間帯に他のイベントがあります');
        return;
      }

      // 担当 staff = newStaffId の courses → それらの visits の時間帯と重複チェック
      const newStaffCourseIds = new Set(
        courses.filter((c) => c.assigned_staff_id === newStaffId).map((c) => c.id),
      );
      const hasVisitOverlap = weekVisits.some((v) => {
        if (!v.visit_date || v.visit_date !== ev.date) return false;
        if (!v.course_id || !newStaffCourseIds.has(v.course_id)) return false;
        const vsMin = v.start_time ? toMinutes(v.start_time) : null;
        const veMin = v.end_time ? toMinutes(v.end_time) : null;
        if (vsMin == null || veMin == null) return false;
        return newStartMin < veMin && vsMin < newEndMin;
      });
      if (hasVisitOverlap) {
        toast.warning('移動先のスタッフは同時間帯に訪問予定があります');
        return;
      }

      // PATCH: new_staff_id (担当変更) + start_time/end_time (時刻スライド), date は据置.
      try {
        await updateEventDragMut.mutateAsync({
          staffId: currentStaffId,
          eventId,
          payload: {
            start_time: newStart,
            end_time: newEnd,
            ...(newStaffId !== currentStaffId ? { new_staff_id: newStaffId } : {}),
          },
        });
        toast.success(`イベントを ${newStart} に移動しました`);
      } catch (err) {
        toast.error(`イベントの移動に失敗しました: ${formatErr(err)}`);
      }
      return;
    }

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
        // 候補: 同じ office_id + 当該 weekday に開講 (effectiveCapacity>0) + primary を除外.
        // スタッフ数連動 (auto-schedule 統一): A-E は staff_count, M系は静的 capacity.
        const candidates = templates.filter(
          (t) =>
            t.id !== primaryTemplate.id &&
            t.office_id === primaryTemplate.office_id &&
            effectiveCapacity(
              t,
              cell.weekday,
              staffCountFor(t.office_id, cell.weekday),
              courseCodesMax,
            ) > 0,
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

  // ─── Phase G-40: 「週を生成」 / 「自動スタッフ割付」 / 「全面最適化」 / 「プール投入」 は
  //     page 側 (Card 1) へ移設済. ここでは mutation 中の pending 状態のみ props で受け取る. ───

  // ─── 患者スケジュール詳細ダイアログ (固定枠 vs 今週 + 個別反映) ───
  const [patientDetailId, setPatientDetailId] = useState<string | null>(null);
  // プール由来クリックか否か. true のとき詳細ダイアログ内に「プール投入の提案」
  // セクションを表示する (テーブル/週ビュー由来の通常クリックでは出さない =
  // 配置済み患者で重い diff-add を毎回走らせないため).
  const [patientDetailPoolMode, setPatientDetailPoolMode] = useState(false);
  // Wave Next 1 H5: useMemo は値メモ化用途. ハンドラは useCallback が正解.
  const handleOpenPatientDetail = useCallback((pid: string) => {
    setPatientDetailPoolMode(false);
    setPatientDetailId(pid);
  }, []);
  // 保留プールの患者カードクリック専用. プール投入提案セクションを有効化して開く.
  const handleOpenPoolPatientDetail = useCallback((pid: string) => {
    setPatientDetailPoolMode(true);
    setPatientDetailId(pid);
  }, []);
  const handleClosePatientDetail = useCallback(() => {
    setPatientDetailId(null);
    setPatientDetailPoolMode(false);
  }, []);

  // ─── Phase G-21 T4 reviewer C2: 🔒 完全固定 toggle handler ────────────
  // CourseDayTable / CourseWeekOverview / WeekdayScheduleCard から
  // (pfvId, nextPinned, scope, patientId) で呼ばれる.
  //
  // Phase G-47: PinScopeMenu で「曜日のみ / 全曜日」が選択可能になったので、
  //   scope に応じて単一 PATCH か bulk POST を振り分ける.
  //     - scope='day'      → 当該 PFV のみ反転 (= 既存挙動と完全同等).
  //     - scope='all-days' → 当該患者の全 PFV (= pfvByPatientId map から取得) を
  //                           bulk POST. 既に target 状態の PFV は payload 除外.
  //
  //   反映先:
  //     - React Query invalidate (patients / courses / visits) で全 UI 再 fetch.
  //     - これにより 患者マスタ / 各曜日テーブル / 週ビュー / リスト表示 が同期する.
  const handleTogglePin = useCallback(
    (pfvId: string, nextPinned: boolean, scope: PinScope, patientId: string) => {
      if (!canEdit) {
        toast.warning('編集権限がありません');
        return;
      }
      // 患者 ID の整合性確認 (= 渡された patientId が pfvByVisitKey と一致するか).
      // 'day' scope は単一 PFV を更新するため pfvId からも逆引きする.
      if (scope === 'day') {
        if (!pfvId) {
          toast.error('対象の固定枠が見つかりません');
          return;
        }
        togglePfvPin.mutate(
          { pfvId, isPinned: nextPinned, patientId },
          {
            onSuccess: () => {
              toast.success(
                nextPinned
                  ? '完全固定にしました (Layer 2 は動かしません)'
                  : '完全固定を解除しました',
              );
            },
            onError: (err) => {
              const msg = err instanceof Error ? err.message : '不明なエラー';
              toast.error(`完全固定の更新に失敗: ${msg}`);
            },
          },
        );
        return;
      }

      // ─ 'all-days' (全曜日 bulk) ─────────────────────────────────────────
      // pfvByVisitKey から当該患者の全 PFV を抽出 (= 7 曜日 × 最大 2 slot まで).
      // 同 pfv は (patient_id, weekday, slot, time) 軸で複数 key を持ち得るが、
      // pfv.id でユニーク化することで重複 PATCH を防ぐ.
      if (!patientId) {
        toast.error('患者 ID が指定されていません');
        return;
      }
      const seen = new Set<string>();
      const allPfvs: PatientFixedVisitV2Read[] = [];
      for (const pfv of pfvByVisitKey.values()) {
        if (pfv.patient_id !== patientId) continue;
        if (seen.has(pfv.id)) continue;
        seen.add(pfv.id);
        allPfvs.push(pfv);
      }
      if (allPfvs.length === 0) {
        toast.error('対象患者の固定枠が見つかりません');
        return;
      }
      // 既に target 状態の PFV は除外 (= 無駄な PATCH 抑止 + audit_log のノイズ削減).
      const needUpdate = allPfvs.filter((p) => Boolean(p.is_pinned) !== nextPinned);
      if (needUpdate.length === 0) {
        toast.info(nextPinned ? '既に全曜日ロック状態です' : '既に全曜日解除状態です');
        return;
      }
      const items = needUpdate.map((p) => ({ pfv_id: p.id, is_pinned: nextPinned }));
      bulkPinPfvs.mutate(items, {
        onSuccess: () => {
          toast.success(
            nextPinned
              ? `全曜日 ${items.length} 件を完全固定しました (Layer 2 は動かしません)`
              : `全曜日 ${items.length} 件の完全固定を解除しました`,
          );
        },
        onError: (err) => {
          const msg = err instanceof Error ? err.message : '不明なエラー';
          toast.error(`全曜日の完全固定更新に失敗: ${msg}`);
        },
      });
    },
    [canEdit, pfvByVisitKey, togglePfvPin, bulkPinPfvs],
  );

  // ─── Phase G-41: 主要 4 ボタン (週生成 / 自動スタッフ割付 / 全面最適化 / プール投入) を本 panel Row 1 に再収容 ───
  //   page 側 (Card 1) に置いた G-40 構成から戻し、 mutation/state/dialog を全部 panel 内で抱える.
  //   pending 中の `isProcessing` は二次操作 (固定枠戻 / 一斉未割当) の多重実行抑止にも利用する.
  const generateWeekMut = useGenerateWeekOnly();
  const assignStaffOnlyMut = useAssignStaffOnly();
  const [diffAddOpen, setDiffAddOpen] = useState(false);
  const [fullOptimizeOpen, setFullOptimizeOpen] = useState(false);
  // P3-①: 当日欠勤の代替スタッフ提案ダイアログ.
  const [staffSubstituteOpen, setStaffSubstituteOpen] = useState(false);
  // Phase G-91: 確認レビューフローのダイアログ (連続 / 性別).
  const [assignWarningOpen, setAssignWarningOpen] = useState(false);
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [reviewApplying, setReviewApplying] = useState(false);
  // 統合提案モーダル「＋新規提案」(StageA+C+B). diff-add (プール投入) とは別 entry.
  const [proposeNewOpen, setProposeNewOpen] = useState(false);
  // スケジュール健康診断ダイアログ (Schedule Advisor Phase 1).
  const [scheduleHealthOpen, setScheduleHealthOpen] = useState(false);
  const isProcessing = generateWeekMut.isPending || assignStaffOnlyMut.isPending;

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
      toast.error(`週の生成に失敗しました: ${formatErr(err)}`);
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
      // Phase G-91: 確認レビューフロー。 問題コース (連続 / 性別) があれば
      // レビューダイアログを開く。 クリーンなら従来どおり success toast のみ。
      const items = res.review_items ?? [];
      if (items.length > 0) {
        setReviewItems(items);
        setAssignWarningOpen(true);
        toast.warning(
          `自動スタッフ割付しました (確定 ${res.courses_assigned} 件)。` +
            `レビューが必要なコースが ${items.length} 件あります。`,
        );
      } else {
        toast.success(`自動スタッフ割付しました (確定 ${res.courses_assigned} 件)`);
      }
    } catch (err) {
      toast.error(`自動スタッフ割付に失敗しました: ${formatErr(err)}`);
    }
  };

  // ─── 担当 dropdown 変更 (PATCH /courses/{id}) ───────────────────
  const updateCourseMut = useUpdateCourse();

  // Phase G-91 (修正1): レビュー承認カードを apply する (= 専用 endpoint 1 回呼び出し).
  // 従来の PATCH /courses ループ (assigned_staff_id のみ) を廃し、
  // POST /apply-staff-review を 1 回呼ぶ。 BE が自動割付と同一の _persist 経由で
  // VSA INSERT / course_status / primary・secondary 同期 / 2 名体制 / trainee
  // companion を全て反映する (= apply 済コースの visit が未割当表示になる
  // リグレッションを解消)。 監査はミドルウェアが POST を自動記録する。
  const applyStaffReviewMut = useApplyStaffReview();

  // Phase G-91 (修正5): 部分失敗時は成功済 course を reviewItems から除去し、
  // 失敗分のみ残す (= 再 apply で成功分を二重反映しない)。
  const handleApplyReview = async (approvedList: ApprovedReviewItem[]) => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    if (approvedList.length === 0) {
      setAssignWarningOpen(false);
      return;
    }
    setReviewApplying(true);
    try {
      const res = await applyStaffReviewMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        items: approvedList.map((a) => ({
          course_id: a.course_id,
          staff_id: a.candidate_staff_id,
        })),
      });
      // 承認した course のうち成功したものを抽出 (= partner 自動補完分は無視)。
      const approvedIds = new Set(approvedList.map((a) => a.course_id));
      const succeededIds = new Set(
        res.results.filter((r) => r.ok && approvedIds.has(r.course_id)).map((r) => r.course_id),
      );
      const failedCount = approvedList.length - succeededIds.size;
      if (failedCount === 0) {
        toast.success(`レビュー内容を割り付けました (${approvedList.length} 件)`);
        setAssignWarningOpen(false);
        setReviewItems([]);
      } else {
        // 成功した course のみ reviewItems から除去する (= 失敗分 + 未承認分は残す)。
        // 未承認カードを誤って消さないよう、 succeeded だけを取り除く。
        setReviewItems((prev) => prev.filter((i) => !succeededIds.has(i.course_id)));
        toast.error(
          `一部の割り付けに失敗しました (成功 ${succeededIds.size} / 失敗 ${failedCount})`,
        );
      }
    } catch (err) {
      toast.error(`割り付けに失敗しました: ${formatErr(err)}`);
    } finally {
      setReviewApplying(false);
    }
  };

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

  // ─── 2026-W20: 月-土タブ「リスト表示」用 — Before/After 形式の CourseListItem[] ──
  // 視覚言語を全面最適化の Before/After と統一. 当該曜日に対応するコース群を
  // course_template ベースで時刻昇順に並べた visit リストへ変換する.
  //   - patient.address / weekly_pattern (time_type / preferred_*) / sex_restriction を素直に展開.
  //   - lat/lng で same_address_group_id (= bucket key) を導出.
  //   - 連続する visit の Haversine 距離を distance_to_next_km にセット.
  const weekdayListCourses = useMemo<ScheduleCourseListItem[]>(() => {
    if (weekdayViewMode !== 'list') return [];
    const out: ScheduleCourseListItem[] = [];
    for (const { template, officeName } of courseTablesForActiveDay) {
      const course = findCourseForTemplate({
        template,
        weekday: activeWeekday,
        isoYear,
        isoWeek,
        courses,
      });
      const courseVisits = course ? (visitsByCourse.get(course.id) ?? []) : [];
      // start_time 昇順 (CourseGridVisit.start_slot は HH:MM, 同 slot は visit.id 安定).
      const sorted = [...courseVisits].sort((a, b) => {
        if (a.start_slot !== b.start_slot) return a.start_slot.localeCompare(b.start_slot);
        return a.id.localeCompare(b.id);
      });

      // visit ごとに lat/lng を取得して連続距離 (haversine) を算出.
      const visitsWithCoords = sorted.map((cv) => {
        const p = patientById.get(cv.patient_id);
        const lat = (p as { lat?: number | null } | undefined)?.lat ?? null;
        const lng = (p as { lng?: number | null } | undefined)?.lng ?? null;
        return { cv, patient: p, lat, lng };
      });

      const officeCoord = officeLatLngById.get(template.office_id) ?? null;
      const visits: ScheduleVisitListItem[] = visitsWithCoords.map((row, i, arr) => {
        const { cv, patient, lat, lng } = row;
        // 距離 = 「ここに来るまでの移動距離」(前の患者から。1 人目は拠点から).
        //   distanceMode='to_reach' で WeekdayScheduleCard が全員ぶん表示する。
        let distance: number | null = null;
        if (lat != null && lng != null) {
          if (i === 0) {
            if (officeCoord && officeCoord.lat != null && officeCoord.lng != null) {
              distance = haversineKm({ lat: officeCoord.lat, lng: officeCoord.lng }, { lat, lng });
            }
          } else {
            const prev = arr[i - 1];
            if (prev && prev.lat != null && prev.lng != null) {
              distance = haversineKm({ lat: prev.lat, lng: prev.lng }, { lat, lng });
            }
          }
        }
        const wp = patient?.weekly_pattern as
          | {
              time_type?: string | null;
              preferred_start?: string | null;
              preferred_end?: string | null;
            }
          | null
          | undefined;
        // 2026-W20 後期: 実動時間 = visit.end_time - visit.start_time (分).
        // visitById から元 VisitRead を引いて HH:MM → 分計算する.
        // 失敗時は null (UI 側で表示スキップ).
        const fullVisit = visitById.get(cv.id) ?? null;
        const startMin = fullVisit?.start_time ? toMinutes(fullVisit.start_time) : null;
        const endMin = fullVisit?.end_time ? toMinutes(fullVisit.end_time) : null;
        const durationMin =
          startMin != null && endMin != null && endMin > startMin ? endMin - startMin : null;
        return {
          key: cv.id,
          patient_id: cv.patient_id,
          start_time: cv.start_slot,
          patient_name: cv.patient_name ?? cv.patient_id,
          address: cv.patient_address ?? null,
          area_label: null,
          time_type: wp?.time_type ?? null,
          preferred_start: wp?.preferred_start ?? null,
          preferred_end: wp?.preferred_end ?? null,
          sex_restriction:
            normalizePatientSexRestriction(patient?.sex_restriction as string | null | undefined) ??
            null,
          same_address_group_id: buildSameAddressKey(lat, lng),
          distance_to_next_km: distance,
          duration_min: durationMin,
          // Phase G-21 T4 reviewer C2: list view にも pin 状態を流し込む.
          fixed_visit_id: cv.fixed_visit_id ?? null,
          is_pinned: cv.is_pinned === true,
        };
      });

      // Phase G-55: リストモードでも空き時間帯を時刻順 interleave で出すため、
      //   日テーブルと同じ実効定員 (effectiveCapacity) + 空き gap (freeGapsByCourse)
      //   を course 単位で添える。頭数ゲート (満員=非表示) は WeekdayScheduleCard 側で
      //   capacity から判定する (= 共有 freeGaps util と同 semantics)。
      const capMax = effectiveCapacity(
        template,
        activeWeekday,
        staffCountFor(template.office_id, activeWeekday),
        courseCodesMax,
      );
      const freeGaps = course ? (freeGapsByCourse.get(course.id) ?? []) : [];

      out.push({
        key: `${template.id}:${activeWeekday}`,
        title: `${officeName ? `${officeName} ` : ''}${template.label} コース`,
        summary: `${visits.length}件`,
        visits,
        freeGaps,
        capacity: { filled: visits.length, max: capMax },
      });
    }
    return out;
  }, [
    weekdayViewMode,
    courseTablesForActiveDay,
    activeWeekday,
    isoYear,
    isoWeek,
    courses,
    visitsByCourse,
    patientById,
    visitById,
    freeGapsByCourse,
    staffCountFor,
    courseCodesMax,
    officeLatLngById,
  ]);

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
        {/*
          Phase G-43: Row 1 を flex justify-end の単一行 toolbar に再構成.
          G-42 の 3-column grid (1fr_auto_1fr) では中央セルと右端セルの間に左 spacer が挟まり、
          「主要 4」と「固定枠戻 / 全件保存」が視覚的に離れて見えていたため、
          全要素を 1 つの flex container に並べて全部右寄せ + 主要 4 と固定枠戻の間に縦区切り線を配置する.
            Row 1 (admin/manager only, flex justify-end):
              [週を生成][自動スタッフ割付 🟢][全面最適化 🟢][プール投入 🟢] │ [固定枠戻][全件保存]
              ※ 主要 4 と固定枠戻/全件保存の間に縦区切り線で視覚的セパレーション.
              ※ Row 1 は最上段なので border-t 不要.
            Row 2 (曜日タブ + テーブル/リスト + 二次操作):
              左: 曜日タブ (月〜土 + 週) + iso week label.
              右 (ml-auto): 3 グループを縦区切り線で分離 (α | γ | δ).
                α テーブル/リスト切替 (「週」タブ時のみ非表示)
                γ 一斉未割当 (= リセット)
                δ 🔒 全件ロック + 🔓 全件解除 (= 一括設定)
          ボタンは基本 variant="outline" size="sm" で統一感を担保し、
          毎週必ず押す主要 2 ボタン (自動スタッフ割付 / 全面最適化) のみ variant="default" (= brand-primary 緑) で目立たせる.
        */}
        <Card className="p-3">
          {/* Row 1: 主要 4 ボタン + 固定枠戻 / 全件保存 をまとめて右寄せ (canEdit のみ).
              Phase G-43: flex flex-wrap justify-end で単一 toolbar 化し、主要 4 と固定枠戻の境界に
              縦区切り線を入れる. 狭幅では flex-wrap で折り返す. */}
          {canEdit ? (
            <div
              className="flex flex-wrap items-center justify-end gap-2"
              role="toolbar"
              aria-label="スケジュール主要操作"
              data-testid="schedule-main-action-toolbar"
            >
              {/* 主要 4 ボタン. */}
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
                variant="default"
                onClick={handleAssignStaff}
                disabled={assignStaffOnlyMut.isPending}
                data-testid="assign-staff-only-button"
              >
                {assignStaffOnlyMut.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <UserCheck className="mr-1 h-4 w-4" aria-hidden />
                )}
                自動スタッフ割付
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setStaffSubstituteOpen(true)}
                data-testid="staff-substitute-button"
              >
                <UserX className="mr-1 h-4 w-4" aria-hidden />
                欠勤対応
              </Button>
              <Button
                type="button"
                size="sm"
                variant="default"
                onClick={() => setDiffAddOpen(true)}
                disabled={isProcessing}
                data-testid="diff-add-button"
              >
                <Plus className="mr-1 h-4 w-4" aria-hidden />
                プール投入
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setProposeNewOpen(true)}
                disabled={isProcessing}
                data-testid="propose-new-button"
              >
                <Plus className="mr-1 h-4 w-4" aria-hidden />
                新規提案
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setScheduleHealthOpen(true)}
                data-testid="schedule-health-button"
              >
                <HeartPulse className="mr-1 h-4 w-4" aria-hidden />
                健康診断
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setFullOptimizeOpen(true)}
                disabled={isProcessing}
                data-testid="full-optimize-button"
              >
                <FlaskConical className="mr-1 h-4 w-4" aria-hidden />
                シミュレーション
              </Button>

              {/* 主要ボタン群と「固定枠戻 / 全件保存」 の区切り線. */}
              <span
                aria-hidden
                className="h-5 w-px bg-border-default"
                data-testid="course-day-button-divider"
              />

              {/* 固定枠戻 + 全件保存 (= データ書き戻し系). */}
              <ResetToFixedButton
                isoYear={isoYear}
                isoWeek={isoWeek}
                officeId={officeId}
                disabled={isProcessing}
              />
              <BulkFixToPatternButton canEdit={canEdit} isoYear={isoYear} isoWeek={isoWeek} />
            </div>
          ) : null}

          {/* Row 2: 曜日タブ (左) + テーブル/リスト + 二次操作 (右、canEdit のみ).
              canEdit 時のみ Row 1 (主要操作) との間に border-t + mt-3 pt-3 で水平区切り線 + 余白. */}
          <div
            className={`flex flex-wrap items-center gap-2${
              canEdit ? ' mt-3 border-t border-border-default pt-3' : ''
            }`}
            data-testid="course-day-tab-row"
          >
            {/* 曜日タブ */}
            <div
              role="tablist"
              aria-label="曜日タブ"
              className="flex flex-wrap gap-1"
              data-testid="course-day-tabs"
            >
              {DISPLAY_WEEKDAYS.map((wd) => {
                const selected = activeTab === wd;
                // Phase G-45: 拠点 filter 選択時のみ、選択中の拠点が休業の曜日タブは
                // 薄色 + 「(休)」表示 + tooltip. 全拠点モード (officeId=null) は
                // 既存挙動 (= 何もしない).
                const selectedOffice = officeId
                  ? (offices.find((o) => o.id === officeId) ?? null)
                  : null;
                const officeNonOperating =
                  selectedOffice != null &&
                  Array.isArray(selectedOffice.operating_weekdays) &&
                  !selectedOffice.operating_weekdays.includes(wd);
                // スタッフ数連動 (auto-schedule 統一): 拠点 filter 選択時、当該曜日に
                // 開講するコース (A-E 開講 or M定員>0) が 1 つも無ければ「休」扱い.
                // (営業日でも staff 0 名なら A-E は全休 / M も静的定員 0 なら休.)
                const hasOpenCourseOnDay =
                  selectedOffice != null &&
                  templates.some(
                    (t) =>
                      t.office_id === selectedOffice.id &&
                      effectiveCapacity(t, wd, staffCountFor(t.office_id, wd), courseCodesMax) > 0,
                  );
                const officeIsClosedOnDay =
                  selectedOffice != null && (officeNonOperating || !hasOpenCourseOnDay);
                const closedClasses = officeIsClosedOnDay && !selected ? ' opacity-50' : '';
                const tooltipTitle = officeIsClosedOnDay
                  ? officeNonOperating
                    ? `${selectedOffice?.name ?? '拠点'} は ${WEEKDAY_LABELS[wd]} 曜日は休業日です`
                    : `${selectedOffice?.name ?? '拠点'} は ${WEEKDAY_LABELS[wd]} 曜日に開講するコースがありません`
                  : undefined;
                return (
                  <button
                    key={wd}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    aria-controls={`course-day-panel-${wd}`}
                    onClick={() => setActiveTab(wd)}
                    data-testid={`course-day-tab-${wd}`}
                    data-closed={officeIsClosedOnDay ? 'true' : 'false'}
                    title={tooltipTitle}
                    className={
                      'rounded border px-3 py-1 text-xs font-semibold ' +
                      (selected
                        ? 'border-brand-primary bg-brand-primary text-white'
                        : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted') +
                      closedClasses
                    }
                  >
                    {WEEKDAY_LABELS[wd]}
                    {officeIsClosedOnDay ? (
                      <span className="ml-1 text-[10px] opacity-70">(休)</span>
                    ) : null}{' '}
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

            <span className="tnum text-[11px] text-text-muted">{isoWeekLabel}</span>

            {/* Row 2 右半: テーブル/リスト + 二次操作 を 3 グループ (α/γ/δ) に分けて
                縦区切り線で分離. ml-auto を持つ最初の見える要素で右寄せを担保 (= α が出ていれば α、
                「週」タブで α 非表示時は γ にフォールバックして右寄せを維持).
                Phase G-42: 旧 β group (= 固定枠戻 + 全件保存) は Row 1 右端へ移動. */}
            <div
              className="ml-auto flex flex-wrap items-center gap-3"
              data-testid="course-day-row2-right-toolbar"
            >
              {/* Group α: テーブル/リスト切替 (「週」タブ時は非表示). */}
              {activeTab !== 'week' ? (
                <div
                  role="group"
                  aria-label="月-土タブ 表示モード切替"
                  className="inline-flex overflow-hidden rounded border border-border-default text-xs"
                >
                  <button
                    type="button"
                    onClick={() => setWeekdayViewMode('table')}
                    aria-pressed={weekdayViewMode === 'table'}
                    data-testid="course-day-mode-table"
                    className={
                      weekdayViewMode === 'table'
                        ? 'bg-brand-primary px-2 py-1 text-white'
                        : 'bg-bg-base px-2 py-1 text-text-secondary hover:bg-bg-muted'
                    }
                  >
                    テーブル
                  </button>
                  <button
                    type="button"
                    onClick={() => setWeekdayViewMode('list')}
                    aria-pressed={weekdayViewMode === 'list'}
                    data-testid="course-day-mode-list"
                    className={
                      weekdayViewMode === 'list'
                        ? 'bg-brand-primary px-2 py-1 text-white'
                        : 'bg-bg-base px-2 py-1 text-text-secondary hover:bg-bg-muted'
                    }
                  >
                    リスト
                  </button>
                </div>
              ) : null}

              {canEdit ? (
                <>
                  {/* α / γ 間の区切り線 (α 表示時のみ).
                      Phase G-42: β group (= 固定枠戻 + 全件保存) を Row 1 へ移動した結果、
                      残る区切りは α/γ と γ/δ の 2 本. */}
                  {activeTab !== 'week' ? (
                    <span
                      aria-hidden
                      className="h-5 w-px bg-border-default"
                      data-testid="course-day-button-divider"
                    />
                  ) : null}

                  {/* Group γ: リセット (一斉未割当). */}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <UnassignAllStaffButton
                      isoYear={isoYear}
                      isoWeek={isoWeek}
                      officeId={officeId}
                      disabled={isProcessing}
                    />
                  </div>

                  {/* γ / δ 間の区切り線. */}
                  <span
                    aria-hidden
                    className="h-5 w-px bg-border-default"
                    data-testid="course-day-row2-divider"
                  />

                  {/* Group δ: 一括設定 (🔒 全件ロック + 🔓 全件解除). */}
                  <div className="flex flex-wrap items-center gap-1.5">
                    <BulkPinAllPfvsButton canEdit={canEdit} />
                  </div>
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
                className="space-y-2"
              >
                <CourseWeekOverview
                  templates={templates}
                  officeNameById={officeNameById}
                  visits={overviewVisits}
                  onJumpToDay={(wd) => setActiveTab(wd)}
                  staffEventsByStaff={staffEventsByStaff}
                  assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
                  staffMap={staffMap}
                  sameAddressKeyByPatientId={sameAddressKeyByPatientId}
                  onPatientClick={handleOpenPatientDetail}
                  onTogglePin={canEdit ? handleTogglePin : undefined}
                  staffCountFor={staffCountFor}
                  courseCodesMax={courseCodesMax}
                  managerCountFor={managerCountFor}
                  staffSummaryOffices={staffSummaryOffices}
                  freeGapsByCell={freeGapsByCell}
                  officeLatLngById={officeLatLngById}
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
                {/* Phase G-36: 表示モード切替 (テーブル ⇄ リスト) は Card 2 の独立 Row 3 へ移設済. */}
                {courseTablesForActiveDay.length === 0 ? (
                  <Card className="p-4 text-sm text-text-muted">
                    {WEEKDAY_LABELS[activeWeekday]}曜日の表示対象コースがありません。 拠点マスタの
                    コーステンプレート (定員) を確認してください。
                  </Card>
                ) : weekdayViewMode === 'list' ? (
                  /* 2026-W20: Before/After 形式の閲覧専用リスト. */
                  <div data-testid="course-day-list-view">
                    <WeekdayScheduleCard
                      title={`${WEEKDAY_LABELS[activeWeekday]}曜日 コース一覧`}
                      totalSummary={`${weekdayListCourses.reduce(
                        (n, c) => n + c.visits.length,
                        0,
                      )}件`}
                      tone="muted"
                      courses={weekdayListCourses}
                      testIdPrefix={`course-day-list-${activeWeekday}`}
                      onPatientClick={handleOpenPatientDetail}
                      // Phase G-21 T4 reviewer C2: list view にも 🔒 toggle を渡す
                      // (= admin/manager 時のみ button が描画される).
                      onTogglePin={canEdit ? handleTogglePin : undefined}
                      // 距離は「ここに来るまでの移動 (前の患者/拠点から)」を全員ぶん表示.
                      distanceMode="to_reach"
                    />
                  </div>
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
                    // Phase G-55: 実効定員 (= 親機が既に週ビューで使う effectiveCapacity を流用)。
                    //   A-E は開講判定で 6 / M系は静的 capacity。filled は配置済み visit 件数。
                    const capMax = effectiveCapacity(
                      template,
                      activeWeekday,
                      staffCountFor(template.office_id, activeWeekday),
                      courseCodesMax,
                    );
                    const capacityInfo = { filled: visits.length, max: capMax };
                    // 空き時間帯 (≥60分) は course が生成済みのときのみ算出済みマップから引く。
                    const freeGaps = course ? (freeGapsByCourse.get(course.id) ?? []) : [];
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
                        officeNameById={officeNameById}
                        capacity={capacityInfo}
                        freeGaps={freeGaps}
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
                        onPatientClick={handleOpenPatientDetail}
                        // Phase G-21 T4 reviewer C2: 🔒 完全固定 toggle を wire-up.
                        // CourseDayTable 側で canEdit=true && onTogglePin 指定時のみ
                        // button を描画する (= staff role は表示なし).
                        onTogglePin={canEdit ? handleTogglePin : undefined}
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
              partnerLocationByPatientSlot={partnerLocationByPatientSlot}
              renderCard={(p, slotInfo) => {
                const wp = coerceWeeklyPattern(p.weekly_pattern);
                // Phase G-44: 不足表示用. 複数体制患者は slot 単位で表示しているので
                // 数値ラベルは出さない (= 既存 partnerAssigned バッジで十分).
                const isMultiStaff =
                  (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff ===
                  true;
                const shortageInfo =
                  !isMultiStaff && patientShortageById.has(p.id)
                    ? (patientShortageById.get(p.id) ?? null)
                    : null;
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
                      // Wave 38: 相方の現在地ラベル ("本店-A 15:00" など) を素通しする.
                      partnerLocationLabel: slotInfo.partnerLocationLabel ?? null,
                      // Phase G-44: 「希望 N、配置 X、不足 Y」 のラベル表示.
                      shortageInfo,
                    }}
                    disabled={!canEdit}
                    // 保留プールの患者カードクリックで詳細 + プール投入提案を開く.
                    onCardClick={() => handleOpenPoolPatientDetail(p.id)}
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

        {/* Phase G-41: 主要 4 ボタン を本パネル Row 1 に戻したため、 対応する dialog も本パネルで描画する.
            Wave 41 v2 § 3 / §13.5.1: 差分追加ダイアログ. */}
        <DiffAddDialog
          open={diffAddOpen}
          onClose={() => setDiffAddOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          canEdit={canEdit}
        />

        {/* 統合提案モーダル「＋新規提案」(StageA+C+B). diff-add とは独立・併存. */}
        <ProposeNewModal
          open={proposeNewOpen}
          onClose={() => setProposeNewOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          poolPatients={poolPatients}
        />

        {/* Schedule Advisor Phase 1: スケジュール健康診断ダイアログ (read-only). */}
        <ScheduleHealthDialog
          open={scheduleHealthOpen}
          onClose={() => setScheduleHealthOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          weekLabel={isoWeekLabel}
        />

        {/* Wave 41 v2 § 4 / §13.5.2: 全面最適化ダイアログ. */}
        <FullOptimizeDialog
          open={fullOptimizeOpen}
          onClose={() => setFullOptimizeOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
        />

        {/* P3-①: 当日欠勤の代替スタッフ提案ダイアログ. */}
        <StaffSubstituteDialog
          open={staffSubstituteOpen}
          onClose={() => setStaffSubstituteOpen(false)}
        />

        {/* Phase G-91: 自動スタッフ割付の確認レビューフロー (連続 / 性別). */}
        <AssignWarningDialog
          open={assignWarningOpen}
          onClose={() => setAssignWarningOpen(false)}
          reviewItems={reviewItems}
          onApply={handleApplyReview}
          applying={reviewApplying}
        />

        {/* 患者スケジュール詳細 (固定枠 vs 今週 + 個別反映)
            条件付きレンダリングで unmount を保証 (hooks の lazy 起動). */}
        {patientDetailId !== null ? (
          <PatientScheduleDetailDialog
            patientId={patientDetailId}
            open
            onClose={handleClosePatientDetail}
            isoYear={isoYear}
            isoWeek={isoWeek}
            canEdit={canEdit}
            // プール由来クリックのときだけプール投入提案セクションを表示する.
            // office スコープは一括ダイアログ (DiffAddDialog) と揃え、同一患者が
            // 両表示で同一提案になるようにする (ドリフト防止 / 同一 queryKey 共有).
            enablePoolProposal={patientDetailPoolMode}
            officeId={officeId}
          />
        ) : null}
      </section>
    </DndContext>
  );
}
