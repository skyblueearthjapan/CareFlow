'use client';

/**
 * CourseDayTablePanel — Wave 17 Phase B-2 メインパネル.
 *
 * Excel スケジュール枠組みに完全準拠した 1 画面構造
 * (Phase G-43 で Row 1 を flex 単一 toolbar 化。W-9/W-9b で両端配置に変更):
 *   ┌─ ヘッダー ────────────────────────────────────────────┐
 *   │  Row 1 (両端配置 toolbar, admin/manager only):                                 │
 *   │  [週を生成][週次ガイド] [新規患者登録][診断][最適化] │ [固定枠戻][全件保存] │
 *   │  (中段・右寄せ) [全件ピン留め][全件ピン留め解除] │ [今週全件固定][今週全件解除] │
 *   │  ─── border-t ────────────────────                                            │
 *   │  Row 2 (曜日タブ + 表示モード + 二次操作):                                      │
 *   │    [月][火][水][木][金][土][週] YYYY-Www                                       │
 *   │    [タイムライン | リスト] │ [戻る][進む][自動スタッフ割当][一斉スタッフ未割当] (右寄せ) │
 *   ├──────────────────────────────────────────────────────┤
 *   │  選択曜日の盤面 (縦タイムライン or 日リスト)             │
 *   │   - 本店 A / B / C / D / E / M + 都賀 A 等             │
 *   │   - タイムラインは 9:00〜18:00 の時間比例カード          │
 *   ├──────────────────────────────────────────────────────┤
 *   │  保留プール (DnD ソース)                                │
 *   └──────────────────────────────────────────────────────┘
 *
 * Phase 2 (2026-07): 旧「テーブル」表示 (CourseDayTable) を撤去し、日タブは
 *   [タイムライン | リスト] の 2 モードのみ。テーブルにしか無かった 4 機能
 *   (担当変更 / 訪問削除 / 「今週のみ」昇格 / プールへの DnD) は移設済み。
 *
 * 主な機能:
 *   - 曜日タブで月〜土を切替 (capacity_<wd> > 0 の曜日のみ表示)
 *   - Phase G-41: 「週を生成」「自動スタッフ割付」「全面最適化」「プール投入」 を Row 1 (右寄せ) に再収容.
 *     mutation pending 状態は内部で算出し、二次操作 (固定枠戻 / 一斉未割当) を多重実行から保護する.
 *   - 2026-07: 「自動スタッフ割付」を「自動スタッフ割当」に改称し、Row 2 の
 *     Group γ (一斉未割当の左隣) へ移動 (リセット→再割当の操作動線を隣接させる).
 *   - タイムライン列ヘッダの担当 dropdown で PATCH /api/v1/courses/{id}
 *   - プールカード → タイムライン列ドロップ → place-and-fix
 *
 * RBAC:
 *   - admin / manager: 編集可 (ドロップ + 担当変更 + 主要 4 + 二次操作 + 個別 reset)
 *   - staff: 閲覧のみ
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import {
  HeartPulse,
  ListChecks,
  Loader2,
  Redo2,
  RefreshCw,
  Route,
  Undo2,
  UserCheck,
} from 'lucide-react';
import { toast } from 'sonner';

import { Rakusuke } from '@/components/brand/Rakusuke';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { addDays } from '@/components/schedule/WeekSelector';
import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import {
  useAssignStaffOnly,
  useApplyStaffReview,
  type AutoCommittedNotice,
  type CrossOfficeNotice,
  type RescueSwapNotice,
  type ReviewItem,
  type SecondaryConstraintWarning,
  type StageAssignmentNotice,
  type UnresolvedGenderWarning,
  type UnresolvedNgWarning,
} from '@/lib/queries/assign_staff_only';
import {
  parseConstraintConfirmationDetail,
  type ConstraintWarning,
} from '@/lib/schemas/patient_ng_staff';
import { useStaffNgPatients } from '@/lib/queries/patient_ng_staff';
import { useCourses, useUpdateCourse, type CourseV2Read } from '@/lib/queries/courses';
import { useGenerateWeekOnly } from '@/lib/queries/generate_week';
import { useOffices } from '@/lib/queries/offices';
import { usePatients } from '@/lib/queries/patients';
import { useOpLogState, useUndoOpLog, useRedoOpLog, useInvalidateOpLog } from '@/lib/queries/opLog';
import { usePlaceAndFix } from '@/lib/queries/place_and_fix';
import { useStaffList } from '@/lib/queries/staff';
import {
  buildStaffEventsMap,
  useUpdateEventForDrag,
  useWeekStaffEvents,
} from '@/lib/queries/staff-events';
import { useWeekStaffOverrides, type WeekOverrideRead } from '@/lib/queries/staff-overrides';
import type { EventRead } from '@/lib/schemas/staff-events';
import { useDeleteVisit, useVisits } from '@/lib/queries/visits';
import { useBulkSyncWeekToFixedMutation } from '@/lib/api/patientSync';
import { useBulkPinPfvs, useTogglePfvPin } from '@/lib/queries/g21';
import { useToggleVisitWeekPin } from '@/lib/queries/visit_week_pin';
import { apiErrorMessage } from '@/lib/api/errorMessage';
// Phase G-47: PinScope 型 (= 個別 🔒 toggle のスコープ '曜日のみ' / '全曜日').
import type { PinScope } from './PinScopeMenu';
import type { PatientFixedVisitV2Read } from '@/lib/schemas/v2/patient_fixed_visit';
import {
  courseCodeIndex,
  effectiveCapacity,
  type CourseTemplateRead,
} from '@/lib/schemas/v2/course_template';
import { useWeekdayStaffCapacityLookup } from '@/lib/queries/weekday_staff_capacity';
import { usePfvCoursePresenceLookup } from '@/lib/queries/pfv_course_presence';
import {
  SEX_RESTRICTION_LABEL,
  coerceWeeklyPattern,
  formatPreferredTimeLabel,
  normalizePatientSexRestriction,
  type PatientRead,
} from '@/lib/schemas/patient';
import { isoWeekFromLocalDate } from '@/lib/format/isoWeek';

import {
  buildSameAddressKey,
  haversineKm,
  type CourseListItem as ScheduleCourseListItem,
  type VisitListItem as ScheduleVisitListItem,
} from '../WeekdayScheduleCard';
import { TimelineDayList } from '@/components/schedule/timeline/TimelineDayList';
import { BulkFixToPatternButton } from './BulkFixToPatternButton';
import { BulkWeekPinAllButton } from './BulkWeekPinAllButton';
import { AssignWarningDialog, type ApprovedReviewItem } from './AssignWarningDialog';
import { ConstraintOverrideConfirmDialog } from './ConstraintOverrideConfirmDialog';
import { ackFlag, useConstraintConfirmRetry } from './useConstraintConfirmRetry';
import { BulkPoolInsertDialog } from './BulkPoolInsertDialog';
import { RegisterPatientButton } from './RegisterPatientButton';
import { ScheduleHealthDialog } from './ScheduleHealthDialog';
import { ScopeOptimizeDialog } from './ScopeOptimizeDialog';
import { ScheduleReviewBanner } from './ScheduleReviewBanner';
import { WeeklyRitualGuideDialog } from './WeeklyRitualGuideDialog';
import { ResetToFixedButton } from './ResetToFixedButton';
import { UnassignAllStaffButton } from './UnassignAllStaffButton';
import {
  floorToCourseSlot,
  getStaffEventsForWeekday,
  parseEventDraggableId,
  toMinutes,
  type CourseGridVisit,
  type PartnerLocation,
} from './courseGrid';
import { CourseWeekOverview, type WeekOverviewVisit } from './CourseWeekOverview';
import { StaffWeekBoard } from './StaffWeekBoard';
import {
  WeekCoursePalette,
  type CourseDragPayload,
  type PaletteCourse,
} from './WeekCoursePalette';
import {
  parseTlColDroppableId,
  parseTlPairDraggableId,
  parseTlVisitDraggableId,
  TimelineDayBoard,
  TlPairDragGhost,
  TlVisitDragGhost,
  type StaffEventFrame,
  type TimelineCourseColumn,
} from '@/components/schedule/timeline/TimelineDayBoard';
import {
  WeekTimelineBoard,
  type WeekTimelineOption,
} from '@/components/schedule/timeline/WeekTimelineBoard';
import { AccompanimentBar } from '@/components/schedule/timeline/accompaniment/AccompanimentBar';
import { useAccompanimentController } from '@/components/schedule/timeline/accompaniment/useAccompanimentController';
import type { AccompanimentWeekVisit } from '@/components/schedule/timeline/accompaniment/types';
import { useTraineeAccompaniments } from '@/lib/queries/trainee_accompaniments';
import {
  augmentAssignedSlotsWithAccompaniment,
  buildAccompanimentLinkIndex,
  planSecondStaffToggle,
  type FulfillmentVisit,
} from '@/lib/scheduling/accompanimentFulfillment';
import { PartnerCourseDialog } from './PartnerCourseDialog';
import { cn } from '@/lib/utils';
import { type TimelineRowMeta } from './CourseMoveTimeline';
import { PatientCard, type PatientCardData } from './PatientCard';
import { PatientScheduleDetailDialog } from './PatientScheduleDetailDialog';
import { POOL_DROPPABLE_ID, buildPoolDraggableId, parsePoolDraggableId } from './PoolPanel';
import { PoolOverviewPane } from './PoolOverviewPane';
import type { Movability, SlotIndex } from '@/lib/schemas/v2/patient_fixed_visit';
// Phase G-44: 「希望訪問パターン」 vs 「実 visit 数」 の共通 utility.
import { countWeekVisits, getDesiredWeeklyVisitCount } from '@/lib/scheduling/preferred-visits';
// Phase G-55: 空き時間帯 (≥60分) 算出の共有 util (mobile FieldBoard と共通).
import {
  computeFreeGaps,
  businessBlocksFromHours,
  BUSINESS_BLOCKS,
  fmtHM,
  parseHM,
  SAME_ADDRESS_PAIR_MIN_OCCUPANCY,
  type FreeGap,
} from '@/lib/scheduling/freeGaps';
// T-2 ②-b: タイムラインカード DnD (15分スナップ) → 二択 (この週だけ/固定パターン)。
import {
  genderPalette,
  snapYOffsetToMinutes,
  TL_DAY_END_MIN,
  TL_DAY_START_MIN,
} from '@/lib/scheduling/timeline';
import { TimelineMoveDialog } from '@/components/schedule/timeline/TimelineMoveDialog';
import { useVisitMoveWeekOnly } from '@/lib/queries/visitMoveWeekOnly';
import type { ChangeScopeValue } from '@/components/schedule/v2/ChangeScopeChoice';
// T-2 ②-a: 空き枠クリック → 登録モーダル (訪問=place-and-fix / イベント=複数スタッフ一括登録).
import {
  SlotRegisterDialog,
  type SlotPatientOption,
} from '@/components/schedule/timeline/SlotRegisterDialog';
import { TimelineEventAddDialog } from '@/components/schedule/timeline/TimelineEventAddDialog';
import { EventEditDialog } from '@/app/(app)/staff/[id]/_components/EventEditDialog';
import { SendEventsToKaipokeDialog } from './SendEventsToKaipokeDialog';
import { ImportEventsFromKaipokeDialog } from './ImportEventsFromKaipokeDialog';
// Phase G-88: 営業時間設定を空き枠表示に反映 (取得前/失敗時は既定枠にフォールバック).
import { useSchedulingSettings } from '@/lib/queries/schedulingSettings';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

/** 表示曜日 (月〜土の 6 つ). 日曜は除外. */
export const DISPLAY_WEEKDAYS = [0, 1, 2, 3, 4, 5] as const;
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

/**
 * NG スタッフ / 性別制限 (patient-ng-staff-design.md §7-2) の確認ダイアログ文言。
 * 経路ごとに動詞を合わせる (BE は同一の 422 を返す)。
 */
const MOVE_CONSTRAINT_TEXT = {
  title: 'それでも移動しますか？',
  description: 'この移動先の担当者は、次の制約に抵触します',
  confirmLabel: '移動する',
} as const;
const PLACE_CONSTRAINT_TEXT = {
  title: 'それでも配置しますか？',
  description: 'この配置先の担当者は、次の制約に抵触します',
  confirmLabel: '配置する',
} as const;

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

/**
 * ドロップ先の論理位置 (曜日 × コース × 時刻).
 *
 * Phase 2 で日テーブル (`course-day-cell:` droppable) を撤去したあとは、
 * タイムライン列 (`tl-col:`) へのドロップ Y 座標から合成する「仮想セル」だけが
 * この形を作る。プール配置 (パリティ①) / イベント移動 (パリティ②) の双方が
 * 旧テーブルと同一の後段フローを共有するための中間表現。
 */
type DropCell = { weekday: number; courseTemplateId: string; time: string };

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
    // 2b) 臨時コース fallback (カイポケ取り込み R-3): code='臨2'..'臨9' を
    //     label='臨時' (先頭 '臨') の template に流す。'臨' 自体は 3) で拾われる。
    //     codeUp/labelFirst は toUpperCase 済みだが CJK には no-op (無害)。
    if (labelFirst === '臨' && /^臨\d$/.test(codeUp)) {
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
}

// ─────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────

export function CourseDayTablePanel({ weekStart, officeId, canEdit }: CourseDayTablePanelProps) {
  const { isoYear, isoWeek } = useMemo(() => isoWeekFromLocalDate(weekStart), [weekStart]);

  // ─── 曜日タブ state (Wave 18 Phase B-6: 'week' = 週間ビュー) ─────
  // デフォルトは週ビュー ('week'). 曜日別 (月-土) は各タブで切替.
  // 'staff' = 職員スケジュール (スタッフ×曜日・イベント運用の家。
  // kaipoke-event-two-way-design.md §6-c で週ビュー内サブモードから昇格)。
  const [activeTab, setActiveTab] = useState<number | 'week' | 'staff'>('week');
  const activeWeekday = typeof activeTab === 'number' ? activeTab : 0;

  // ─── 2026-W20: 月-土タブの表示モード ─────────────────────────
  // timeline = 縦タイムライン (T-1・時間比例カード / DnD 編集可能). 既定.
  // list     = 時刻順 visit リスト (視覚言語統一).
  //   docs/plans/schedule-timeline-redesign-design.md
  // Phase 2 (2026-07): 旧 'table' (Excel 形式時刻グリッド) は撤去済み。
  const [weekdayViewMode, setWeekdayViewMode] = useState<'list' | 'timeline'>('timeline');
  // T-3: 週タブの見え方。overview=既存の全コース俯瞰(既定・全機能温存) / timeline=週タイムライン(全コース縦積み)。
  // ラベルは overview="リスト" (PO指示 2026-07-08。内部値は据置)。
  // 'staff' = スタッフ別 (カイポケ職員スケジュール同等・案B・PO要望 2026-07-26)。
  // 旧 'staff' サブモードはトップレベルタブ「職員スケジュール」へ昇格済み (2026-08-20)。
  const [weekViewMode, setWeekViewMode] = useState<'overview' | 'timeline'>('overview');

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
  // T-4: 提案系タイムライン (CourseMoveTimeline 等) のカード視覚言語用メタ。
  // 性別ウォッシュ・条件ピル・📍住所を患者マスタから FE join する (API 不変)。
  // note: allPatients は limit=500 — 超過分の患者は中立色になるだけ (安全な劣化)。
  const patientRowMetaById = useMemo(() => {
    const m = new Map<string, TimelineRowMeta>();
    for (const p of allPatients) {
      m.set(p.id, {
        sex: p.sex ?? null,
        address: p.address ?? null,
        condLabel:
          p.sex_restriction === 'female_only'
            ? '👩女性のみ'
            : p.sex_restriction === 'male_only'
              ? '👨男性のみ'
              : null,
      });
    }
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

  // `(patient_id, weekday, slot_index) → PFV` の **時刻を含まない** lookup (2026-08-08).
  //
  // 上の pfvByVisitKey は開始時刻まで含めた完全一致なので、型とズレている訪問は
  // 「固定枠なし」と区別がつかない。ズレを可視化し、ピン留めできない理由を正確に
  // 伝える (「固定訪問スケジュールは 13:00 です」) には、時刻抜きで引ける必要がある。
  const pfvByPatientWeekdaySlot = useMemo(() => {
    const m = new Map<string, PatientFixedVisitV2Read>();
    pfvPatientIds.forEach((pid, i) => {
      const list = pfvQueries[i]?.data ?? [];
      for (const pfv of list) {
        if (pfv.mode !== 'normal') continue;
        m.set(`${pid}:${pfv.weekday}:${pfv.slot_index ?? 0}`, pfv);
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

  // ─── PO 2026-07-09: PFV に含まれるコースを「正」として列を出す (和集合の根拠) ──
  // (course_template_id, weekday) → PFV 件数. スタッフ数連動が 0 でも PFV があれば
  // 列を隠さない (= 既存訪問を可視に保つ). effectiveCapacity と和集合で判定する.
  const { pfvCountFor } = usePfvCoursePresenceLookup();

  // Phase G-25: 担当 dropdown は全拠点解放 (= 拠点を超えて配置可能).
  // 自動算出 (run_v2_pipeline) は引き続き拠点内のみだが、 手動 dropdown は全 active staff を表示.
  // 各 option には 「氏名 (拠点名)」 形式で所属を併記 (= CourseDayTable 側で format).
  const staffByOffice = useMemo(() => {
    // 新人同行 §8: 新人 (is_trainee) はコース担当にできない → 担当 dropdown から除外。
    // BE PATCH /courses でも 422 で二重防御。フラグ OFF で自動的に候補へ復帰する。
    const allActive = [...allStaff]
      .filter((s) => s.status === 'active' && !s.is_trainee)
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

  // ─── 当該週に visit が実在する course_id の集合 (列の表示条件 ③ の根拠) ──
  // course は曜日固定なので course_id を持つ visit があれば「その曜日に訪問実在」。
  const courseIdsWithVisits = useMemo(() => {
    const s = new Set<string>();
    for (const v of weekVisits) {
      const cid = v.course_id ?? null;
      if (cid) s.add(cid);
    }
    return s;
  }, [weekVisits]);

  // ─── 表示するコース一覧: 「拠点 × テンプレート」を活性曜日で表示判定 (和集合) ──
  // PO 2026-07-09: 列の表示条件は以下の和集合 (どれか 1 つでも真なら表示):
  //   ① スタッフ数連動 effectiveCapacity>0 (A-E は staff_count, M系は静的 capacity)。
  //   ② PFV presence: 固定訪問 (PFV) にこのテンプレ×曜日のコースが含まれる (= 正)。
  //   ③ 当該曜日にこのテンプレのコースへ実在する visit がある。
  // これによりスタッフ削除等で列ごと消えて既存訪問が管理画面から不可視になる事故を防ぐ。
  const courseTablesForActiveDay = useMemo(() => {
    const list: Array<{
      template: CourseTemplateRead;
      officeName: string;
    }> = [];
    for (const t of templates) {
      const staffCount = staffCountFor(t.office_id, activeWeekday);
      const capacityOpen = effectiveCapacity(t, activeWeekday, staffCount, courseCodesMax) > 0;
      const pfvOpen = pfvCountFor(t.id, activeWeekday) > 0;
      let visitOpen = false;
      if (!capacityOpen && !pfvOpen) {
        const course = findCourseForTemplate({
          template: t,
          weekday: activeWeekday,
          isoYear,
          isoWeek,
          courses,
        });
        visitOpen = course ? courseIdsWithVisits.has(course.id) : false;
      }
      if (!capacityOpen && !pfvOpen && !visitOpen) continue;
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
  }, [
    templates,
    offices,
    activeWeekday,
    staffCountFor,
    courseCodesMax,
    pfvCountFor,
    isoYear,
    isoWeek,
    courses,
    courseIdsWithVisits,
  ]);

  // ─── PO 2026-07-09: スタッフ不足バナー (表示 A-E 列数 > 稼働スタッフ数) ──
  // 列は PFV/visit の和集合でも出るため、稼働スタッフが足りない拠点を曜日単位で警告する。
  // A-E コースのみ対象 (M系は静的定員なので除外)。拠点ごとに 1 行。
  const staffShortageBanners = useMemo(() => {
    const aeCountByOffice = new Map<string, number>();
    for (const { template } of courseTablesForActiveDay) {
      if (courseCodeIndex(template.label) === null) continue; // M系は対象外
      aeCountByOffice.set(template.office_id, (aeCountByOffice.get(template.office_id) ?? 0) + 1);
    }
    const wdLabel = WEEKDAY_LABELS[activeWeekday] ?? '';
    const out: Array<{ officeId: string; message: string }> = [];
    for (const [oid, aeCount] of aeCountByOffice) {
      const staff = staffCountFor(oid, activeWeekday);
      if (aeCount > staff) {
        const officeName = offices.find((o) => o.id === oid)?.name ?? '';
        out.push({
          officeId: oid,
          message: `⚠ スタッフ不足: ${officeName} ${wdLabel}曜 は コース${aeCount}本 に対して稼働スタッフ ${staff}名 です。担当を確認してください。`,
        });
      }
    }
    out.sort((a, b) => a.message.localeCompare(b.message, 'ja'));
    return out;
  }, [courseTablesForActiveDay, staffCountFor, activeWeekday, offices]);

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
        // 2b) 臨時コース (臨2..臨9) → label 先頭 '臨' template fallback (R-3)
        (/^臨\d$/.test(codeUp)
          ? templates.find(
              (t) => t.office_id === c.office_id && (t.label || '').trim().slice(0, 1) === '臨',
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
      // 2026-08-07 (PO 要望): 可動域を盤面に「うっすら」出すための値.
      let movability: Movability | null = null;
      // 2026-08-08 (PO 要望): 型とズレている場合の「型の開始時刻」.
      let masterStartTime: string | null = null;
      if (pfvWeekday !== null && visitHHMM) {
        const pfv = pfvByVisitKey.get(`${v.patient_id}:${pfvWeekday}:${visitHHMM}:${pfvSlot}`);
        if (pfv) {
          fixedVisitId = pfv.id;
          isPinned = pfv.is_pinned === true;
          movability = pfv.movability ?? null;
        } else {
          // 完全一致が無い = 「固定枠が無い」か「固定枠はあるが時刻がズレている」。
          // 後者だけを拾って型の時刻を伝える (前者は従来どおり null のまま)。
          const byWd = pfvByPatientWeekdaySlot.get(`${v.patient_id}:${pfvWeekday}:${pfvSlot}`);
          if (byWd) {
            masterStartTime = (byWd.start_time ?? '').slice(0, 5) || null;
            // 可動域は「その枠の性質」なのでズレていても伝える (盤面の一貫性).
            movability = byWd.movability ?? null;
          }
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
        // 可動域 (2026-08-07): 'locked' は提案系・自動割当とも不可侵。'unknown' は非表示。
        movability,
        // 型とのズレ (2026-08-08): ズレている場合のみ型の開始時刻が入る。
        master_start_time: masterStartTime,
        // Wave U-2: 「今週のみ」チップの根拠 (source='manual_week' でチップ表示).
        source: (v as { source?: string | null }).source ?? null,
        // 週のピン (青ピン / 2026-08-09): source と独立のフラグ。
        week_pinned: (v as { week_pinned?: boolean | null }).week_pinned ?? null,
        // R-2: キャンセル表示 ('cancelled' のとき grey + 打消し線 + バッジ).
        status: (v as { status?: string | null }).status ?? null,
        // T-1 縦タイムライン: 実時刻 (時間比例描画) + 患者性別 (カード地色).
        start_time: v.start_time ?? null,
        end_time: v.end_time ?? null,
        patient_sex: (patient?.sex as string | null | undefined) ?? null,
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
    pfvByPatientWeekdaySlot,
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
      // R-2: キャンセル visit は空き計算から除外 (= 空き扱い).
      if ((v as { status?: string | null }).status === 'cancelled') continue;
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

  // ─── 新人同行の充足判定用: 患者ごとの「配置済み訪問」 ─────────────────
  //   複数名対応患者の 2 人目 (slot1) を新人同行で賄えているかを判定するため、
  //   assignedSlotsByPatient と同じ raw weekVisits を材料に、各訪問の
  //   id / course_id / course_template_id / weekday を解決したものを患者単位で集める。
  //   (course_id → template は courseTemplateByCourseId、weekday は visit_date から。)
  const placedVisitsByPatient = useMemo(() => {
    const m = new Map<string, FulfillmentVisit[]>();
    for (const v of weekVisits) {
      const courseId = v.course_id ?? null;
      const courseTemplateId = courseId ? (courseTemplateByCourseId.get(courseId) ?? null) : null;
      let weekday: number | null = null;
      if (v.visit_date) {
        const d = new Date(v.visit_date + 'T00:00:00');
        weekday = (d.getDay() + 6) % 7;
      } else if (courseId) {
        weekday = courses.find((c) => c.id === courseId)?.weekday ?? null;
      }
      const arr = m.get(v.patient_id) ?? [];
      arr.push({ id: v.id, courseId, courseTemplateId, weekday });
      m.set(v.patient_id, arr);
    }
    return m;
  }, [weekVisits, courseTemplateByCourseId, courses]);

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

  // W-3: 希望未登録 active 患者 (weekly_pattern 未設定 / frequency_per_week<=0)。
  // プールには載らないが存在する患者を可視化するための安全網。
  const unregisteredActivePatients = useMemo(() => {
    return allPatients.filter((p) => p.status === 'active' && getDesiredWeeklyVisitCount(p) === 0);
  }, [allPatients]);

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
      // 2026-08-08: 完全一致が無いときだけ、時刻抜きで固定枠を引いてズレを判定する。
      const pfvByWd = pfvHit
        ? undefined
        : pfvByPatientWeekdaySlot.get(`${v.patient_id}:${wd}:${pfvSlot}`);
      const masterStartTime = pfvByWd ? (pfvByWd.start_time ?? '').slice(0, 5) || null : null;
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
        // 可動域 (2026-08-07): 週タイムラインでも「固 / 時 / 曜」を淡く出す。
        movability: pfvHit?.movability ?? pfvByWd?.movability ?? null,
        // 型とのズレ (2026-08-08): ズレている場合のみ型の開始時刻が入る。
        master_start_time: masterStartTime,
        // 週のピン (青) の表示根拠。source と week_pinned の両方を運ぶ (2026-08-09)。
        source: (v as { source?: string | null }).source ?? null,
        week_pinned: (v as { week_pinned?: boolean | null }).week_pinned ?? null,
        // 週ビューの距離算出用 (コース合計 + 次までの距離).
        lat: (patient as { lat?: number | null } | undefined)?.lat ?? null,
        lng: (patient as { lng?: number | null } | undefined)?.lng ?? null,
        // T-3 週タイムライン: 実時刻 (時間比例) + 患者性別 (カード地色) + 2名判定.
        end_time: v.end_time ?? null,
        patient_sex: (patient?.sex as string | null | undefined) ?? null,
        patient_requires_multiple_staff:
          (patient as { requires_multiple_staff?: boolean | null } | undefined)
            ?.requires_multiple_staff === true,
        // 週タイムラインの同住所・同時刻ペア (90分占有ボックス) 判定用。
        same_address_key: sameAddressKeyByPatientId.get(v.patient_id) ?? null,
        // 週カードの📍住所行 (日ビューと情報統一・PO要望)。
        patient_address: patient?.address ?? null,
        // スタッフ別ビューの帰属用 (2026-07-26: 臨時テンプレ合算の誤帰属を防ぐため
        // コース担当ではなく訪問の primary で行を決める)。
        primary_staff_id: (v as { primary_staff_id?: string | null }).primary_staff_id ?? null,
      });
    }
    return out;
  }, [
    weekVisits,
    courseTemplateByCourseId,
    courses,
    patientById,
    pfvByVisitKey,
    pfvByPatientWeekdaySlot,
    visitsByGroupId,
    sameAddressKeyByPatientId,
  ]);

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

  /**
   * スタッフ枠 (PO確定 2026-07-26): 曜日ごとに「その日コースを持たないが
   * イベントのあるスタッフ」を枠として盤面に編み込むための元データ。
   * 休みの人にもその日のスケジュールがある、をUIで表現する。
   * weekday → frames (スタッフ名順)。
   */
  const staffEventFramesByWeekday = useMemo(() => {
    const assignedByWd = new Map<number, Set<string>>();
    for (const [key, sid] of assignedStaffByTemplateWeekday.entries()) {
      const wd = Number(key.split(':')[1]);
      if (!assignedByWd.has(wd)) assignedByWd.set(wd, new Set());
      assignedByWd.get(wd)!.add(sid);
    }
    const out = new Map<number, StaffEventFrame[]>();
    for (let wd = 0; wd < 6; wd++) {
      const assigned = assignedByWd.get(wd) ?? new Set<string>();
      const frames: StaffEventFrame[] = [];
      for (const staffId of staffEventsByStaff.keys()) {
        if (assigned.has(staffId)) continue; // コースあり → 既存の列/セル内に出る
        const staff = staffMap.get(staffId);
        if (!staff) continue;
        const events = getStaffEventsForWeekday(staffId, wd, staffEventsByStaff);
        if (events.length > 0) frames.push({ staff, events });
      }
      frames.sort((a, b) => a.staff.name.localeCompare(b.staff.name, 'ja'));
      if (frames.length > 0) out.set(wd, frames);
    }
    return out;
  }, [assignedStaffByTemplateWeekday, staffEventsByStaff, staffMap]);

  // ─── DnD ──────────────────────────────────────────────────────────
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 5 } }),
  );
  const [activePatientId, setActivePatientId] = useState<string | null>(null);
  // タイムラインカードのドラッグ中はカード実寸ゴーストを DragOverlay に出す。
  const [activeTlVisit, setActiveTlVisit] = useState<CourseGridVisit | null>(null);
  // 同住所ペア (2名セット) ドラッグ中のペアボックス実寸ゴースト。
  const [activeTlPairVisits, setActiveTlPairVisits] = useState<CourseGridVisit[] | null>(null);
  // プールカードのゴースト用: renderCard が描画のたびに「draggableId → 表示中の
  // PatientCardData」を記録し、ドラッグ開始時にそのまま流用する (= 掴んだカードと
  // 完全に同じ情報でゴーストを出す。ref への書込は冪等でレンダーに影響しない)。
  const poolCardDataRef = useRef(new Map<string, PatientCardData>());
  const [activePoolCard, setActivePoolCard] = useState<PatientCardData | null>(null);
  const placeAndFixMut = usePlaceAndFix();
  const deleteVisitMut = useDeleteVisit();
  // ─── Wave U-3: 戻る/進む (undo/redo) ────────────────────────────────────
  const opLogStateQuery = useOpLogState(isoYear, isoWeek);
  const opLogState = opLogStateQuery.data;
  const undoMut = useUndoOpLog();
  const redoMut = useRedoOpLog();
  const invalidateOpLog = useInvalidateOpLog();
  // Wave 39: D&D で event を移動 (時刻スライド + 担当者変更) するための mutation.
  const updateEventDragMut = useUpdateEventForDrag();
  // Wave U-2 (D-2 既定B): DnD は「この週だけ」で即時反映し、成功トーストの
  //   「毎週の型にも登録」アクションで昇格させる。昇格は 1 患者の週→型同期。
  //   D&D は動的な患者を対象にするため (hooks 規則上バインド済みの単体版フックは
  //   使えない)、patient_ids 配列を渡せる bulk 版フックを 1 患者で流用する。
  const bulkSyncWeekToFixedMut = useBulkSyncWeekToFixedMutation();
  // 同住所ペアの2名セット移動でも一括昇格できるよう patient_ids 配列を受ける。
  // 週のピン (青ピン / PO 決定 2026-08-08)。
  // 型とズレている訪問は赤ピン (型のピン) が刺せないため、今の位置で守る手段が
  // これしか無い。解除してもその場では動かず、次の週生成で型の時刻が読み込まれる。
  const toggleWeekPinMut = useToggleVisitWeekPin();
  const handleToggleWeekPin = useCallback(
    (visitId: string, nextPinned: boolean) => {
      if (!canEdit) return;
      toggleWeekPinMut.mutate(
        { visitId, pinned: nextPinned },
        {
          onSuccess: () => {
            toast.success(
              nextPinned
                ? '今週この時刻で固定しました（固定訪問スケジュールは変更していません）'
                : '今週の固定を解除しました（次の週生成で固定訪問スケジュールの時刻に戻ります）',
            );
          },
          onError: (e) => {
            toast.error(`今週の固定を変更できませんでした: ${apiErrorMessage(e)}`);
          },
        },
      );
    },
    [canEdit, toggleWeekPinMut],
  );

  const promoteWeekToFixed = useCallback(
    (patientIds: string[], label: string) => {
      bulkSyncWeekToFixedMut.mutate(
        { patient_ids: patientIds, iso_year: isoYear, iso_week: isoWeek, dry_run: false },
        {
          onSuccess: (res) => {
            if (res.transaction_applied) {
              toast.success(`${label} の今週の配置を固定訪問週間（毎週の型）に登録しました`);
            } else {
              toast.warning(`${label} の固定訪問週間への登録は行われませんでした`);
            }
          },
          onError: () => toast.error(`${label} の固定訪問週間への登録に失敗しました`),
        },
      );
    },
    [bulkSyncWeekToFixedMut, isoYear, isoWeek],
  );
  /** Wave U-2: 「この週だけ」配置の成功トーストに付ける昇格アクション。 */
  const promoteToastAction = useCallback(
    (patientIds: string[], label: string) => ({
      label: '毎週の型にも登録',
      onClick: () => promoteWeekToFixed(patientIds, label),
    }),
    [promoteWeekToFixed],
  );

  // ─── Wave U-3: undo/redo ハンドラ ──────────────────────────────────────
  const handleUndo = useCallback(async () => {
    try {
      await undoMut.mutateAsync({ iso_year: isoYear, iso_week: isoWeek });
    } catch (err) {
      // 409 は useUndoOpLog の onError で toast.warning を表示済みなのでスキップ
      if (!(err instanceof ApiError && err.status === 409)) {
        toast.error(`操作の取り消しに失敗しました: ${formatErr(err)}`);
      }
    }
  }, [undoMut, isoYear, isoWeek]);

  const handleRedo = useCallback(async () => {
    try {
      await redoMut.mutateAsync({ iso_year: isoYear, iso_week: isoWeek });
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) {
        toast.error(`操作のやり直しに失敗しました: ${formatErr(err)}`);
      }
    }
  }, [redoMut, isoYear, isoWeek]);

  // ─── Wave U-3: キーボードショートカット (Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z) ──
  useEffect(() => {
    if (!canEdit) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      // 入力要素にフォーカスがあるときは発火しない
      const target = e.target as HTMLElement;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target.isContentEditable
      ) {
        return;
      }
      const undoRedoPending = undoMut.isPending || redoMut.isPending;
      if (e.ctrlKey && !e.shiftKey && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (opLogState?.can_undo && !undoRedoPending) {
          void handleUndo();
        }
      } else if (
        (e.ctrlKey && e.key.toLowerCase() === 'y') ||
        (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'z')
      ) {
        e.preventDefault();
        if (opLogState?.can_redo && !undoRedoPending) {
          void handleRedo();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [canEdit, opLogState, undoMut.isPending, redoMut.isPending, handleUndo, handleRedo]);

  // NG スタッフ / 性別制限 (§7-2) の確認 → acknowledge 再送。移動 / 配置 / 空き枠登録の
  // 3 経路で共用する (同時に 2 つは起きない)。コース担当変更は別 state (constraintConfirm)。
  const placementConstraintConfirm = useConstraintConfirmRetry();

  // ─── T-2 ②-a: 空き枠クリック → 登録モーダル ──────────────────────────
  // タイムラインの空き枠クリックで開く。訪問=place-and-fix (fix_pattern=false =
  // この週だけ・プールDnDと同じ契約/トースト昇格)、会議・イベント=TimelineEventAddDialog
  // (D-1: 全スタッフから複数選択・カイポケ反映外) へ切替。canEdit のときだけ配線する。
  const [slotRegState, setSlotRegState] = useState<{
    col: TimelineCourseColumn;
    gap: FreeGap;
  } | null>(null);
  const [slotEventState, setSlotEventState] = useState<{
    /** 起動元の列の担当 (既定選択)。未割当列からは null = 選択なしで開く。 */
    staffId: string | null;
    date: string;
    startHM: string;
    endHM: string;
  } | null>(null);

  const handleFreeSlotClick = useCallback(
    (col: TimelineCourseColumn, gap: FreeGap) => {
      if (!canEdit) return;
      setSlotRegState({ col, gap });
    },
    [canEdit],
  );

  // NG スタッフ (§8-2 逆引き): 枠の担当を NG 指定している患者を 1 クエリで引く。
  // 患者ごとの ng-staff を N 本引くより安く、`ng_staff_count` では「誰を NG か」が
  // 分からない (この枠の担当が NG かは突合が要る) ため逆引きが正解。
  // 未割当コース (staffId=null) では実行されず、⛔ 注記も出ない。
  const slotStaffId = slotRegState?.col.assignedStaff?.id ?? null;
  const slotNgPatientsQuery = useStaffNgPatients(slotStaffId);
  const slotNgPatientIds = useMemo(
    () => new Set((slotNgPatientsQuery.data ?? []).map((r) => r.patient_id)),
    [slotNgPatientsQuery.data],
  );

  // 配置候補 = プール患者 (不足あり)。2名体制はプールDnD (相方コース選択) に委譲。
  const slotPatientOptions = useMemo<SlotPatientOption[]>(() => {
    if (!slotRegState) return [];
    return poolPatients
      .filter(
        (p) => (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff !== true,
      )
      .map((p) => {
        const wp = (p.weekly_pattern ?? null) as { service_minutes?: number } | null;
        return {
          id: p.id,
          name: p.name,
          // 基本の訪問時間 35 分にフォールバック (PO 決定 2026-08-09。旧 60 分)。
          defaultDurationMin: Math.max(1, Number(wp?.service_minutes ?? 35)),
          shortage: patientShortageById.get(p.id)?.shortage ?? 0,
          // この枠の担当をこの患者が NG 指定しているか (⛔ 注記 + 警告帯の材料)。
          ngWithSlotStaff: slotNgPatientIds.has(p.id),
        };
      });
  }, [slotRegState, poolPatients, patientShortageById, slotNgPatientIds]);

  // acknowledge=true は「NG スタッフ / 性別制限の確認ダイアログで OK した再送」(§7-2)。
  // 自己参照するため関数宣言で書く (useCallback だと自分を呼べない)。
  async function handleSlotRegisterVisit(
    {
      patientId,
      startHM,
      durationMin,
      courseTemplateId,
    }: {
      patientId: string;
      startHM: string;
      durationMin: number;
      /** 再送時は slotRegState が閉じている可能性があるため確定値を持ち回る。 */
      courseTemplateId: string;
    },
    acknowledge = false,
  ) {
    const patient = patientById.get(patientId);
    try {
      const opGroupId = crypto.randomUUID();
      await placeAndFixMut.mutateAsync({
        patient_id: patientId,
        course_template_id: courseTemplateId,
        iso_year: isoYear,
        iso_week: isoWeek,
        weekday: activeWeekday,
        start_time: startHM,
        duration_min: durationMin,
        staff_count: 1,
        fix_pattern: false,
        op_group_id: opGroupId,
        ...ackFlag(acknowledge),
      });
      invalidateOpLog(isoYear, isoWeek);
      const pname = patient?.name ?? patientId;
      toast.success(
        `${pname} を ${startHM} に配置しました（今週のみ・毎週の型は変更していません）`,
        {
          action: promoteToastAction([patientId], pname),
          // ②-c: op-log 記録済みのためトーストから直接 undo できる。
          cancel: { label: '元に戻す', onClick: () => void handleUndo() },
        },
      );
      setSlotRegState(null);
    } catch (err) {
      if (
        !acknowledge &&
        placementConstraintConfirm.capture(
          err,
          () =>
            handleSlotRegisterVisit({ patientId, startHM, durationMin, courseTemplateId }, true),
          PLACE_CONSTRAINT_TEXT,
        )
      ) {
        return;
      }
      toast.error(`配置に失敗しました: ${formatErr(err)}`);
    }
  }

  const handleSlotSwitchToEvent = useCallback(() => {
    if (!slotRegState) return;
    // D-1: 複数スタッフ選択ダイアログになったため、未割当コースからも起動できる
    // (既定選択なしで開く)。担当がいれば既定でチェックしておく。
    setSlotEventState({
      staffId: slotRegState.col.assignedStaff?.id ?? null,
      date: format(addDays(weekStart, activeWeekday), 'yyyy-MM-dd'),
      startHM: fmtHM(slotRegState.gap.startMin),
      endHM: fmtHM(slotRegState.gap.endMin),
    });
    setSlotRegState(null);
  }, [slotRegState, weekStart, activeWeekday]);

  // イベント帯クリック → 既存 EventEditDialog (編集+削除)。帯は担当スタッフの列内表示
  // (全幅帯で「全員に入った」ように誤読された PO 指摘 2026-07-08 の対応)。
  const [tlEventEdit, setTlEventEdit] = useState<{
    staffId: string;
    event: EventRead;
  } | null>(null);

  // イベントをカイポケへ送る (Phase 3・職員スケジュールタブのツールバーから)
  const [sendEventsOpen, setSendEventsOpen] = useState(false);
  // カイポケからイベントのみ取り込む (逆方向・同ツールバー)
  const [importEventsOpen, setImportEventsOpen] = useState(false);

  const handleTimelineEventClick = useCallback(
    (ev: EventRead, col: TimelineCourseColumn) => {
      if (!canEdit) return;
      const staff = col.assignedStaff;
      if (!staff) return;
      setTlEventEdit({ staffId: staff.id, event: ev });
    },
    [canEdit],
  );

  // ─── T-2 ②-b: タイムラインカード DnD → 二択 (この週だけ / 固定パターン) ──
  // week = 既存 visit-move-week-only をそのまま叩く (PFV 不変・op-log 記録・BE ピン422)。
  // pattern = 同じ移動 + 週→型同期 (promoteWeekToFixed = 既存トースト昇格と同義)。
  const visitMoveWeekOnlyMut = useVisitMoveWeekOnly();
  // visits は 1 件 (単独カード) or 2 件 (同住所ペア = 2名セット移動)。
  const [tlMoveState, setTlMoveState] = useState<{
    visits: CourseGridVisit[];
    fromCol: TimelineCourseColumn;
    toCol: TimelineCourseColumn;
    newStartMin: number;
    /** 表示用の占有分 (ペアは90分占有)。 */
    durationMin: number;
  } | null>(null);

  /**
   * 移動の実処理。同住所ペアは 1 件ずつ順に投げるため、
   *   - `fromIndex` = まだ移していない先頭 (= 途中の 1 件が 422 でも既に成功した分を再送しない)
   *   - `acknowledge` = NG スタッフ / 性別制限の確認ダイアログで OK した再送 (§7-2)
   * を持ち回る。既存の「部分成功でも op-log を undo バーへ出す」方針は踏襲。
   * 自己参照するため関数宣言で書く。
   */
  async function applyTlMove(
    st: NonNullable<typeof tlMoveState>,
    scope: ChangeScopeValue,
    opts: { acknowledge?: boolean; fromIndex?: number; opGroupId?: string } = {},
  ) {
    const { acknowledge = false, fromIndex = 0 } = opts;
    if (st.visits.length === 0) return;
    const names = st.visits.map(
      (v) => patientById.get(v.patient_id)?.name ?? v.patient_name ?? v.patient_id,
    );
    const label = st.visits.length >= 2 ? `${names.join('・')}（同住所2名）` : names[0]!;
    const patientIds = st.visits.map((v) => v.patient_id);
    const courseChanged = st.toCol.template.id !== st.fromCol.template.id;
    // 2名セットは同一 op_group_id で連続移動 (undo で両方まとめて戻る)。
    const opGroupId = opts.opGroupId ?? crypto.randomUUID();
    let i = fromIndex;
    try {
      for (; i < st.visits.length; i++) {
        const v = st.visits[i]!;
        await visitMoveWeekOnlyMut.mutateAsync({
          iso_year: isoYear,
          iso_week: isoWeek,
          patient_id: v.patient_id,
          old_weekday: activeWeekday,
          old_start_time: (v.start_time ?? '').slice(0, 5),
          new_weekday: activeWeekday,
          new_start_time: fmtHM(st.newStartMin),
          ...(courseChanged ? { new_course_template_id: st.toCol.template.id } : {}),
          op_group_id: opGroupId,
          ...ackFlag(acknowledge),
        });
      }
      invalidateOpLog(isoYear, isoWeek);
      if (scope === 'pattern') {
        // 週→型同期の成功/失敗トーストは promoteWeekToFixed 側が出す。
        toast.success(`${label} を ${fmtHM(st.newStartMin)} に移動しました`);
        promoteWeekToFixed(patientIds, label);
      } else {
        toast.success(
          `${label} を ${fmtHM(st.newStartMin)} に移動しました（今週のみ・毎週の型は変更していません）`,
          {
            action: promoteToastAction(patientIds, label),
            // ②-c: op-log 記録済みのためトーストから直接 undo できる。
            cancel: { label: '元に戻す', onClick: () => void handleUndo() },
          },
        );
      }
      setTlMoveState(null);
    } catch (err) {
      // ペアの2件目で失敗した場合も、1件目の op-log を undo バーへ即時反映する
      // (レビューMED対応。Ctrl+Z で復旧可能)。
      invalidateOpLog(isoYear, isoWeek);
      // NG スタッフ / 性別制限の 422 は「確認して通す」フローへ (ブロックではない)。
      // 失敗した i 件目から ack 付きで再開する (成功済みは二重移動しない)。
      const failedIndex = i;
      if (
        !acknowledge &&
        placementConstraintConfirm.capture(
          err,
          () =>
            applyTlMove(st, scope, {
              acknowledge: true,
              fromIndex: failedIndex,
              opGroupId,
            }),
          MOVE_CONSTRAINT_TEXT,
        )
      ) {
        return;
      }
      toast.error(`移動に失敗しました: ${formatErr(err)}`);
    }
  }

  const handleTlMoveConfirm = async (scope: ChangeScopeValue) => {
    const st = tlMoveState;
    if (!st) return;
    await applyTlMove(st, scope);
  };

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
    // プールカード: 表示中と同一のデータでカード実寸ゴーストを出す (情報を落とさない)。
    setActivePoolCard(poolCardDataRef.current.get(id) ?? null);
    // T-2 ②-b改: タイムラインカードはカード実寸ゴースト (TlVisitDragGhost) を出すため
    // visit オブジェクトごと保持する (時間=面積のままドラッグ・A-1 PO要望)。
    const tlId = parseTlVisitDraggableId(id);
    if (tlId) {
      let found: CourseGridVisit | null = null;
      for (const c of timelineColumns) {
        const v = c.visits.find((x) => x.id === tlId);
        if (v) {
          found = v;
          break;
        }
      }
      setActiveTlVisit(found);
    } else {
      setActiveTlVisit(null);
    }
    // 同住所ペア (2名セット) のドラッグはペアボックス実寸ゴースト。
    const pairIds = parseTlPairDraggableId(id);
    if (pairIds) {
      const col = timelineColumns.find((c) => c.visits.some((v) => v.id === pairIds[0]));
      const vs = pairIds
        .map((vid) => col?.visits.find((v) => v.id === vid))
        .filter((v): v is CourseGridVisit => v != null);
      setActiveTlPairVisits(vs.length === 2 ? vs : null);
    } else {
      setActiveTlPairVisits(null);
    }
  };

  /**
   * DnD の受け口 (Phase 2 でテーブル DnD 撤去後の全経路):
   *   - pool-patient → tl-col: (T-6 パリティ①) スナップ位置から仮想セルを合成して
   *                             配置フローへ流す (2名体制=相方選択ダイアログも同一)。
   *   - tl-visit / tl-pair → tl-col:  15 分スナップ移動 (delete + place-and-fix)。
   *   - tl-visit / tl-pair → pool:    visit を delete (= プールに戻る)。
   *   - event → tl-col:        (T-6 パリティ②) 仮想セル合成 → 時刻スライド + 担当者変更。
   *
   * NOTE: 旧テーブル由来の id 名前空間 (`visit:` / `course-day-cell:`) は
   *       もはやどのコンポーネントも生成しないため、該当ブランチは削除済み。
   *       仮想セル ({weekday, courseTemplateId, time}) の合成は残す。
   */
  const handleDragEnd = async (e: DragEndEvent) => {
    setActivePatientId(null);
    setActiveTlVisit(null);
    setActiveTlPairVisits(null);
    setActivePoolCard(null);
    const { active, over } = e;
    if (!over) return;
    const activeId = String(active.id);
    const overId = String(over.id);

    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }

    const patientId = parsePatientDraggableId(activeId);
    const eventId = parseEventDraggableId(activeId);
    const isPoolDrop = overId === POOL_DROPPABLE_ID;

    // ─── T-2 ②-b: タイムラインカード → 列 (連続時間軸の15分スナップ移動) ───
    // ドロップ位置 = カードの translated top − 列 rect top → snapYOffsetToMinutes。
    const tlVisitId = parseTlVisitDraggableId(activeId);
    const tlPairIds = parseTlPairDraggableId(activeId);
    if (tlVisitId || tlPairIds) {
      const wantIds = tlPairIds ?? [tlVisitId!];

      // ─── G4: タイムラインカード → プール (訪問を外す) ───────────────────
      // 既存テーブルの visit→プールと同じ semantics: delete のみ (cascade=false =
      // 固定枠は保持)。同住所ペア (tl-pair) は 2 件とも外す (同一 op_group_id で
      // 1 操作 = undo でまとめて戻る。tlMove の作法に倣う)。
      if (isPoolDrop) {
        const srcCol = timelineColumns.find((c) => c.visits.some((v) => v.id === wantIds[0]));
        const poolVisits = wantIds
          .map((id) => srcCol?.visits.find((v) => v.id === id))
          .filter((v): v is CourseGridVisit => v != null);
        if (poolVisits.length !== wantIds.length) return;
        // 2 名体制 (visit_group_id) は BE の cascade_partner で相方まで消えるため禁止
        // (既存テーブル visit→プールと同じガード)。
        if (poolVisits.some((v) => Boolean(v.visit_group_id))) {
          toast.warning(
            '2 名体制 (ペア配置済) の visit はプールへ戻せません / 別セルへ移動できません。× ボタンで一括削除してから再配置してください。',
          );
          return;
        }
        const names = poolVisits.map(
          (v) => patientById.get(v.patient_id)?.name ?? v.patient_name ?? v.patient_id,
        );
        const label = poolVisits.length >= 2 ? `${names.join('・')}（同住所2名）` : names[0]!;
        // 赤 (完全固定) は注意書きを見せたうえで人手操作を許す (PO 決定 2026-08-09)。
        if (poolVisits.some((v) => v.is_pinned === true)) {
          if (
            !window.confirm(
              `⚠ これは完全固定です。\n${label} をプールに戻しますか？\n(固定枠は保持されます)`,
            )
          )
            return;
        }
        // catch から参照するため try の外で数える (部分成功の件数を toast に出す)。
        let doneCount = 0;
        try {
          const opGroupId = crypto.randomUUID();
          for (const v of poolVisits) {
            await deleteVisitMut.mutateAsync({
              id: v.id,
              cascadeFixedVisit: false,
              op_group_id: opGroupId,
            });
            doneCount += 1;
          }
          invalidateOpLog(isoYear, isoWeek);
          toast.success(`${label} をプールに戻しました`);
        } catch (err) {
          // ペアの 2 件目で失敗しても 1 件目の op-log を undo バーへ即時反映する。
          invalidateOpLog(isoYear, isoWeek);
          // 部分成功を隠さない: 「何も起きていない」と誤読して undo を試さないのを防ぐ
          // (レビューMED)。doneCount は catch までに成功した削除件数。
          toast.error(
            doneCount > 0
              ? `プールへの戻しが途中で失敗しました（${doneCount}件は削除済み）。取り消すには「元に戻す」を使ってください: ${formatErr(err)}`
              : `プールへの戻しに失敗しました: ${formatErr(err)}`,
          );
        }
        return;
      }

      const colKey = parseTlColDroppableId(overId);
      if (!colKey) return; // タイムライン外へのドロップは無効 (何もしない)
      const toCol = timelineColumns.find((c) => c.key === colKey);
      const fromCol = timelineColumns.find((c) => c.visits.some((v) => v.id === wantIds[0]));
      const visits = wantIds
        .map((id) => fromCol?.visits.find((v) => v.id === id))
        .filter((v): v is CourseGridVisit => v != null);
      if (!toCol || !fromCol || visits.length !== wantIds.length) return;
      const oldStartMin = parseHM(visits[0]!.start_time);
      if (oldStartMin === null) return;
      // 占有分: 単独=実所要 / 同住所ペア=90分占有 (実所要が超えるならそちら)。
      let occupancyMin = 0;
      for (const v of visits) {
        const e = parseHM(v.end_time);
        if (e === null || e <= oldStartMin) return;
        occupancyMin = Math.max(occupancyMin, e - oldStartMin);
      }
      if (visits.length >= 2) {
        occupancyMin = Math.max(occupancyMin, SAME_ADDRESS_PAIR_MIN_OCCUPANCY);
      }
      const translatedTop = active.rect.current.translated?.top ?? null;
      const overTop = over.rect?.top ?? null;
      if (translatedTop === null || overTop === null) return;
      const newStartMin = snapYOffsetToMinutes(translatedTop - overTop);
      // 置けない場所 = 営業時間レンジ外 (ペアは90分占有ぶんで判定)。
      if (newStartMin < TL_DAY_START_MIN || newStartMin + occupancyMin > TL_DAY_END_MIN) {
        toast.warning('この位置には置けません（9:00〜18:00 の範囲に収まるように移動してください）');
        return;
      }
      // 同一コース・同時刻へのドロップは noop。
      if (toCol.key === fromCol.key && newStartMin === oldStartMin) return;
      setTlMoveState({ visits, fromCol, toCol, newStartMin, durationMin: occupancyMin });
      return;
    }

    // ─── T-6 パリティ②: イベント帯 → タイムライン列 (連続時間軸の15分スナップ移動) ───
    // 旧テーブルセルと同じ移動フロー (案X 同曜日 / 案Q 担当者変更 / 案K 衝突拒否) を
    // 流用するため、スナップ位置から仮想セルを合成して下の分岐へ流す (パリティ①と同型)。
    let eventCell: DropCell | null = null;
    if (eventId) {
      const colKey = parseTlColDroppableId(overId);
      const toCol = colKey ? timelineColumns.find((c) => c.key === colKey) : undefined;
      const translatedTop = active.rect.current.translated?.top ?? null;
      const overTop = over.rect?.top ?? null;
      if (toCol && translatedTop !== null && overTop !== null) {
        const startMin = snapYOffsetToMinutes(translatedTop - overTop);
        const entry = eventById.get(eventId);
        const evDur = entry
          ? Math.max(
              (toMinutes(entry.event.end_time) ?? 0) - (toMinutes(entry.event.start_time) ?? 0),
              0,
            )
          : 0;
        if (startMin < TL_DAY_START_MIN || startMin + evDur > TL_DAY_END_MIN) {
          toast.warning(
            'この位置には置けません（9:00〜18:00 の範囲に収まるように移動してください）',
          );
          return;
        }
        eventCell = {
          weekday: activeWeekday,
          courseTemplateId: toCol.template.id,
          time: formatHHMM(startMin),
        };
      }
    }

    // ─── Wave 39: スタッフイベント drop (時刻スライド + 担当者変更) ───
    // 案 X (同曜日内のみ) + 案 Q (drop 先 course の assigned_staff_id を新所有者に)
    // + 案 K (衝突時 rollback / 移動禁止) を実装する。
    if (eventId && eventCell) {
      const cell = eventCell; // 以下は旧テーブルセルと共通のイベント移動フロー。
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

    // ─── プール患者 → タイムライン列 ───────────────────────────────
    // Wave 37 Phase 3-C: patient.requires_multiple_staff=true なら相方コース
    //   選択ダイアログを開き、確定後に staff_count=2 で place-and-fix を呼ぶ。
    //   従来通常患者 (false) は staff_count=1 + course_template_id (旧形式) で呼ぶ。
    // ─── T-6 パリティ①: プールカード → タイムライン列 (連続時間軸の15分スナップ配置) ───
    // 旧テーブルセル (course-day-cell) と完全に同じ配置フローを流用するため、ドロップの
    // スナップ位置から仮想セル {weekday, courseTemplateId, time} を合成して下の分岐へ流す。
    // これで 2名体制=相方コース選択ダイアログ / 通常=この週だけ place-and-fix +
    // 昇格トースト + undo、が旧テーブルと同一挙動になる。
    let poolCell: DropCell | null = null;
    if (patientId) {
      const colKey = parseTlColDroppableId(overId);
      const toCol = colKey ? timelineColumns.find((c) => c.key === colKey) : undefined;
      const translatedTop = active.rect.current.translated?.top ?? null;
      const overTop = over.rect?.top ?? null;
      if (toCol && translatedTop !== null && overTop !== null) {
        const startMin = snapYOffsetToMinutes(translatedTop - overTop);
        const patient = patientById.get(patientId);
        const wp = (patient?.weekly_pattern ?? null) as { service_minutes?: number } | null;
        const durationMin = Math.max(1, Number(wp?.service_minutes ?? 60));
        if (startMin < TL_DAY_START_MIN || startMin + durationMin > TL_DAY_END_MIN) {
          toast.warning(
            'この位置には置けません（9:00〜18:00 の範囲に収まるように配置してください）',
          );
          return;
        }
        poolCell = {
          weekday: activeWeekday,
          courseTemplateId: toCol.template.id,
          time: formatHHMM(startMin),
        };
      }
    }

    if (patientId && poolCell) {
      const cell = poolCell; // 以下は旧テーブルセルと共通の配置フロー。
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

      // 通常患者 (1 名体制): Wave U-2 D-2 既定B = この週だけ配置 (fix_pattern=false)。
      // 型は変えず source=manual_week で今週のみ置く。トーストで昇格導線を出す。
      await applyPoolDrop(patientId, cell, durationMin);
      return;
    }
  };

  /**
   * プールカード → 列 の 1 名体制配置 (place-and-fix)。
   * acknowledge=true は NG スタッフ / 性別制限の確認ダイアログで OK した再送 (§7-2)。
   */
  async function applyPoolDrop(
    patientId: string,
    cell: DropCell,
    durationMin: number,
    acknowledge = false,
  ) {
    const patient = patientById.get(patientId);
    try {
      const opGroupId = crypto.randomUUID();
      await placeAndFixMut.mutateAsync({
        patient_id: patientId,
        course_template_id: cell.courseTemplateId,
        iso_year: isoYear,
        iso_week: isoWeek,
        weekday: cell.weekday,
        start_time: cell.time,
        duration_min: durationMin,
        staff_count: 1,
        fix_pattern: false,
        op_group_id: opGroupId,
        ...ackFlag(acknowledge),
      });
      invalidateOpLog(isoYear, isoWeek);
      const pname = patient?.name ?? patientId;
      toast.success(
        `${pname} を ${cell.time} に配置しました（今週のみ・毎週の型は変更していません）`,
        {
          action: promoteToastAction([patientId], pname),
        },
      );
    } catch (err) {
      if (
        !acknowledge &&
        placementConstraintConfirm.capture(
          err,
          () => applyPoolDrop(patientId, cell, durationMin, true),
          PLACE_CONSTRAINT_TEXT,
        )
      ) {
        return;
      }
      toast.error(`配置に失敗しました: ${formatErr(err)}`);
    }
  }

  // ─── Wave 37 Phase 3-C: 相方コース確定ハンドラ ─────────────────────
  // ダイアログで 2 つ目の course_template_id を確定したら、staff_count=2 で
  // place-and-fix を呼び出す。BE Phase 2-A が 2 visit を visit_group_id 共有で作成。
  const handlePartnerConfirm = async (secondaryTemplateId: string) => {
    const ds = partnerDialogState;
    if (!ds) return;
    closePartnerDialog();
    await applyPartnerPlace(ds, secondaryTemplateId);
  };

  /** 2 名体制の配置本体。acknowledge=true は §7-2 の確認ダイアログ経由の再送。 */
  async function applyPartnerPlace(
    ds: NonNullable<typeof partnerDialogState>,
    secondaryTemplateId: string,
    acknowledge = false,
  ) {
    const patient = patientById.get(ds.patientId);
    try {
      const opGroupId = crypto.randomUUID();
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
        // Wave U-2 D-2 既定B: この週だけ配置 (fix_pattern=false)。昇格は 1 患者の
        // 週→型同期で両 slot がまとめて上がる (bulk-sync は patient 単位)。
        fix_pattern: false,
        op_group_id: opGroupId,
        ...ackFlag(acknowledge),
      });
      invalidateOpLog(isoYear, isoWeek);
      const pname = patient?.name ?? ds.patientId;
      toast.success(
        `${pname} を ${ds.time} に 2 名体制で配置しました（今週のみ・毎週の型は変更していません）`,
        {
          action: promoteToastAction([ds.patientId], pname),
        },
      );
    } catch (err) {
      if (
        !acknowledge &&
        placementConstraintConfirm.capture(
          err,
          () => applyPartnerPlace(ds, secondaryTemplateId, true),
          PLACE_CONSTRAINT_TEXT,
        )
      ) {
        return;
      }
      toast.error(`2 名体制配置に失敗しました: ${formatErr(err)}`);
    }
  }

  // ─── Wave 36: visit × ボタン削除ハンドラ ────────────────────────
  const handleDeleteVisit = async (visitId: string, patientName: string) => {
    const targetVisit = timelineColumns.flatMap((c) => c.visits).find((x) => x.id === visitId);
    // 青 (今週固定) は蓋 = 削除不可 (ボタン側でも防ぐが二重防御。BE も 422)。
    if (targetVisit?.week_pinned === true) {
      toast.warning('今週固定（青ピン）されています。解除してから削除してください');
      return;
    }
    // 赤 (完全固定) は注意書きを見せたうえで人手削除を許す (PO 決定 2026-08-09)。
    const lockedNote = targetVisit?.is_pinned === true ? '\n⚠ これは完全固定です。' : '';
    if (
      !window.confirm(`${patientName} の訪問を削除しますか？${lockedNote}\n(固定枠は保持されます)`)
    )
      return;
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
    // W-5b: 直行フラグの取り残し防止 (非プール経路では常に自動展開しない)。
    setPatientDetailAutoOvercapacity(false);
    // W-14: 詰まり解消の自動探索フラグも非プール経路ではリセットする。
    setPatientDetailAutoUnblock(false);
    setPatientDetailId(pid);
  }, []);
  // 保留プールの患者カードクリック専用. プール投入提案セクションを有効化して開く.
  // W-5b: 定員超過候補まで自動展開するか (BulkPoolInsertDialog done 画面からの直行専用).
  const [patientDetailAutoOvercapacity, setPatientDetailAutoOvercapacity] = useState(false);
  // W-14: 詰まり解消探索を自動発火するか (同 done 画面「ずらせば入る手を探せます」導線).
  const [patientDetailAutoUnblock, setPatientDetailAutoUnblock] = useState(false);
  const handleOpenPoolPatientDetail = useCallback(
    (pid: string, opts?: { autoOvercapacity?: boolean; autoUnblock?: boolean }) => {
      setPatientDetailPoolMode(true);
      setPatientDetailAutoOvercapacity(opts?.autoOvercapacity ?? false);
      setPatientDetailAutoUnblock(opts?.autoUnblock ?? false);
      setPatientDetailId(pid);
    },
    [],
  );
  const handleClosePatientDetail = useCallback(() => {
    setPatientDetailId(null);
    setPatientDetailPoolMode(false);
    setPatientDetailAutoOvercapacity(false);
    setPatientDetailAutoUnblock(false);
  }, []);

  // 案1融合 (PO確定 2026-08-09): 患者マスタの「空き提案を見る」からの deep link。
  // /schedule?proposePatient={id} で開くと、その患者の提案ダイアログ (プール投入
  // 提案セクション有効) を直接開く。1 回だけ発火し、URL からは消す (戻る対策)。
  const proposeHandledRef = useRef(false);
  useEffect(() => {
    if (proposeHandledRef.current) return;
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const pid = params.get('proposePatient');
    if (!pid) return;
    proposeHandledRef.current = true;
    handleOpenPoolPatientDetail(pid);
    params.delete('proposePatient');
    const qs = params.toString();
    window.history.replaceState(null, '', window.location.pathname + (qs ? `?${qs}` : ''));
  }, [handleOpenPoolPatientDetail]);

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
                nextPinned ? '完全固定にしました (システムは動かしません)' : '完全固定を外しました',
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
        toast.info(
          nextPinned ? '既に全曜日 完全固定の状態です' : '既に全曜日 完全固定なしの状態です',
        );
        return;
      }
      const items = needUpdate.map((p) => ({ pfv_id: p.id, is_pinned: nextPinned }));
      bulkPinPfvs.mutate(items, {
        onSuccess: () => {
          toast.success(
            nextPinned
              ? `全曜日 ${items.length} 件を完全固定にしました (システムは動かしません)`
              : `全曜日 ${items.length} 件の完全固定を外しました`,
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

  // ─── Phase G-41 起源: 主要ボタン群を本 panel Row 1 に再収容 (現在は 週生成/新規患者登録/診断/最適化/週次ガイド) ───
  //   page 側 (Card 1) に置いた G-40 構成から戻し、 mutation/state/dialog を全部 panel 内で抱える.
  //   pending 中の `isProcessing` は二次操作 (固定枠戻 / 一斉未割当) の多重実行抑止にも利用する.
  const generateWeekMut = useGenerateWeekOnly();
  const assignStaffOnlyMut = useAssignStaffOnly();
  // Phase G-91: 確認レビューフローのダイアログ (連続 / 性別).
  const [assignWarningOpen, setAssignWarningOpen] = useState(false);
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [reviewApplying, setReviewApplying] = useState(false);
  // Wave N-2: 体制上不可避な連続のお知らせ (自動確定済み).
  const [autoCommittedNotices, setAutoCommittedNotices] = useState<AutoCommittedNotice[]>([]);
  // W-11: 性別制約を満たす候補ゼロで残った違反の警告 (手動調整が必要・承認不可).
  const [unresolvedWarnings, setUnresolvedWarnings] = useState<UnresolvedGenderWarning[]>([]);
  // NG スタッフ: NG 候補ゼロで残った違反 / 2 名体制 secondary の制約違反 (承認不可).
  const [unresolvedNgWarnings, setUnresolvedNgWarnings] = useState<UnresolvedNgWarning[]>([]);
  const [secondaryConstraintWarnings, setSecondaryConstraintWarnings] = useState<
    SecondaryConstraintWarning[]
  >([]);
  // 4段ソルバ Stage 2: マネージャー動員のお知らせ (確定済み).
  const [managerMobilizedNotices, setManagerMobilizedNotices] = useState<StageAssignmentNotice[]>(
    [],
  );
  // v2.0 新Stage 3: 拠点をまたぐ応援の警告 (確定済み).
  const [crossOfficeNotices, setCrossOfficeNotices] = useState<CrossOfficeNotice[]>([]);
  // v2.0 新Stage 3: 応援による入れ替えの報告 (確定済み).
  const [rescueSwapNotices, setRescueSwapNotices] = useState<RescueSwapNotice[]>([]);
  // プール一括投入ダイアログ (W-2). PoolOverviewPane の「一括投入」ボタンから開く.
  const [bulkPoolInsertOpen, setBulkPoolInsertOpen] = useState(false);
  // スケジュール健康診断ダイアログ (Schedule Advisor Phase 1).
  const [scheduleHealthOpen, setScheduleHealthOpen] = useState(false);
  // 範囲最適化ダイアログ (scope-optimization W1-W2).
  const [scopeOptimizeOpen, setScopeOptimizeOpen] = useState(false);
  // 健康診断からの導線用の範囲プリセット (ツールバーから開くときは null = 手動選択).
  const [scopeOptimizeInitialScope, setScopeOptimizeInitialScope] = useState<{
    weekdays: number[] | null;
    courseCodes: string[] | null;
  } | null>(null);
  // 健康診断の行が属する拠点 (全拠点表示でも対策計算へ引き継ぐ。処方箋フロー).
  const [scopeOptimizeInitialOfficeId, setScopeOptimizeInitialOfficeId] = useState<string | null>(
    null,
  );
  // P3-⑥: 週次ガイドダイアログ (案内のみ・実行ボタンなし).
  const [weeklyRitualGuideOpen, setWeeklyRitualGuideOpen] = useState(false);
  // PO 2026-07-10: 既に生成済みの週へ「週を生成」を誤って再実行すると不都合
  // (実施済み訪問がある週では 500 になる既知バグもある)。当週に訪問が実在する場合は
  // 即実行せず確認ダイアログを挟む。訪問 0 件の週は従来どおり即実行 (挙動不変)。
  const [generateWeekConfirmOpen, setGenerateWeekConfirmOpen] = useState(false);
  const isProcessing = generateWeekMut.isPending || assignStaffOnlyMut.isPending;

  // 週生成の実処理 (mutation)。即実行と確認ダイアログの「再実行する」の両方から呼ぶ。
  const runGenerateWeek = async () => {
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

  const handleGenerateWeek = () => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return;
    }
    // 既に訪問がある週は再生成の誤操作対策として確認ダイアログを出す。
    if (weekVisits.length > 0) {
      setGenerateWeekConfirmOpen(true);
      return;
    }
    void runGenerateWeek();
  };

  const handleConfirmGenerateWeek = async () => {
    setGenerateWeekConfirmOpen(false);
    await runGenerateWeek();
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
      // Wave N-2 / W-11 / 4段ソルバ v2.0: 確認レビューフロー＋お知らせ＋残留違反＋Stage通知を統合処理。
      //   - review_items (要承認) / auto_committed_notices (確定済みお知らせ) /
      //     unresolved_warnings (性別候補ゼロの残留違反・要手動調整) /
      //     manager_mobilized_notices (Stage 2 動員) /
      //     cross_office_notices (新Stage 3 拠点跨ぎ救援・警告) /
      //     rescue_swap_notices (新Stage 3 入れ替え報告) のいずれかが
      //     1 件以上あればダイアログを開く。
      //   - toast:
      //       review あり                   → warning (不可避/残留/Stage件数を追記)
      //       review 0 + notices等あり      → warning (「確認してください」で誤誘導しない)
      //       すべて 0                      → success のみ (従来どおり)
      const items = res.review_items ?? [];
      const notices = res.auto_committed_notices ?? [];
      const unresolved = res.unresolved_warnings ?? [];
      const mobilized = res.manager_mobilized_notices ?? [];
      const crossOffice = res.cross_office_notices ?? [];
      const swaps = res.rescue_swap_notices ?? [];
      // NG スタッフ (patient-ng-staff-design.md §5): NG 残留 / secondary 制約違反.
      const unresolvedNg = res.unresolved_ng_warnings ?? [];
      const secondaryConstraints = res.secondary_constraint_warnings ?? [];
      if (
        items.length > 0 ||
        notices.length > 0 ||
        unresolved.length > 0 ||
        unresolvedNg.length > 0 ||
        secondaryConstraints.length > 0 ||
        mobilized.length > 0 ||
        crossOffice.length > 0 ||
        swaps.length > 0
      ) {
        setReviewItems(items);
        setAutoCommittedNotices(notices);
        setUnresolvedWarnings(unresolved);
        setUnresolvedNgWarnings(unresolvedNg);
        setSecondaryConstraintWarnings(secondaryConstraints);
        setManagerMobilizedNotices(mobilized);
        setCrossOfficeNotices(crossOffice);
        setRescueSwapNotices(swaps);
        setAssignWarningOpen(true);
        if (items.length > 0) {
          const suffixParts: string[] = [];
          if (notices.length > 0)
            suffixParts.push(`体制上不可避の連続 ${notices.length} 件は確定済み`);
          if (unresolved.length > 0)
            suffixParts.push(`性別制約を満たせない残留 ${unresolved.length} 件`);
          if (unresolvedNg.length > 0)
            suffixParts.push(`NGスタッフを避けられない残留 ${unresolvedNg.length} 件`);
          if (secondaryConstraints.length > 0)
            suffixParts.push(`2名体制の2人目未確定 ${secondaryConstraints.length} 件`);
          if (mobilized.length > 0)
            suffixParts.push(`マネージャー動員 ${mobilized.length} 件確定済み`);
          if (crossOffice.length > 0)
            suffixParts.push(`拠点をまたぐ応援 ${crossOffice.length} 件確定済み`);
          if (swaps.length > 0) suffixParts.push(`入れ替え ${swaps.length} 件`);
          const suffix = suffixParts.length > 0 ? `（うち${suffixParts.join('・')}）` : '';
          toast.warning(
            `自動スタッフ割当が完了しました (確定 ${res.courses_assigned} 件)。` +
              `レビューが必要なコースが ${items.length} 件あります。${suffix}`,
          );
        } else {
          // review は 0 だが notices / 残留違反 / Stage 通知があるため「問題なし」に見せない。
          const parts: string[] = [];
          if (notices.length > 0)
            parts.push(`体制上不可避の連続 ${notices.length} 件を自動確定しました`);
          if (unresolved.length > 0)
            parts.push(`性別制約を満たせない残留が ${unresolved.length} 件あります`);
          if (unresolvedNg.length > 0)
            parts.push(`NGスタッフを避けられない残留が ${unresolvedNg.length} 件あります`);
          if (secondaryConstraints.length > 0)
            parts.push(
              `2名体制の2人目が性別制限やNGスタッフに該当しているコースが ${secondaryConstraints.length} 件あります`,
            );
          if (mobilized.length > 0)
            parts.push(`マネージャー動員 ${mobilized.length} 件を自動確定しました`);
          if (crossOffice.length > 0)
            parts.push(`拠点をまたぐ応援 ${crossOffice.length} 件を自動確定しました`);
          if (swaps.length > 0) parts.push(`応援による入れ替えが ${swaps.length} 件あります`);
          toast.warning(
            `自動スタッフ割当が完了しました (確定 ${res.courses_assigned} 件)。` +
              `${parts.join('。')}。内容をご確認ください。`,
          );
        }
      } else {
        toast.success(`自動スタッフ割当が完了しました (確定 ${res.courses_assigned} 件)`);
      }
    } catch (err) {
      toast.error(`自動スタッフ割当に失敗しました: ${formatErr(err)}`);
    }
  };

  // ─── 担当 dropdown 変更 (PATCH /courses/{id}) ───────────────────
  const updateCourseMut = useUpdateCourse();

  // Phase G-91 (修正1): レビュー承認カードを apply する (= 専用 endpoint 1 回呼び出し).
  // 従来の PATCH /courses ループ (assigned_staff_id のみ) を廃し、
  // POST /apply-staff-review を 1 回呼ぶ。 BE が自動割付と同一の _persist 経由で
  // VSA INSERT / course_status / primary・secondary 同期 / 2 名体制 を全て反映する
  // (= apply 済コースの visit が未割当表示になるリグレッションを解消)。
  // 旧 trainee companion 注入は新人同行 Phase 2 で撤去済み。
  // 監査はミドルウェアが POST を自動記録する。
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
          // NG スタッフ: BE の管理者お知らせ (§7-3) 判定用に理由を同送 (旧 BE は無視).
          reason: a.reason,
          also_violates: a.also_violates,
        })),
      });
      // 承認した course のうち成功したものを抽出 (= partner 自動補完分は無視)。
      const approvedIds = new Set(approvedList.map((a) => a.course_id));
      const succeededIds = new Set(
        res.results.filter((r) => r.ok && approvedIds.has(r.course_id)).map((r) => r.course_id),
      );
      const failedCount = approvedList.length - succeededIds.size;
      if (failedCount === 0) {
        toast.success(`レビュー内容を割り当てました (${approvedList.length} 件)`);
        setAssignWarningOpen(false);
        setReviewItems([]);
        setAutoCommittedNotices([]);
        setUnresolvedWarnings([]);
      } else {
        // 成功した course のみ reviewItems から除去する (= 失敗分 + 未承認分は残す)。
        // 未承認カードを誤って消さないよう、 succeeded だけを取り除く。
        setReviewItems((prev) => prev.filter((i) => !succeededIds.has(i.course_id)));
        toast.error(
          `一部の割り当てに失敗しました (成功 ${succeededIds.size} / 失敗 ${failedCount})`,
        );
      }
    } catch (err) {
      toast.error(`割り当てに失敗しました: ${formatErr(err)}`);
    } finally {
      setReviewApplying(false);
    }
  };

  // NG スタッフ / 性別制限に抵触する担当変更の確認ダイアログ (§7-2 acknowledge 方式).
  // BE が 422 `constraint_confirmation_required` を返したら、 内容を提示して
  // OK なら acknowledge_constraint_warnings: true を足して同じ PATCH を再送する。
  const [constraintConfirm, setConstraintConfirm] = useState<{
    courseId: string;
    staffId: string | null;
    warnings: ConstraintWarning[];
  } | null>(null);

  // 戻り値 (週空間 A1): 反映が成功したら true。422確認フローへ回った/失敗は false
  // (呼び出し側が「休みの日に貼った」警告トースト等を成功時だけ出すために使う)。
  const handleChangeAssignedStaff = async (
    courseId: string,
    staffId: string | null,
    acknowledge = false,
  ): Promise<boolean> => {
    if (!canEdit) {
      toast.warning('編集権限がありません');
      return false;
    }
    try {
      const opGroupId = crypto.randomUUID();
      await updateCourseMut.mutateAsync({
        id: courseId,
        patch: {
          assigned_staff_id: staffId,
          op_group_id: opGroupId,
          ...(acknowledge ? { acknowledge_constraint_warnings: true } : {}),
        },
      });
      invalidateOpLog(isoYear, isoWeek);
      setConstraintConfirm(null);
      toast.success(staffId ? '担当を更新しました' : '担当を未割当にしました');
      return true;
    } catch (err) {
      // 422 + 構造化 detail なら「確認して通す」フローへ (ブロックではない)。
      const detail =
        err instanceof ApiError && err.status === 422
          ? parseConstraintConfirmationDetail(err.body)
          : null;
      if (detail && !acknowledge) {
        setConstraintConfirm({ courseId, staffId, warnings: detail.warnings });
        return false;
      }
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      setConstraintConfirm(null);
      toast.error(`担当の更新に失敗しました: ${msg}`);
      return false;
    }
  };

  // ─── 週空間 A1 (weekly-space-design.md §4): コースパレット + 貼り付け DnD ───

  // ドラッグ中 payload (盤面のドロップ可能セルのハイライト用)。
  const [courseDrag, setCourseDrag] = useState<CourseDragPayload | null>(null);

  // 当該週の全スタッフ休み/時間変更 (admin のみ取得可・セル網掛け + 貼り付け警告)。
  const weekOverridesQuery = useWeekStaffOverrides(isoYear, isoWeek, canEdit);
  const offByStaffWeekday = useMemo(() => {
    // 1 セル 1 件前提: DB は UNIQUE(staff, iso_year, iso_week, weekday) で
    // 同日複数 override を許さない (models/staff.py)。この制約を緩める場合は
    // 値を配列化してバッジ/警告も複数表示に変えること (レビュー指摘)。
    const m = new Map<string, WeekOverrideRead>();
    for (const o of weekOverridesQuery.data ?? []) {
      m.set(`${o.staff_id}:${o.weekday}`, o);
    }
    return m;
  }, [weekOverridesQuery.data]);

  // `${templateId}:${weekday}` → course_id (assignedStaffByTemplateWeekday と同じ
  // 突合ロジック・未割当コースも含む)。盤面のコース帯チップのドラッグ元解決に使う。
  const courseIdByTemplateWeekday = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of courses) {
      const tpl = templates.find(
        (t) =>
          t.office_id === c.office_id &&
          (t.label || '').trim().slice(0, 1).toUpperCase() === String(c.code).toUpperCase(),
      );
      if (tpl) m.set(`${tpl.id}:${c.weekday}`, c.id);
    }
    return m;
  }, [courses, templates]);

  // パレット表示用のコースカード (件数・合計分・時間帯は当週 visits から算出)。
  const paletteCourses = useMemo<PaletteCourse[]>(() => {
    const statsByCourseId = new Map<
      string,
      { count: number; minutes: number; first: string | null; last: string | null }
    >();
    for (const v of weekVisits) {
      const cid = v.course_id ?? null;
      if (!cid) continue;
      let s = statsByCourseId.get(cid);
      if (!s) {
        s = { count: 0, minutes: 0, first: null, last: null };
        statsByCourseId.set(cid, s);
      }
      s.count += 1;
      const st = (v.start_time ?? '').slice(0, 5);
      const en = (v.end_time ?? '').slice(0, 5);
      if (st && en) {
        const mins =
          (Number(en.slice(0, 2)) - Number(st.slice(0, 2))) * 60 +
          (Number(en.slice(3, 5)) - Number(st.slice(3, 5)));
        if (Number.isFinite(mins) && mins > 0) s.minutes += mins;
      }
      if (st && (!s.first || st < s.first)) s.first = st;
      if (en && (!s.last || en > s.last)) s.last = en;
    }
    return courses
      .filter((c) => c.weekday >= 0 && c.weekday <= 5)
      .map((c) => {
        const officeName = officeNameById.get(c.office_id) ?? '';
        const s = statsByCourseId.get(c.id);
        const staff = c.assigned_staff_id ? staffMap.get(c.assigned_staff_id) : undefined;
        return {
          id: c.id,
          weekday: c.weekday,
          label: `${officeName}${c.code}`,
          assignedStaffId: c.assigned_staff_id ?? null,
          assignedStaffName: staff?.name ?? null,
          visitCount: s?.count ?? 0,
          totalMinutes: s?.minutes ?? 0,
          timeRange: s?.first && s?.last ? `${s.first}〜${s.last}` : null,
        };
      });
  }, [courses, weekVisits, officeNameById, staffMap]);

  /** パレット/セル間のコースドロップ = 今週のコース担当変更 (マスタ不変)。 */
  const handleCourseDropOnStaff = async (courseId: string, staffId: string, weekday: number) => {
    setCourseDrag(null);
    const course = courses.find((c) => c.id === courseId);
    if (!course) return;
    if (course.weekday !== weekday) {
      // 曜日移動は Phase A2 (course-move-weekday)。A1 では同一曜日のみ。
      toast.info('曜日をまたぐ貼り付けはまだできません — 同じ曜日のセルに貼ってください');
      return;
    }
    if (course.assigned_staff_id === staffId) return;
    const staff = staffMap.get(staffId);
    if (staff?.is_trainee) {
      toast.error('新人はコース担当にできません（同行で割り当ててください）');
      return;
    }
    const ok = await handleChangeAssignedStaff(courseId, staffId);
    // 休みの日への貼り付けは「行うが隠さず知らせる」(§4-2・ブロックしない)。
    const off = offByStaffWeekday.get(`${staffId}:${weekday}`);
    if (ok && off) {
      toast.warning(
        `${staff?.name ?? 'このスタッフ'}さんはこの日「${off.type}」の予定です。担当を確認してください`,
      );
    }
  };

  /** パレットへのドロップ = 担当解除 (未割当へ戻す・今週のみ)。 */
  const handleCourseUnassignDrop = (courseId: string) => {
    setCourseDrag(null);
    const course = courses.find((c) => c.id === courseId);
    if (!course || !course.assigned_staff_id) return;
    void handleChangeAssignedStaff(courseId, null);
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
          // 型とのズレ (2026-08-08): 赤トグルが押せない理由を正しく出すのに必要。
          master_start_time: cv.master_start_time ?? null,
          // T-1L: タイムライン兄弟リスト用の患者性別 (行頭ドット・左色帯).
          patient_sex: (patient?.sex as string | null | undefined) ?? null,
          // G2/G3: 日リストの × (訪問削除) と「今週のみ」チップ (固定昇格) の根拠.
          visit_id: cv.id,
          source: cv.source ?? null,
          week_pinned: cv.week_pinned ?? null,
          // T-6撤去: 旧テーブルの ①/② バッジと「相方: ...」注記を日リストへ移設.
          group_slot_label: cv.group_slot_label,
          partner_location: cv.partner_location ?? null,
          partner_label: cv.partner_label ?? null,
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

      const plannedVisits = visits.filter(
        (v) => (v as { status?: string | null }).status !== 'cancelled',
      );
      const listStaffName = course?.assigned_staff_id
        ? (staffMap.get(course.assigned_staff_id)?.name ?? null)
        : null;
      // PO 2026-07-09: assigned_staff_id はあるが active 一覧に無い = 削除済み (stale)。
      const listStaffMissing =
        !!course?.assigned_staff_id && !staffMap.has(course.assigned_staff_id);
      out.push({
        key: `${template.id}:${activeWeekday}`,
        title: `${officeName ? `${officeName} ` : ''}${template.label} コース`,
        summary: `${plannedVisits.length}件`,
        visits,
        freeGaps,
        capacity: { filled: plannedVisits.length, max: capMax },
        // T-1L: TimelineDayList のグループ見出し用.
        office_name: officeName ?? null,
        course_code: template.label ?? null,
        staff_name: listStaffName,
        staff_missing: listStaffMissing,
        // 新人同行 (§7.2): コース丸ごと同行バッジ解決用 (resolveCourseId の引数).
        course_template_id: template.id,
        weekday: activeWeekday,
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
    staffMap,
  ]);

  // ─── T-1: 縦タイムライン用の列データ (schedule-timeline-redesign-design.md) ──
  //   テーブルと同じ per-course データ (visits / freeGaps / capacity) をタイムライン列へ
  //   変換するだけ (表示専用・API 不変)。担当スタッフの sex はアバター色に使う。
  const timelineColumns = useMemo<TimelineCourseColumn[]>(() => {
    if (weekdayViewMode !== 'timeline') return [];
    const out: TimelineCourseColumn[] = [];
    for (const { template, officeName } of courseTablesForActiveDay) {
      const course = findCourseForTemplate({
        template,
        weekday: activeWeekday,
        isoYear,
        isoWeek,
        courses,
      });
      const visits = course ? (visitsByCourse.get(course.id) ?? []) : [];
      const assignedStaff = course?.assigned_staff_id
        ? (staffMap.get(course.assigned_staff_id) ?? null)
        : null;
      // PO 2026-07-09: assigned_staff_id はあるが active 一覧に無い = 削除済み (stale)。
      const assignedStaffMissing =
        !!course?.assigned_staff_id && !staffMap.has(course.assigned_staff_id);
      const capMax = effectiveCapacity(
        template,
        activeWeekday,
        staffCountFor(template.office_id, activeWeekday),
        courseCodesMax,
      );
      const freeGaps = course ? (freeGapsByCourse.get(course.id) ?? []) : [];
      const staffEvents = assignedStaff
        ? getStaffEventsForWeekday(assignedStaff.id, activeWeekday, staffEventsByStaff)
        : [];
      out.push({
        key: `${template.id}:${activeWeekday}`,
        template,
        course,
        officeName,
        visits,
        assignedStaff,
        assignedStaffMissing,
        freeGaps,
        capacity: {
          filled: visits.filter((v) => v.status !== 'cancelled').length,
          max: capMax,
        },
        staffEvents,
        // G1: 列ヘッダの担当スタッフ変更 select の選択肢 (テーブルと同じ供給元)。
        staffOptions: staffByOffice.get(template.office_id) ?? [],
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
    staffMap,
    freeGapsByCourse,
    staffEventsByStaff,
    staffCountFor,
    courseCodesMax,
    staffByOffice,
  ]);

  // ─── T-1: 現在時刻ライン (表示中の曜日が「今日」のときだけ出す) ──────────
  const timelineNowMinutes = useMemo<number | null>(() => {
    if (weekdayViewMode !== 'timeline') return null;
    const now = new Date();
    // 表示中の曜日の実日付 (週の月曜 + activeWeekday) が今日かどうか。
    const activeDate = addDays(weekStart, activeWeekday);
    const sameDay =
      activeDate.getFullYear() === now.getFullYear() &&
      activeDate.getMonth() === now.getMonth() &&
      activeDate.getDate() === now.getDate();
    if (!sameDay) return null;
    return now.getHours() * 60 + now.getMinutes();
  }, [weekdayViewMode, weekStart, activeWeekday]);

  // ─── T-3: 週タイムラインのコース選択肢 (拠点順・担当名つき) ──────────────
  const weekTimelineOptions = useMemo<WeekTimelineOption[]>(() => {
    const officeOrder = new Map(offices.map((o, i) => [o.id, i]));
    return templates
      .slice()
      .sort((a, b) => {
        const oa = officeOrder.get(a.office_id) ?? 99;
        const ob = officeOrder.get(b.office_id) ?? 99;
        return oa !== ob ? oa - ob : (a.label ?? '').localeCompare(b.label ?? '');
      })
      .map((t) => {
        const officeName = officeNameById.get(t.office_id) ?? '';
        return { templateId: t.id, label: `${officeName}${t.label}` };
      });
  }, [templates, offices, officeNameById]);

  // 週タイムライン (全コース縦積み): コース×曜日の実効定員 (受入可能数)。
  const weekTimelineCapacityByWeekday = useCallback(
    (templateId: string, weekday: number): number => {
      const t = templates.find((tpl) => tpl.id === templateId);
      if (!t) return 0;
      return effectiveCapacity(t, weekday, staffCountFor(t.office_id, weekday), courseCodesMax);
    },
    [templates, staffCountFor, courseCodesMax],
  );

  // 週タイムライン: コース×曜日の担当スタッフ (曜日ごとに担当が異なり得る)。
  // 日ビューヘッダと同じ性別色アバターを出すため name + sex を返す。
  const weekTimelineStaffByWeekday = useCallback(
    (templateId: string, weekday: number): { name: string; sex?: string | null } | null => {
      const staffId = assignedStaffByTemplateWeekday.get(`${templateId}:${weekday}`);
      if (!staffId) return null;
      const staff = staffMap.get(staffId);
      if (!staff) return null;
      return { name: staff.name, sex: (staff as { sex?: string | null }).sex ?? null };
    },
    [assignedStaffByTemplateWeekday, staffMap],
  );

  // 週の曜日ヘッダ日付 (0=Mon..5=Sat)。
  const weekdayDates = useMemo<(string | null)[]>(() => {
    return Array.from({ length: 6 }, (_, wd) => {
      const d = addDays(weekStart, wd);
      return `${d.getMonth() + 1}/${d.getDate()}`;
    });
  }, [weekStart]);

  // ─── 新人同行モード (§7.1/§7.2) ─────────────────────────────────────────
  const templateLabelById = useMemo(() => {
    const m = new Map<string, string>();
    for (const o of weekTimelineOptions) m.set(o.templateId, o.label);
    return m;
  }, [weekTimelineOptions]);

  // (course_template_id, weekday) → その週のコースインスタンス id。
  const resolveAccompanimentCourseId = useCallback(
    (templateId: string, weekday: number): string | null => {
      const t = templates.find((tpl) => tpl.id === templateId);
      if (!t) return null;
      return findCourseForTemplate({ template: t, weekday, isoYear, isoWeek, courses })?.id ?? null;
    },
    [templates, isoYear, isoWeek, courses],
  );

  // 週の全訪問を同行判定用の索引へ。コース id とラベルを解決して埋める。
  const accompanimentWeekVisits = useMemo<AccompanimentWeekVisit[]>(() => {
    return overviewVisits.map((v) => {
      const startMin = parseHM(v.start_time);
      const endMin = parseHM(v.end_time ?? null) ?? (startMin !== null ? startMin + 35 : null);
      return {
        visitId: v.id,
        patientId: v.patient_id,
        patientName: v.patient_name,
        weekday: v.weekday,
        courseId: resolveAccompanimentCourseId(v.course_template_id, v.weekday),
        courseTemplateId: v.course_template_id,
        courseLabel: templateLabelById.get(v.course_template_id) ?? null,
        startMin,
        endMin,
        // 同住所×同時刻ペア (90分占有) を重複判定から免除するためのキー。
        sameAddressKey: sameAddressKeyByPatientId.get(v.patient_id) ?? null,
      };
    });
  }, [overviewVisits, resolveAccompanimentCourseId, templateLabelById, sameAddressKeyByPatientId]);

  const weekdayDateLabel = useCallback(
    (weekday: number): string => {
      const date = weekdayDates[weekday] ?? '';
      const wd = WEEKDAY_LABELS[weekday] ?? '';
      return date ? `${date}(${wd})` : wd;
    },
    [weekdayDates],
  );

  // 同行者は新人に限らない (general-accompaniment-design.md 確定#1〜#8)。
  // active な全スタッフを候補にし、新人を先頭グループへ寄せて渡す (§4 セレクタ一般化)。
  const accompanimentStaffOptions = useMemo(() => {
    const actives = allStaff.filter((s) => s.status === 'active');
    return [...actives].sort((a, b) => {
      const at = a.is_trainee === true ? 0 : 1;
      const bt = b.is_trainee === true ? 0 : 1;
      if (at !== bt) return at - bt;
      return (a.name ?? '').localeCompare(b.name ?? '', 'ja');
    });
  }, [allStaff]);

  const accompaniment = useAccompanimentController({
    isoYear,
    isoWeek,
    canEdit,
    staffOptions: accompanimentStaffOptions,
    weekVisits: accompanimentWeekVisits,
    resolveCourseId: resolveAccompanimentCourseId,
    weekdayDateLabel,
  });

  // ─── 同行で「2人目 (slot1)」を充足した複数名対応患者をプールから消す ─────
  //   週の全同行リンク (保存済みサーバデータ) を取得し、複数名対応患者のうち
  //   配置済み訪問がすべて同行つきの患者について slot1 を上乗せする。これにより
  //   PoolPanel の②カード (2人目未配置) がプールから消える。同行を外して確定すれば
  //   invalidate → 再取得で復活する。kind (新人/一般) は見ない (確定#7)。
  //   ※ controller 内の displayQuery と同一キーのため React Query が dedupe する。
  const traineeAccompanimentsQuery = useTraineeAccompaniments({
    isoYear,
    isoWeek,
    enabled: accompanimentStaffOptions.length > 0,
  });
  const accompanimentLinkIndex = useMemo(
    () => buildAccompanimentLinkIndex(traineeAccompanimentsQuery.data),
    [traineeAccompanimentsQuery.data],
  );
  const multiStaffPatientIds = useMemo(
    () =>
      allPatients
        .filter(
          (p) =>
            (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff === true,
        )
        .map((p) => p.id),
    [allPatients],
  );
  // プールへ渡す「同行充足を織り込んだ」配置済み slot マップ。
  const assignedSlotsForPool = useMemo(
    () =>
      augmentAssignedSlotsWithAccompaniment(
        assignedSlotsByPatient,
        multiStaffPatientIds,
        placedVisitsByPatient,
        accompanimentLinkIndex,
      ),
    [assignedSlotsByPatient, multiStaffPatientIds, placedVisitsByPatient, accompanimentLinkIndex],
  );

  // ─── Wave U-3: undo/redo 中は両ボタン disabled ─────────────────────────
  const undoRedoPending = undoMut.isPending || redoMut.isPending;

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
      {/* lg 以上は flex 高さチェーン (ページ非スクロール)。ツールバー等は固定行、
          タイムライン領域とプールだけが内部スクロールする。 */}
      <section
        className="flex flex-col gap-3 lg:min-h-0 lg:flex-1"
        data-testid="course-day-table-panel"
      >
        {/*
          W-9b: Row 1 を justify-between の両端配置に変更。
            左グループ: [週を生成][週次ガイド]  ← 週次操作の入口ペアを左端 (曜日タブ真上) に配置.
            右グループ: [新規患者登録][診断][最適化] │ [固定枠戻][全件保存].
            ※ Row 1 は最上段なので border-t 不要.
            Row 2 (曜日タブ + テーブル/リスト + 二次操作):
              左: 曜日タブ (月〜土 + 週) + iso week label.
              右 (ml-auto): 3 グループを縦区切り線で分離 (α | γ | δ).
                α テーブル/リスト切替 (「週」タブ時のみ非表示)
                γ 自動スタッフ割当 🟢 + 一斉スタッフ未割当 (= 割当/リセットの対操作)
                δ 🔒 全件ロック + 🔓 全件解除 (= 一括設定)
          ボタンは基本 variant="outline" size="sm" で統一感を担保し、
          毎週必ず押す主要ボタン (自動スタッフ割当) のみ variant="default" (= brand-primary 緑) で目立たせる.
        */}
        {/* 上部ツールバーは常時固定 (親がページ非スクロールの flex 列のため sticky 不要。
            PO指摘 2026-07-08: sticky の「張り付くまで一瞬上がる」挙動を根絶)。 */}
        <Card className="p-3 lg:shrink-0">
          {/* Row 1: 両端配置 toolbar (canEdit のみ).
              W-9b: justify-between で左右グループに分割。
                左グループ = [週を生成][週次ガイド] (週次操作の入口ペアを曜日タブ真上・左端に配置).
                右グループ = [新規患者登録][診断][最適化] │ [固定枠戻][全件保存].
              狭幅では flex-wrap で左グループ→右グループ順に折り返す. */}
          {/* RB (PO決定 2026-07-08): 全ロール同一表示・操作は権限どおり。
              旧: canEdit で Row1 丸ごと非表示 → staff だけ画面構成が変わっていた。
              以後は常時表示し、編集系ボタンだけ disabled にする (BE RBAC は不変)。 */}
          {
            <div
              className="flex flex-wrap items-center justify-between gap-2"
              role="toolbar"
              aria-label="スケジュール主要操作"
              data-testid="schedule-main-action-toolbar"
            >
              {/* 左グループ: 週次操作の起点ペア (W-9b PO 指示). */}
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={handleGenerateWeek}
                  disabled={!canEdit || generateWeekMut.isPending}
                  data-testid="generate-week-button"
                >
                  {generateWeekMut.isPending ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                  )}
                  週を生成
                </Button>
                {/* PO 指示 (W-9): 「週次ガイド」を「週を生成」の右隣に配置。
                    週次操作の入口とその手順書を対のペアとして隣接。
                    P3-⑥: 案内のみ・variant=ghost で目立たせすぎない. */}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setWeeklyRitualGuideOpen(true)}
                  data-testid="weekly-ritual-guide-button"
                >
                  <ListChecks className="mr-1 h-4 w-4" aria-hidden />
                  週次ガイド
                </Button>
              </div>

              {/* 右グループ: その他操作 + 書き戻し系. */}
              <div className="flex flex-wrap items-center gap-2">
                {/* PO 指示 2026-07-03: 「プール投入」ボタンは削除。保留プールの
                    「効果を表示」ボタン (PoolOverviewPane) が入口として十分なため。 */}
                {/* W-4 (D-4): 旧「＋新規提案」を「＋新規患者登録」に置換。患者マスタの
                    登録フォームを再利用し、登録→希望登録→プール流入の入口を一本化する。 */}
                <RegisterPatientButton disabled={!canEdit || isProcessing} />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setScheduleHealthOpen(true)}
                  disabled={!canEdit}
                  data-testid="schedule-health-button"
                >
                  <HeartPulse className="mr-1 h-4 w-4" aria-hidden />
                  スケジュール診断
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setScopeOptimizeInitialScope(null); // ツールバーからは手動選択で開く.
                    setScopeOptimizeInitialOfficeId(null);
                    setScopeOptimizeOpen(true);
                  }}
                  disabled={!canEdit}
                  data-testid="scope-optimize-button"
                >
                  <Route className="mr-1 h-4 w-4" aria-hidden />
                  スケジュール最適化
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
                  disabled={!canEdit || isProcessing}
                />
                <BulkFixToPatternButton canEdit={canEdit} isoYear={isoYear} isoWeek={isoWeek} />
              </div>
            </div>
          }

          {/* Row 2 (中段): 青ピン一括のみ (PO 決定 2026-08-09)。
              赤の一括 (全件ピン留め/解除) は統合により廃止 — 完全固定は患者マスタの
              固定訪問スケジュールで設定する (週全体 / 曜日ごと)。 */}
          <div
            className="mt-2 flex flex-wrap items-center justify-end gap-1.5"
            data-testid="course-day-bulk-pin-row"
          >
            {/* 何に対する一括かをグループ見出しで明示する (PO 指摘 2026-08-09)。 */}
            <span className="text-[11px] font-semibold text-text-muted">今週の配置:</span>
            <BulkWeekPinAllButton canEdit={canEdit} isoYear={isoYear} isoWeek={isoWeek} />
          </div>

          {/* Row 3: 曜日タブ (左) + 表示切替・二次操作 (右寄せ).
              中段 (一括ピン群) との間に border-t + mt-3 pt-3 で水平区切り線 + 余白. */}
          <div
            className="mt-3 flex flex-wrap items-center gap-2 border-t border-border-default pt-3"
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
              {/* 職員スケジュール (スタッフ×曜日・イベント運用の家)。カイポケの同名画面と
                  同型。週ビュー内「スタッフ別」サブモードからの昇格 (2026-08-20)。 */}
              <button
                key="staff"
                type="button"
                role="tab"
                aria-selected={activeTab === 'staff'}
                aria-controls="course-staff-schedule-panel"
                onClick={() => setActiveTab('staff')}
                disabled={accompaniment.active}
                title={
                  accompaniment.active
                    ? '同行モード中はタイムライン表示のみ使えます'
                    : 'スタッフごとの週の予定とイベント（カイポケの職員スケジュールと同じ構造）'
                }
                data-testid="course-day-tab-staff"
                className={`ml-1 rounded border px-3 py-1 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${
                  activeTab === 'staff'
                    ? 'border-brand-primary bg-brand-primary text-white'
                    : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted'
                }`}
              >
                職員スケジュール
              </button>
            </div>

            <span className="tnum text-[11px] text-text-muted">{isoWeekLabel}</span>

            {/* Row 2 右半: 表示モード + 二次操作 を 3 グループ (α/γ/δ) に分けて
                縦区切り線で分離. ml-auto を持つ最初の見える要素で右寄せを担保 (= α が出ていれば α、
                「週」タブで α 非表示時は γ にフォールバックして右寄せを維持).
                Phase G-42: 旧 β group (= 固定枠戻 + 全件保存) は Row 1 右端へ移動. */}
            <div
              className="ml-auto flex flex-wrap items-center gap-3"
              data-testid="course-day-row2-right-toolbar"
            >
              {/* Group α: タイムライン/リスト切替 (「週」タブ時は非表示). */}
              {typeof activeTab === 'number' ? (
                <div
                  role="group"
                  aria-label="月-土タブ 表示モード切替"
                  className="inline-flex overflow-hidden rounded border border-border-default text-xs"
                >
                  <button
                    type="button"
                    onClick={() => setWeekdayViewMode('timeline')}
                    aria-pressed={weekdayViewMode === 'timeline'}
                    data-testid="course-day-mode-timeline"
                    className={
                      weekdayViewMode === 'timeline'
                        ? 'bg-brand-primary px-2 py-1 text-white'
                        : 'bg-bg-base px-2 py-1 text-text-secondary hover:bg-bg-muted'
                    }
                  >
                    タイムライン
                  </button>
                  <button
                    type="button"
                    onClick={() => setWeekdayViewMode('list')}
                    aria-pressed={weekdayViewMode === 'list'}
                    // 同行モード中はリスト表示に選択操作が無いため切替を封じる。
                    disabled={accompaniment.active}
                    title={
                      accompaniment.active
                        ? '同行モード中はタイムライン表示のみ使えます'
                        : undefined
                    }
                    data-testid="course-day-mode-list"
                    className={
                      weekdayViewMode === 'list'
                        ? 'bg-brand-primary px-2 py-1 text-white'
                        : 'bg-bg-base px-2 py-1 text-text-secondary hover:bg-bg-muted disabled:cursor-not-allowed disabled:opacity-50'
                    }
                  >
                    リスト
                  </button>
                </div>
              ) : activeTab === 'staff' ? (
                /* 職員スケジュールタブ: イベント追加 + カイポケ送信の常設入口。 */
                <div className="inline-flex items-center gap-1.5">
                  <Button
                    type="button"
                    size="sm"
                    variant="default"
                    disabled={!canEdit}
                    onClick={() =>
                      setSlotEventState({
                        staffId: null,
                        date: format(weekStart, 'yyyy-MM-dd'),
                        startHM: '09:00',
                        endHM: '10:00',
                      })
                    }
                    data-testid="staff-tab-add-event"
                    title="スタッフの打合せ・イベントを追加（複数スタッフ一括可）"
                  >
                    ＋イベント
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!canEdit}
                    onClick={() => setImportEventsOpen(true)}
                    data-testid="staff-tab-import-events"
                    title="この週のカイポケの個別業務（イベント）だけを取り込みます（訪問には触れません）"
                  >
                    ⇩ カイポケ取込
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!canEdit}
                    onClick={() => setSendEventsOpen(true)}
                    data-testid="staff-tab-send-events"
                    title="この週のらく助のイベントをカイポケの職員スケジュールへ登録します"
                  >
                    ⇧ カイポケ送信
                  </Button>
                </div>
              ) : (
                /* T-3: 「週」タブ時は タイムライン / リスト の切替を出す (縦スペース不消費). */
                <div
                  role="group"
                  aria-label="週タブ 表示モード切替"
                  className="inline-flex items-center gap-2"
                >
                  <div className="inline-flex overflow-hidden rounded border border-border-default text-xs">
                    <button
                      type="button"
                      onClick={() => setWeekViewMode('timeline')}
                      aria-pressed={weekViewMode === 'timeline'}
                      data-testid="course-week-mode-timeline"
                      className={
                        weekViewMode === 'timeline'
                          ? 'bg-brand-primary px-2 py-1 text-white'
                          : 'bg-bg-base px-2 py-1 text-text-secondary hover:bg-bg-muted'
                      }
                    >
                      タイムライン
                    </button>
                    <button
                      type="button"
                      onClick={() => setWeekViewMode('overview')}
                      aria-pressed={weekViewMode === 'overview'}
                      // 同行モード中はリスト表示に選択操作が無いため切替を封じる。
                      disabled={accompaniment.active}
                      title={
                        accompaniment.active
                          ? '同行モード中はタイムライン表示のみ使えます'
                          : undefined
                      }
                      data-testid="course-week-mode-overview"
                      className={
                        weekViewMode === 'overview'
                          ? 'bg-brand-primary px-2 py-1 text-white'
                          : 'bg-bg-base px-2 py-1 text-text-secondary hover:bg-bg-muted disabled:cursor-not-allowed disabled:opacity-50'
                      }
                    >
                      コース別
                    </button>
                    {/* 旧「スタッフ別」サブモードはトップレベルタブ
                        「職員スケジュール」へ昇格 (2026-08-20)。 */}
                  </div>
                </div>
              )}

              {/* RB (PO決定 2026-07-08): γ/δ も全ロール常時表示・編集ボタンは disabled。 */}
              {
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

                  {/* Group γ: ↶ 戻る / ↷ 進む (Wave U-3) + 自動スタッフ割当 + リセット.
                      2026-07: 「自動スタッフ割付」を「自動スタッフ割当」に改称し
                      Row 1 から一斉スタッフ未割当の左隣へ移動 (割当⇄リセットの対操作を隣接).
                      Wave U-3: 戻る/進むを自動スタッフ割当の左に追加。 */}
                  <div className="flex flex-wrap items-center gap-1.5">
                    {/* Wave U-3: ↶ 戻る */}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void handleUndo()}
                      disabled={!canEdit || !opLogState?.can_undo || undoRedoPending}
                      title={
                        opLogState?.undo_label != null ? `戻す: ${opLogState.undo_label}` : '戻す'
                      }
                      data-testid="schedule-undo-button"
                    >
                      {undoRedoPending ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                      ) : (
                        <Undo2 className="mr-1 h-4 w-4" aria-hidden />
                      )}
                      戻る
                    </Button>
                    {/* Wave U-3: ↷ 進む */}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => void handleRedo()}
                      disabled={!canEdit || !opLogState?.can_redo || undoRedoPending}
                      title={
                        opLogState?.redo_label != null ? `進む: ${opLogState.redo_label}` : '進む'
                      }
                      data-testid="schedule-redo-button"
                    >
                      {undoRedoPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                      ) : (
                        <Redo2 className="mr-1 h-4 w-4" aria-hidden />
                      )}
                      進む
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="default"
                      onClick={handleAssignStaff}
                      disabled={!canEdit || assignStaffOnlyMut.isPending}
                      data-testid="assign-staff-only-button"
                    >
                      {assignStaffOnlyMut.isPending ? (
                        <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                      ) : (
                        <UserCheck className="mr-1 h-4 w-4" aria-hidden />
                      )}
                      自動スタッフ割当
                    </Button>
                    <UnassignAllStaffButton
                      isoYear={isoYear}
                      isoWeek={isoWeek}
                      officeId={officeId}
                      disabled={!canEdit || isProcessing}
                    />
                    {/* 同行モード (§7.1)。active なスタッフが 1 人以上・編集権限ありのときのみ。 */}
                    {accompaniment.available && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          // 同行の選択操作 (コース列ヘッダ/訪問カードのクリック) は
                          // タイムライン表示にのみ結線されている。一覧/リスト表示のまま
                          // モードに入ると「押しても何も起きない」ため、開始時に両タブの
                          // 表示を強制的にタイムラインへ切り替える (PO報告 2026-07-12)。
                          setWeekViewMode('timeline');
                          setWeekdayViewMode('timeline');
                          // 職員スケジュールタブには同行の選択操作が無いため週タブへ退避
                          setActiveTab((t) => (t === 'staff' ? 'week' : t));
                          accompaniment.enter();
                        }}
                        disabled={accompaniment.active}
                        data-testid="accompaniment-enter-button"
                        title="スタッフが他スタッフの訪問に同行する設定を編集します"
                      >
                        👥 同行
                      </Button>
                    )}
                  </div>
                </>
              }
            </div>
          </div>
        </Card>

        {/* Schedule Advisor Phase 3: 見直しどきバナー (条件成立時のみ描画, admin/manager only).
            トレンド上の移動時間悪化を検知したら健康診断への導線を控えめに提示する. */}
        {canEdit ? (
          <ScheduleReviewBanner
            isoYear={isoYear}
            isoWeek={isoWeek}
            officeId={officeId}
            onOpenHealth={() => setScheduleHealthOpen(true)}
          />
        ) : null}

        {/* Wave 19: 2 ペイン レイアウト — メイン (1fr) + プール (320px)。
            lg 以上は残り高さを占有し、左右それぞれが内部スクロール。 */}
        <div
          // lg:grid-rows-[minmax(0,1fr)]: 行トラックを内容高でなくコンテナ高に固定する
          // (これが無いと行が内容ぶん伸びて下端がはみ出し、内部スクロールが効かない)。
          className="grid grid-cols-1 gap-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[1fr_320px] lg:grid-rows-[minmax(0,1fr)]"
          data-testid="course-day-two-pane"
        >
          {/* 左ペイン: 当該曜日の盤面 (lg 以上はこの中だけ縦スクロール)。
              日タイムラインだけは盤面内部でスクロールさせる (列ヘッダ固定のため)。 */}
          <div
            className={cn(
              'min-w-0 space-y-3 lg:min-h-0',
              // 日タイムライン/週リストはペインを固定高にして内部スクロールへ委譲
              // (ヘッダ行 sticky が内部スクロールで効く)。週タイムライン(縦積み)や
              // 日リストは従来のペインスクロール。
              (typeof activeTab === 'number' && weekdayViewMode === 'timeline') ||
                (activeTab === 'week' && weekViewMode === 'overview')
                ? 'lg:flex lg:flex-col lg:overflow-hidden'
                : 'lg:overflow-y-auto',
            )}
          >
            {/* 職員スケジュール (スタッフ×曜日・イベント運用の家・2026-08-20 昇格)。
                投影 = 訪問/コース (読むだけ) / 正典 = イベント (＋で追加・帯クリックで編集)。 */}
            {activeTab === 'staff' ? (
              <div
                id="course-staff-schedule-panel"
                role="tabpanel"
                aria-labelledby="course-day-tab-staff"
                data-testid="course-staff-schedule-panel"
                className="space-y-2"
              >
                <StaffWeekBoard
                  templates={templates}
                  officeNameById={officeNameById}
                  visits={overviewVisits}
                  assignedStaffByTemplateWeekday={assignedStaffByTemplateWeekday}
                  staffMap={staffMap}
                  staffEventsByStaff={staffEventsByStaff}
                  weekStart={weekStart}
                  onPatientClick={handleOpenPatientDetail}
                  showAllStaff
                  onAddEvent={
                    canEdit
                      ? (staffId, date) =>
                          setSlotEventState({
                            staffId,
                            date: format(date, 'yyyy-MM-dd'),
                            startHM: '09:00',
                            endHM: '10:00',
                          })
                      : undefined
                  }
                  onEventClick={
                    canEdit ? (ev, staffId) => setTlEventEdit({ staffId, event: ev }) : undefined
                  }
                  // 週空間 A1: コース貼り付け DnD (weekly-space-design.md §4)。
                  courseIdByTemplateWeekday={canEdit ? courseIdByTemplateWeekday : undefined}
                  onCourseDrop={
                    canEdit
                      ? (courseId, staffId, weekday) =>
                          void handleCourseDropOnStaff(courseId, staffId, weekday)
                      : undefined
                  }
                  activeCourseDrag={courseDrag}
                  onCourseDragChange={setCourseDrag}
                  onCourseUnassign={canEdit ? handleCourseUnassignDrop : undefined}
                  offByStaffWeekday={offByStaffWeekday}
                />
                {/* コースの表 (パレット): 未割当コースをセルへドラッグして貼り付ける。 */}
                <WeekCoursePalette
                  courses={paletteCourses}
                  canEdit={canEdit}
                  onDragChange={setCourseDrag}
                  onUnassignDrop={canEdit ? handleCourseUnassignDrop : undefined}
                  activeDrag={courseDrag}
                />
                <p className="text-[11px] text-text-muted">
                  コースは下の「コースの表」からスタッフのセルへドラッグで貼り付け（今週のみ・毎週の型には影響しません）。
                  戻すときはコース帯の「×」か、コースの表へドラッグ、またはツールバーの「戻る」。
                  訪問明細の編集は週・曜日タブで。イベントはこの画面が正典で、＋やイベント帯のクリックで追加・編集できます。
                </p>
              </div>
            ) : /* Wave 18 Phase B-6: 「週」タブ選択時は CourseWeekOverview を表示 */
            activeTab === 'week' ? (
              <div
                id="course-week-overview-panel"
                role="tabpanel"
                aria-labelledby="course-day-tab-week"
                data-testid="course-week-overview-panel"
                className={cn(
                  'space-y-2',
                  // 週リストは内部スクロール (曜日ヘッダ固定) のため高さを委譲する。
                  weekViewMode === 'overview' && 'lg:flex lg:min-h-0 lg:flex-1 lg:flex-col',
                )}
              >
                {weekViewMode === 'timeline' ? (
                  /* T-3改: 週タイムライン (全コース縦積み・縦スクロールで一元閲覧). */
                  <WeekTimelineBoard
                    options={weekTimelineOptions}
                    visits={overviewVisits}
                    eventFramesByWeekday={staffEventFramesByWeekday}
                    weekdayDates={weekdayDates}
                    // 同行モード中は患者詳細を抑止 (§7.1 N-2)。
                    onPatientClick={accompaniment.active ? undefined : handleOpenPatientDetail}
                    capacityByWeekday={weekTimelineCapacityByWeekday}
                    staffByWeekday={weekTimelineStaffByWeekday}
                    accompaniment={accompaniment.binding}
                  />
                ) : (
                  <CourseWeekOverview
                    templates={templates}
                    officeNameById={officeNameById}
                    eventFramesByWeekday={staffEventFramesByWeekday}
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
                    pfvCountFor={pfvCountFor}
                    managerCountFor={managerCountFor}
                    staffSummaryOffices={staffSummaryOffices}
                    freeGapsByCell={freeGapsByCell}
                    officeLatLngById={officeLatLngById}
                    accompaniment={accompaniment.binding}
                  />
                )}
              </div>
            ) : (
              /* メイン: 当該曜日の盤面 (タイムライン or リスト) */
              <div
                id={`course-day-panel-${activeWeekday}`}
                role="tabpanel"
                aria-labelledby={`course-day-tab-${activeWeekday}`}
                className={cn(
                  'space-y-3',
                  // 日タイムラインは盤面内部スクロール (lg:h-full) のため、左ペイン
                  // (lg:overflow-hidden) からの高さチェーンをここでも繋ぐ必要がある。
                  // これが無いと盤面が内容高で描画され、下端がペインに切り捨てられて
                  // スクロール不能になる (2026-07-08 下端切れの正体)。
                  weekdayViewMode === 'timeline' && 'lg:flex lg:min-h-0 lg:flex-1 lg:flex-col',
                )}
                data-testid="course-day-table-list"
              >
                {/* PO 2026-07-09: スタッフ不足バナー (表示 A-E 列数 > 稼働スタッフ数)。
                    列は PFV/visit の和集合でも出るため、担当が足りない拠点を曜日単位で警告。 */}
                {staffShortageBanners.length > 0 ? (
                  <div className="space-y-1.5" data-testid="staff-shortage-banner">
                    {staffShortageBanners.map((b) => (
                      <div
                        key={b.officeId}
                        role="alert"
                        className="rounded border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning"
                      >
                        {b.message}
                      </div>
                    ))}
                  </div>
                ) : null}
                {/* Phase G-36: 表示モード切替 (タイムライン ⇄ リスト) は Card 2 の Row 2 へ移設済. */}
                {courseTablesForActiveDay.length === 0 ? (
                  <Card className="p-4 text-sm text-text-muted">
                    {WEEKDAY_LABELS[activeWeekday]}曜日の表示対象コースがありません。 拠点マスタの
                    コーステンプレート (定員) を確認してください。
                  </Card>
                ) : weekdayViewMode === 'timeline' ? (
                  /* T-1: 縦タイムライン (時間比例カード・読み取り専用). */
                  <div data-testid="course-day-timeline-view" className="lg:min-h-0 lg:flex-1">
                    <TimelineDayBoard
                      columns={timelineColumns}
                      // スタッフ枠 (PO確定 2026-07-26): コース無し・イベントありの
                      // スタッフを盤面の列として編み込む (休みの人もスケジュールの一員)。
                      staffFrames={staffEventFramesByWeekday.get(activeWeekday) ?? []}
                      weekdayLabel={WEEKDAY_LABELS[activeWeekday] ?? ''}
                      // 同行モード中は通常のクリック/DnD/空き枠/イベントを全て抑止 (§7.1 N-2)。
                      onPatientClick={accompaniment.active ? undefined : handleOpenPatientDetail}
                      nowMinutes={timelineNowMinutes}
                      // T-2 ②-a: canEdit のときだけ空き枠クリック登録を解禁。
                      onFreeSlotClick={
                        canEdit && !accompaniment.active ? handleFreeSlotClick : undefined
                      }
                      // イベント帯クリック → 編集/削除 (canEdit のみ)。
                      onEventClick={
                        canEdit && !accompaniment.active ? handleTimelineEventClick : undefined
                      }
                      // T-2 ②-b: カード DnD (15分スナップ移動) は canEdit のみ。
                      dndEnabled={canEdit && !accompaniment.active}
                      // G1: 列ヘッダの担当スタッフ変更 (テーブルの dropdown と同機能)。
                      onChangeAssignedStaff={
                        canEdit && !accompaniment.active
                          ? (col, staffId) => {
                              if (!col.course) {
                                toast.warning(
                                  '先に「週を生成」を押してコースを作成してから担当を設定してください',
                                );
                                return;
                              }
                              void handleChangeAssignedStaff(col.course.id, staffId);
                            }
                          : undefined
                      }
                      isStaffMutating={updateCourseMut.isPending}
                      // G2: カード右下の × (訪問削除)。確認ダイアログは既存ハンドラが持つ。
                      onDeleteVisit={
                        canEdit && !accompaniment.active
                          ? (visitId, patientName) => {
                              void handleDeleteVisit(visitId, patientName);
                            }
                          : undefined
                      }
                      // G3: 「今週のみ」チップ → 固定訪問週間 (毎週の型) へ昇格。
                      onPromoteWeekOnly={
                        canEdit && !accompaniment.active
                          ? (patientId, patientName) => promoteWeekToFixed([patientId], patientName)
                          : undefined
                      }
                      // 週のピン (青ピン): 型とズレた訪問を今週この位置で固定する。
                      onToggleWeekPin={
                        canEdit && !accompaniment.active ? handleToggleWeekPin : undefined
                      }
                      // 型のピン (赤): カード右下クラスタから毎週の固定を操作 (PO 決定 2026-08-08)。
                      onTogglePin={canEdit && !accompaniment.active ? handleTogglePin : undefined}
                      accompaniment={accompaniment.binding}
                      accompanimentWeekday={activeWeekday}
                    />
                  </div>
                ) : (
                  /* T-1L: タイムライン兄弟の日リスト (モック意匠の整列グリッド・全情報保持).
                     共有コア WeekdayScheduleCard は提案ダイアログ専用に温存. */
                  <div data-testid="course-day-list-view">
                    <TimelineDayList
                      courses={weekdayListCourses}
                      staffFrames={staffEventFramesByWeekday.get(activeWeekday) ?? []}
                      onPatientClick={handleOpenPatientDetail}
                      onTogglePin={canEdit ? handleTogglePin : undefined}
                      // 週のピン (青): 赤トグルの右隣に並ぶ (PO 決定 2026-08-08)。
                      onToggleWeekPin={canEdit ? handleToggleWeekPin : undefined}
                      // G2: 行末の × (訪問削除)。確認ダイアログは既存ハンドラが持つ。
                      onDeleteVisit={
                        canEdit
                          ? (visitId, patientName) => {
                              void handleDeleteVisit(visitId, patientName);
                            }
                          : undefined
                      }
                      // G3: 「今週のみ」チップ → 固定訪問週間 (毎週の型) へ昇格。
                      onPromoteWeekOnly={
                        canEdit
                          ? (patientId, patientName) => promoteWeekToFixed([patientId], patientName)
                          : undefined
                      }
                      accompaniment={accompaniment.binding}
                    />
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 右ペイン: 保留プール (sticky で追従) */}
          <aside
            // lg 以上は左ペインと同じ高さに固定され、プールの中だけスクロールする。
            className="rounded lg:min-h-0 lg:overflow-y-auto"
            data-testid="course-day-pool-pane"
            // Wave 37 Phase 3-C: 配置済み slot マップを serialize してテスト・debug 用に露出.
            // 形式: "patientId:slot,...,patientId:slot"
            // 新人同行で 2 人目を充足した複数名対応患者は slot1 が上乗せされる
            // (assignedSlotsForPool) ため、②カードが消えた状態がここにも反映される。
            data-assigned-slots={Array.from(assignedSlotsForPool.entries())
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
            {/* Stage P-2: PoolOverviewPane が PoolGroupedByWeekday をラップし、
                「効果を計算」ボタン・delta バッジ・効果順ソートを追加する。
                患者カードクリック→詳細ダイアログへの既存導線は変更しない。 */}
            <PoolOverviewPane
              patients={poolPatients}
              disabled={!canEdit}
              assignedSlotsByPatient={assignedSlotsForPool}
              partnerLocationByPatientSlot={partnerLocationByPatientSlot}
              isoYear={isoYear}
              isoWeek={isoWeek}
              officeId={officeId}
              onBulkInsert={canEdit ? () => setBulkPoolInsertOpen(true) : undefined}
              unregisteredPatients={unregisteredActivePatients}
              onClickUnregisteredPatient={handleOpenPoolPatientDetail}
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
                const draggableId = buildPoolDraggableId(p.id, slotInfo.slotIndex);
                const cardData: PatientCardData = {
                  id: p.id,
                  name: p.name,
                  // タイムラインカードと同じ性別ウォッシュ意匠 (A-1 PO要望)。
                  sex: (p as { sex?: string | null }).sex ?? null,
                  caption: p.kana ?? undefined,
                  preferredTimeLabel: formatPreferredTimeLabel(wp),
                  serviceMinutes: wp.service_minutes ?? undefined,
                  sexRestriction:
                    (p.sex_restriction as 'female_only' | 'male_only' | null | undefined) ?? null,
                  requiresMultipleStaff:
                    (p as { requires_multiple_staff?: boolean | null }).requires_multiple_staff ??
                    null,
                  patientStatus: p.status ?? null,
                  // NG スタッフあり バッジ (§8-2 Phase 2). 患者一覧 (usePatients) が
                  // 載せている派生カウントをそのまま渡す。
                  ngStaffCount: p.ng_staff_count ?? null,
                  slotIndex: slotInfo.slotIndex,
                  partnerAssigned: slotInfo.partnerAssigned,
                  // Wave 38: 相方の現在地ラベル ("本店-A 15:00" など) を素通しする.
                  partnerLocationLabel: slotInfo.partnerLocationLabel ?? null,
                  // Phase G-44: 「希望 N、配置 X、不足 Y」 のラベル表示.
                  shortageInfo,
                };
                // ドラッグ開始時にゴーストへ流用するため「表示中のデータ」を記録。
                poolCardDataRef.current.set(draggableId, cardData);

                // ── 同行モード連携 ─────────────────────────────────────
                //   ②カード (複数名対応・2人目未配置) を、同行モード中にクリック
                //   したら、その患者の配置済み訪問を個別リンクとして一括トグルする
                //   (既にコース選択に内包される訪問はスキップ)。モード中は通常の
                //   カードクリック (患者詳細) は抑止する。
                //   ②カードは患者単位の操作なので「患者単位」に絞っているときだけ armed。
                const isSecondStaffCard =
                  isMultiStaff &&
                  slotInfo.slotIndex === 1 &&
                  slotInfo.partnerAssigned === true &&
                  accompaniment.binding.isVisitArmed;
                const accSelectableVisitIds =
                  accompaniment.active && isSecondStaffCard
                    ? (placedVisitsByPatient.get(p.id) ?? [])
                        .map((v) => v.id)
                        .filter((id) => !accompaniment.binding.isVisitInSelectedCourse(id))
                    : [];
                const accSelected =
                  accSelectableVisitIds.length > 0 &&
                  accSelectableVisitIds.every((id) => accompaniment.binding.isVisitSelected(id));
                const handlePoolCardClick = () => {
                  if (accompaniment.active) {
                    if (isSecondStaffCard) {
                      // 全選択済みなら解除、そうでなければ全選択 (タイムライン選択と同言語)。
                      const { toggleIds } = planSecondStaffToggle(
                        accSelectableVisitIds,
                        accompaniment.binding.isVisitSelected,
                      );
                      for (const id of toggleIds) accompaniment.binding.toggleVisit(id);
                    }
                    return; // モード中は患者詳細を開かない。
                  }
                  handleOpenPoolPatientDetail(p.id);
                };
                return (
                  <PatientCard
                    draggableId={draggableId}
                    patient={cardData}
                    disabled={!canEdit}
                    // 保留プールの患者カードクリックで詳細 + プール投入提案を開く.
                    // 同行モード中は②カードで訪問をトグル選択 (詳細は抑止)。
                    onCardClick={handlePoolCardClick}
                    selected={accSelected}
                  />
                );
              }}
            />
          </aside>
        </div>

        <DragOverlay>
          {/* タイムラインカード: カード実寸ゴースト (時間=面積のまま動く)。 */}
          {activeTlVisit ? <TlVisitDragGhost visit={activeTlVisit} /> : null}
          {/* 同住所ペア: 2名セットのままペアボックス実寸ゴースト。 */}
          {activeTlPairVisits ? <TlPairDragGhost visits={activeTlPairVisits} /> : null}
          {activePoolCard ? (
            // プールカード: 表示中と同一情報のカード実寸ゴースト (PatientCard 流用)。
            <PatientCard
              ghost
              draggableId={buildPoolDraggableId(activePoolCard.id, activePoolCard.slotIndex)}
              patient={activePoolCard}
            />
          ) : activePatientId ? (
            (() => {
              // フォールバック (ref 未登録時): 性別ウォッシュの簡易ゴースト。
              const p = patientById.get(activePatientId);
              const pal = genderPalette((p as { sex?: string | null } | undefined)?.sex);
              return (
                <div
                  className="flex h-full w-full cursor-grabbing items-center rounded-lg border border-l-[3px] px-2 py-1 text-xs font-bold shadow-[var(--shadow-md)]"
                  style={{
                    background: pal.bg,
                    borderColor: pal.ln,
                    borderLeftColor: pal.bar,
                    color: pal.ink,
                  }}
                >
                  {p?.name ?? activePatientId}
                </div>
              );
            })()
          ) : null}
        </DragOverlay>

        {/* T-2 ②-a: 空き枠クリック → 登録モーダル (訪問 / 会議・イベント切替) */}
        <SlotRegisterDialog
          open={slotRegState != null}
          context={
            slotRegState
              ? {
                  courseLabel: `${slotRegState.col.officeName}${slotRegState.col.template.label}`,
                  staffName: slotRegState.col.assignedStaff?.name ?? null,
                  weekdayLabel: WEEKDAY_LABELS[activeWeekday] ?? '',
                  gapStartMin: slotRegState.gap.startMin,
                  gapEndMin: slotRegState.gap.endMin,
                  canRegisterEvent: slotRegState.col.assignedStaff != null,
                }
              : null
          }
          patients={slotPatientOptions}
          busy={placeAndFixMut.isPending}
          onRegisterVisit={(args) => {
            const tplId = slotRegState?.col.template.id;
            if (!tplId) return;
            void handleSlotRegisterVisit({ ...args, courseTemplateId: tplId });
          }}
          onSwitchToEvent={handleSlotSwitchToEvent}
          onClose={() => setSlotRegState(null)}
        />
        {slotEventState ? (
          <TimelineEventAddDialog
            open
            onClose={() => setSlotEventState(null)}
            // D-1: 全登録スタッフ (他コース担当・管理職含む・在籍中のみ) から複数選択。
            staffOptions={allStaff
              .filter((s) => s.status === 'active')
              .map((s) => ({ id: s.id, name: s.name }))}
            defaultStaffIds={slotEventState.staffId ? [slotEventState.staffId] : []}
            defaultDate={slotEventState.date}
            defaultStart={slotEventState.startHM}
            defaultEnd={slotEventState.endHM}
          />
        ) : null}
        {/* イベント帯クリック → 編集/削除 (既存 EventEditDialog 流用) */}
        {tlEventEdit ? (
          <EventEditDialog
            staffId={tlEventEdit.staffId}
            event={tlEventEdit.event}
            open
            onOpenChange={(o) => {
              if (!o) setTlEventEdit(null);
            }}
          />
        ) : null}
        {/* イベントをカイポケへ送る (Phase 3・職員スケジュールタブ) */}
        <SendEventsToKaipokeDialog
          open={sendEventsOpen}
          onClose={() => setSendEventsOpen(false)}
          weekStartIso={format(weekStart, 'yyyy-MM-dd')}
        />
        {/* カイポケからイベントのみ取り込む (逆方向) */}
        <ImportEventsFromKaipokeDialog
          open={importEventsOpen}
          onClose={() => setImportEventsOpen(false)}
          weekStartIso={format(weekStart, 'yyyy-MM-dd')}
        />
        {/* T-2 ②-b: カード DnD 後の二択 (この週だけ / 固定パターン) */}
        <TimelineMoveDialog
          open={tlMoveState != null}
          context={
            tlMoveState
              ? {
                  patientName: (() => {
                    const names = tlMoveState.visits.map(
                      (v) => patientById.get(v.patient_id)?.name ?? v.patient_name ?? v.patient_id,
                    );
                    return tlMoveState.visits.length >= 2
                      ? `${names.join('・')}（同住所2名セット）`
                      : (names[0] ?? '');
                  })(),
                  fromLabel: `${tlMoveState.fromCol.officeName}${tlMoveState.fromCol.template.label}`,
                  toLabel: `${tlMoveState.toCol.officeName}${tlMoveState.toCol.template.label}`,
                  weekdayLabel: WEEKDAY_LABELS[activeWeekday] ?? '',
                  oldTimeHM: (tlMoveState.visits[0]?.start_time ?? '').slice(0, 5),
                  newTimeHM: fmtHM(tlMoveState.newStartMin),
                  durationMin: tlMoveState.durationMin,
                  courseChanged: tlMoveState.toCol.template.id !== tlMoveState.fromCol.template.id,
                  // 完全固定 (赤) を含む移動は注意書き付きで許可 (PO 決定 2026-08-09)。
                  lockedNotice: tlMoveState.visits.some((v) => v.is_pinned === true),
                }
              : null
          }
          busy={visitMoveWeekOnlyMut.isPending || bulkSyncWeekToFixedMut.isPending}
          onConfirm={(scope) => void handleTlMoveConfirm(scope)}
          onClose={() => setTlMoveState(null)}
        />

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

        {/* Schedule Advisor Phase 1: スケジュール健康診断ダイアログ (read-only). */}
        <ScheduleHealthDialog
          open={scheduleHealthOpen}
          onClose={() => setScheduleHealthOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          weekLabel={isoWeekLabel}
          // scope-optimization W2: 「移動が多いコース」から範囲最適化へのワンクリック導線.
          // 行の拠点 (courseOfficeId) を引き継ぐため、全拠点表示からでも対策計算できる.
          onOptimizeCourse={(courseCode, weekdayFilter, courseOfficeId) => {
            setScheduleHealthOpen(false);
            setScopeOptimizeInitialScope({
              weekdays: weekdayFilter === 'all' ? null : [weekdayFilter],
              courseCodes: [courseCode],
            });
            setScopeOptimizeInitialOfficeId(courseOfficeId);
            setScopeOptimizeOpen(true);
          }}
        />

        {/* scope-optimization W1-W3: 範囲最適化ダイアログ (simulate + 先頭N手適用 +
            タイムライン表示。全拠点モードではダイアログ内で拠点を選べる). */}
        <ScopeOptimizeDialog
          open={scopeOptimizeOpen}
          onClose={() => setScopeOptimizeOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          weekLabel={isoWeekLabel}
          canEdit={canEdit}
          initialScope={scopeOptimizeInitialScope}
          initialOfficeId={scopeOptimizeInitialOfficeId}
          offices={offices.map((o) => ({ id: o.id, name: o.name }))}
          patientMetaById={patientRowMetaById}
        />

        {/* W-2: プール一括投入ダイアログ (simulate → 見せる4点 → apply). */}
        <BulkPoolInsertDialog
          open={bulkPoolInsertOpen}
          onClose={() => setBulkPoolInsertOpen(false)}
          isoYear={isoYear}
          isoWeek={isoWeek}
          officeId={officeId}
          poolPatients={poolPatients.map((p) => ({
            id: p.id,
            name: p.name,
            primary_office_id: p.primary_office_id ?? null,
          }))}
          offices={offices.map((o) => ({ id: o.id, name: o.name }))}
          onOpenPatientDetail={handleOpenPoolPatientDetail}
        />

        {/* Phase G-91 / Wave N-2: 自動スタッフ割当の確認レビューフロー (連続 / 性別) + お知らせ. */}
        <AssignWarningDialog
          open={assignWarningOpen}
          onClose={() => {
            setAssignWarningOpen(false);
            // W-11 / 4段ソルバ v2.0: 整合のため review/notices/残留違反/Stage通知すべてクリアする。
            setReviewItems([]);
            setAutoCommittedNotices([]);
            setUnresolvedWarnings([]);
            setUnresolvedNgWarnings([]);
            setSecondaryConstraintWarnings([]);
            setManagerMobilizedNotices([]);
            setCrossOfficeNotices([]);
            setRescueSwapNotices([]);
          }}
          reviewItems={reviewItems}
          onApply={handleApplyReview}
          applying={reviewApplying}
          notices={autoCommittedNotices}
          unresolvedWarnings={unresolvedWarnings}
          unresolvedNgWarnings={unresolvedNgWarnings}
          secondaryConstraintWarnings={secondaryConstraintWarnings}
          managerMobilizedNotices={managerMobilizedNotices}
          crossOfficeNotices={crossOfficeNotices}
          rescueSwapNotices={rescueSwapNotices}
        />

        {/* NG スタッフ §7-2: 移動 / 配置 / 空き枠登録が NG / 性別に抵触したときの確認. */}
        <ConstraintOverrideConfirmDialog {...placementConstraintConfirm.dialogProps} />

        {/* NG スタッフ §7-2: 手動でのコース担当変更が NG / 性別に抵触したときの確認. */}
        <ConstraintOverrideConfirmDialog
          open={constraintConfirm !== null}
          warnings={constraintConfirm?.warnings ?? []}
          applying={updateCourseMut.isPending}
          onCancel={() => setConstraintConfirm(null)}
          onConfirm={() => {
            if (!constraintConfirm) return;
            void handleChangeAssignedStaff(
              constraintConfirm.courseId,
              constraintConfirm.staffId,
              true,
            );
          }}
        />

        {/* P3-⑥: 週次ガイドダイアログ (案内のみ・BE 変更なし). */}
        <WeeklyRitualGuideDialog
          open={weeklyRitualGuideOpen}
          onClose={() => setWeeklyRitualGuideOpen(false)}
        />

        {/* PO 2026-07-10: 生成済みの週への「週を生成」再実行の誤操作対策。
            当週に訪問が実在する場合のみ表示 (訪問 0 件は即実行で挙動不変)。 */}
        <Dialog
          open={generateWeekConfirmOpen}
          onOpenChange={(o) => {
            if (!o && !generateWeekMut.isPending) setGenerateWeekConfirmOpen(false);
          }}
        >
          <DialogContent className="max-w-md" data-testid="generate-week-confirm">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Rakusuke pose="think" className="h-10 shrink-0" />
                この週は既に生成されています。再実行しますか？
              </DialogTitle>
              <DialogDescription>
                この週には既に {weekVisits.length}{' '}
                件の訪問があります。週の生成をやり直すと、自動生成された未実施の訪問が作り直されます（実施済み・手動作成分は保持されます）。予定の組み直しが目的なら、通常は「固定枠に戻す」を使ってください。
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setGenerateWeekConfirmOpen(false)}
                disabled={generateWeekMut.isPending}
              >
                キャンセル
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={handleConfirmGenerateWeek}
                disabled={generateWeekMut.isPending}
                data-testid="generate-week-confirm-ok"
              >
                {generateWeekMut.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                )}
                再実行する
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

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
            // (Stage P-3 以降は個別フロー専用。DiffAddDialog は廃止済み)
            enablePoolProposal={patientDetailPoolMode}
            officeId={officeId}
            autoRequestOvercapacity={patientDetailAutoOvercapacity}
            autoRequestUnblock={patientDetailAutoUnblock}
            patientMetaById={patientRowMetaById}
          />
        ) : null}

        {/* 同行モードの下部固定バー (§7.1)。active のときだけ描画。 */}
        {accompaniment.bar ? <AccompanimentBar {...accompaniment.bar} /> : null}

        {/* NG スタッフ §7-2: 同行登録が NG / 性別に抵触したときの確認 (ack 再送)。 */}
        <ConstraintOverrideConfirmDialog {...accompaniment.constraintDialogProps} />
      </section>
    </DndContext>
  );
}
