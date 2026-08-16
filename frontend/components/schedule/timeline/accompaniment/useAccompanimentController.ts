'use client';

/**
 * 同行モードの司令塔 (§7.1 / general-accompaniment-design.md §4)。
 *
 * 親 (CourseDayTablePanel) から週の visits / コース解決関数などを受け取り、
 *   - 週の全同行リンク取得 (常時表示 §7.2 用・複数名対応)
 *   - モード中は選択中スタッフのリンクを取得して初期選択に反映
 *   - 選択状態 (コース/個別) の管理・実効集合・時間重複のリアルタイム判定
 *   - 対象の二択 (コース(曜日)単位 / 患者単位) による armed target の絞り込み
 *   - 確定 (PUT) と 422 の表示 (重複=理由つき / NG・性別=確認して再送)
 * をまとめて担い、盤へ渡す 1 つの `binding` と下部バー用の `bar` を返す。
 *
 * 盤 (WeekTimelineBoard/TimelineDayBoard) 本体は表示専用のまま保つ。
 *
 * 同行者は新人に限らない (確定#1〜#8)。種別 (kind) は BE が自動判定するため、
 * FE は「誰でも選べる・新人は先頭に出す」だけを担う。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api-client';
import { apiErrorMessage } from '@/lib/api/errorMessage';
import {
  useTraineeAccompaniments,
  useUpdateTraineeAccompaniments,
} from '@/lib/queries/trainee_accompaniments';
import {
  accompanimentStaffName,
  formatAccompanimentConflict,
  parseAccompanimentConflictDetail,
  parseOverlapDetail,
  type AccompanimentConflict,
  type TraineeAccompanimentOverlap,
} from '@/lib/schemas/trainee_accompaniment';
import type { StaffRead } from '@/lib/schemas/staff';
import {
  computeAccompanimentOverlaps,
  type AccompanimentOverlapEntry,
} from '@/lib/scheduling/accompanimentOverlap';
import type { ConstraintOverrideConfirmDialogProps } from '@/components/schedule/v2/ConstraintOverrideConfirmDialog';
import {
  ackFlag,
  useConstraintConfirmRetry,
  type ConstraintConfirmText,
} from '@/components/schedule/v2/useConstraintConfirmRetry';

import type { AccompanimentBinding, AccompanimentWeekVisit } from './types';

/** 対象の二択 (確定#2: コース(曜日)単位 / 患者単位。週一括は作らない)。 */
export type AccompanimentTargetMode = 'course' | 'visit';

/** NG スタッフ / 性別制限の確認ダイアログ文言 (同行経路)。 */
export const ACCOMPANIMENT_CONSTRAINT_CONFIRM_TEXT: ConstraintConfirmText = {
  title: 'それでも同行として登録しますか？',
  description: 'NGスタッフ/性別制限に該当しますが、同行として登録しますか',
  confirmLabel: '同行として登録する',
};

export interface UseAccompanimentControllerParams {
  isoYear: number;
  isoWeek: number;
  canEdit: boolean;
  /** 同行者に選べる active スタッフ (新人が先頭に来る順で親が渡す)。 */
  staffOptions: StaffRead[];
  /** 週の全訪問 (親が overviewVisits から組む)。 */
  weekVisits: AccompanimentWeekVisit[];
  /** (course_template_id, weekday) → コースインスタンス id。 */
  resolveCourseId: (templateId: string, weekday: number) => string | null;
  /** weekday → 表示用日付ラベル (例: '7/14(月)')。 */
  weekdayDateLabel: (weekday: number) => string;
}

export interface AccompanimentBarProps {
  /** 同行者候補 (新人 + 一般スタッフ)。 */
  staffOptions: StaffRead[];
  selectedStaffId: string | null;
  onSelectStaff: (id: string) => void;
  /** 対象の二択 (armed target の絞り込み)。 */
  targetMode: AccompanimentTargetMode;
  onChangeTargetMode: (mode: AccompanimentTargetMode) => void;
  courseCount: number;
  visitCount: number;
  /** クライアント側リアルタイム重複メッセージ。 */
  overlapMessages: string[];
  /** サーバ 422 由来メッセージ (二重防御・理由つき)。 */
  serverOverlapMessages: string[];
  canConfirm: boolean;
  isSaving: boolean;
  setDefaultChecked: boolean;
  onToggleSetDefault: (checked: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
  /** 選択中スタッフのリンク初期取得中。 */
  isLoadingLinks: boolean;
}

export interface AccompanimentController {
  available: boolean;
  active: boolean;
  enter: () => void;
  binding: AccompanimentBinding;
  bar: AccompanimentBarProps | null;
  /** NG/性別 422 の確認ダイアログ props (親が ConstraintOverrideConfirmDialog へ spread)。 */
  constraintDialogProps: ConstraintOverrideConfirmDialogProps;
}

interface SelectedCourseMeta {
  templateId: string;
  weekday: number;
}

/** サーバ 422 overlap (旧形) → 下部バー文言。 */
function serverOverlapToMessage(o: TraineeAccompanimentOverlap): string {
  const side = (s: TraineeAccompanimentOverlap['a']) => {
    const name = s.patient_name ?? '—';
    const label = s.course_code ? `（${s.course_code}）` : '';
    return `${(s.start ?? '').slice(0, 5)} ${name}${label}`;
  };
  return `⚠ 時間が重複しています: ${o.date} ${side(o.a)} × ${side(o.b)} — 同時には行けません`;
}

export function useAccompanimentController({
  isoYear,
  isoWeek,
  canEdit,
  staffOptions,
  weekVisits,
  resolveCourseId,
  weekdayDateLabel,
}: UseAccompanimentControllerParams): AccompanimentController {
  const [active, setActive] = useState(false);
  const [selectedStaffId, setSelectedStaffId] = useState<string | null>(null);
  const [targetMode, setTargetMode] = useState<AccompanimentTargetMode>('course');
  const [selectedCourses, setSelectedCourses] = useState<Map<string, SelectedCourseMeta>>(
    () => new Map(),
  );
  const [selectedVisitIds, setSelectedVisitIds] = useState<Set<string>>(() => new Set());
  const [setDefaultChecked, setDefaultCheckedState] = useState(false);
  const [serverOverlaps, setServerOverlaps] = useState<TraineeAccompanimentOverlap[]>([]);
  const [serverConflicts, setServerConflicts] = useState<AccompanimentConflict[]>([]);

  const available = canEdit && staffOptions.length > 0;

  const constraintConfirm = useConstraintConfirmRetry();

  // 週の全同行リンク (常時表示 §7.2)。モード外でも取得する。
  const displayQuery = useTraineeAccompaniments({ isoYear, isoWeek });

  // 選択中スタッフのリンク (初期選択の種)。モード中のみ。
  const editingQuery = useTraineeAccompaniments({
    isoYear,
    isoWeek,
    traineeStaffId: selectedStaffId,
    enabled: active && !!selectedStaffId,
  });

  const updateMut = useUpdateTraineeAccompaniments();

  // weekVisits を id 索引化。
  const visitById = useMemo(() => {
    const m = new Map<string, AccompanimentWeekVisit>();
    for (const v of weekVisits) m.set(v.visitId, v);
    return m;
  }, [weekVisits]);

  // courseId → その訪問群 (実効集合の展開に使う)。
  const visitsByCourseId = useMemo(() => {
    const m = new Map<string, AccompanimentWeekVisit[]>();
    for (const v of weekVisits) {
      if (!v.courseId) continue;
      const arr = m.get(v.courseId);
      if (arr) arr.push(v);
      else m.set(v.courseId, [v]);
    }
    return m;
  }, [weekVisits]);

  // --- 常時表示バッジの解決マップ (全リンクから・1 対象に複数名) ----------------
  const { visitNameMap, courseNameMap } = useMemo(() => {
    const vMap = new Map<string, string[]>();
    const cMap = new Map<string, string[]>();
    const push = (map: Map<string, string[]>, key: string, name: string) => {
      if (!name) return;
      const arr = map.get(key);
      if (!arr) map.set(key, [name]);
      else if (!arr.includes(name)) arr.push(name);
    };
    for (const it of displayQuery.data ?? []) {
      const name = accompanimentStaffName(it);
      if (it.target_type === 'visit' && it.visit?.id) push(vMap, it.visit.id, name);
      if (it.target_type === 'course' && it.course?.id) push(cMap, it.course.id, name);
    }
    // 表示順を安定させる (取得順に依存しない)。
    for (const arr of vMap.values()) arr.sort((a, b) => a.localeCompare(b, 'ja'));
    for (const arr of cMap.values()) arr.sort((a, b) => a.localeCompare(b, 'ja'));
    return { visitNameMap: vMap, courseNameMap: cMap };
  }, [displayQuery.data]);

  // --- 選択中スタッフのリンクを初期選択へ (切替ごとに 1 回だけ種まき) ------------
  const seededForRef = useRef<string | null>(null);
  useEffect(() => {
    if (!active || !selectedStaffId) return;
    if (editingQuery.data === undefined) return;
    if (seededForRef.current === selectedStaffId) return;
    seededForRef.current = selectedStaffId;
    const courses = new Map<string, SelectedCourseMeta>();
    const visitIds = new Set<string>();
    for (const it of editingQuery.data) {
      if (it.target_type === 'course' && it.course?.id) {
        courses.set(it.course.id, {
          templateId: it.course.template_id ?? '',
          weekday: it.course.weekday,
        });
      }
      if (it.target_type === 'visit' && it.visit?.id) {
        visitIds.add(it.visit.id);
      }
    }
    setSelectedCourses(courses);
    setSelectedVisitIds(visitIds);
    setServerOverlaps([]);
    setServerConflicts([]);
  }, [active, selectedStaffId, editingQuery.data]);

  // --- 実効同行訪問集合 (コース内全訪問 ∪ 個別) -------------------------------
  const effectiveVisitIds = useMemo(() => {
    const set = new Set<string>();
    for (const courseId of selectedCourses.keys()) {
      for (const v of visitsByCourseId.get(courseId) ?? []) set.add(v.visitId);
    }
    for (const id of selectedVisitIds) set.add(id);
    return set;
  }, [selectedCourses, selectedVisitIds, visitsByCourseId]);

  // --- リアルタイム重複判定 --------------------------------------------------
  const overlap = useMemo(() => {
    const entries: AccompanimentOverlapEntry[] = [];
    for (const id of effectiveVisitIds) {
      const v = visitById.get(id);
      if (!v) continue;
      entries.push({
        visitId: v.visitId,
        dayKey: v.weekday,
        dayLabel: weekdayDateLabel(v.weekday),
        startMin: v.startMin,
        endMin: v.endMin,
        patientName: v.patientName,
        courseLabel: v.courseLabel,
        sameAddressKey: v.sameAddressKey,
      });
    }
    return computeAccompanimentOverlaps(entries);
  }, [effectiveVisitIds, visitById, weekdayDateLabel]);

  const clearServerErrors = useCallback(() => {
    setServerOverlaps((prev) => (prev.length ? [] : prev));
    setServerConflicts((prev) => (prev.length ? [] : prev));
  }, []);

  // --- binding 用コールバック ------------------------------------------------
  const isCourseSelected = useCallback(
    (courseId: string | null) => (courseId ? selectedCourses.has(courseId) : false),
    [selectedCourses],
  );
  const isVisitSelected = useCallback(
    (visitId: string) => effectiveVisitIds.has(visitId),
    [effectiveVisitIds],
  );
  const isVisitInSelectedCourse = useCallback(
    (visitId: string) => {
      const v = visitById.get(visitId);
      return !!v?.courseId && selectedCourses.has(v.courseId);
    },
    [visitById, selectedCourses],
  );
  const isVisitOverlapping = useCallback(
    (visitId: string) => overlap.overlapVisitIds.has(visitId),
    [overlap.overlapVisitIds],
  );

  const toggleCourse = useCallback(
    (courseId: string | null, templateId: string, weekday: number) => {
      if (!courseId) return;
      // 患者単位モード中はコースヘッダを受け付けない (armed target の絞り込み)。
      if (targetMode !== 'course') return;
      clearServerErrors();
      setSelectedCourses((prev) => {
        const next = new Map(prev);
        if (next.has(courseId)) next.delete(courseId);
        else next.set(courseId, { templateId, weekday });
        return next;
      });
      // コース選択時、そのコース内の個別選択は冗長になるため落とす。
      setSelectedVisitIds((prev) => {
        if (prev.size === 0) return prev;
        const members = visitsByCourseId.get(courseId);
        if (!members || members.length === 0) return prev;
        const memberIds = new Set(members.map((m) => m.visitId));
        let changed = false;
        const next = new Set(prev);
        for (const id of prev) {
          if (memberIds.has(id)) {
            next.delete(id);
            changed = true;
          }
        }
        return changed ? next : prev;
      });
    },
    [targetMode, clearServerErrors, visitsByCourseId],
  );

  const toggleVisit = useCallback(
    (visitId: string) => {
      // コース単位モード中は患者カードを受け付けない。
      if (targetMode !== 'visit') return;
      const v = visitById.get(visitId);
      // 選択済みコース内の訪問は個別トグル不可 (§7.1)。
      if (v?.courseId && selectedCourses.has(v.courseId)) return;
      clearServerErrors();
      setSelectedVisitIds((prev) => {
        const next = new Set(prev);
        if (next.has(visitId)) next.delete(visitId);
        else next.add(visitId);
        return next;
      });
    },
    [targetMode, visitById, selectedCourses, clearServerErrors],
  );

  const visitBadgeName = useCallback(
    (visitId: string) => visitNameMap.get(visitId) ?? [],
    [visitNameMap],
  );
  const courseBadgeName = useCallback(
    (courseId: string | null) => (courseId ? (courseNameMap.get(courseId) ?? []) : []),
    [courseNameMap],
  );

  const binding: AccompanimentBinding = useMemo(
    () => ({
      active,
      isCourseSelected,
      isVisitSelected,
      isVisitInSelectedCourse,
      isVisitOverlapping,
      toggleCourse,
      toggleVisit,
      isCourseArmed: targetMode === 'course',
      isVisitArmed: targetMode === 'visit',
      visitBadgeName,
      courseBadgeName,
      resolveCourseId,
    }),
    [
      active,
      isCourseSelected,
      isVisitSelected,
      isVisitInSelectedCourse,
      isVisitOverlapping,
      toggleCourse,
      toggleVisit,
      targetMode,
      visitBadgeName,
      courseBadgeName,
      resolveCourseId,
    ],
  );

  // --- モード制御 ------------------------------------------------------------
  /**
   * 選択・警告・「毎週の既定」チェック・対象の二択をすべて初期状態へ戻す。
   * スタッフ切替でも必ずこれを通す (前スタッフのチェック状態が残ったまま確定して
   * 意図しない無期限の既定が書かれる事故を防ぐ)。
   */
  const resetSelection = useCallback(() => {
    setSelectedCourses(new Map());
    setSelectedVisitIds(new Set());
    setServerOverlaps([]);
    setServerConflicts([]);
    setDefaultCheckedState(false);
    setTargetMode('course');
    seededForRef.current = null;
  }, []);

  const enter = useCallback(() => {
    resetSelection();
    setSelectedStaffId(staffOptions.length === 1 ? (staffOptions[0]?.id ?? null) : null);
    setActive(true);
  }, [resetSelection, staffOptions]);

  const onCancel = useCallback(() => {
    setActive(false);
    setSelectedStaffId(null);
    resetSelection();
    constraintConfirm.reset();
  }, [resetSelection, constraintConfirm]);

  const onSelectStaff = useCallback(
    (id: string) => {
      resetSelection();
      constraintConfirm.reset();
      setSelectedStaffId(id);
    },
    [resetSelection, constraintConfirm],
  );

  const onChangeTargetMode = useCallback(
    (mode: AccompanimentTargetMode) => {
      // 対象を切り替えたら前の対象で出た 422 理由は無効なので消す。
      clearServerErrors();
      setTargetMode(mode);
    },
    [clearServerErrors],
  );

  /**
   * 確定 PUT。NG/性別の 422 は確認ダイアログ → ack つきで自己再送する
   * (`patient-ng-staff-design.md` §7-2 と同型)。重複 422 は理由つきで下部バーへ。
   */
  const onConfirm = useCallback(() => {
    if (!selectedStaffId) return;
    const courseMetas = [...selectedCourses.values()];
    const payload = {
      staff_id: selectedStaffId,
      iso_year: isoYear,
      iso_week: isoWeek,
      course_ids: [...selectedCourses.keys()],
      visit_ids: [...selectedVisitIds],
      defaults: setDefaultChecked
        ? courseMetas
            .filter((m) => m.templateId)
            .map((m) => ({ weekday: m.weekday, course_template_id: m.templateId }))
        : null,
    };
    const run = async (acknowledge: boolean): Promise<void> => {
      try {
        await updateMut.mutateAsync({ ...payload, ...ackFlag(acknowledge) });
        setActive(false);
        setSelectedStaffId(null);
        resetSelection();
      } catch (err) {
        // NG/性別 → 確認ダイアログ (OK なら ack つきで再送)。
        if (!acknowledge) {
          const captured = constraintConfirm.capture(
            err,
            () => run(true),
            ACCOMPANIMENT_CONSTRAINT_CONFIRM_TEXT,
          );
          if (captured) return;
        }
        if (err instanceof ApiError && err.status === 422) {
          const conflict = parseAccompanimentConflictDetail(err.body);
          if (conflict) {
            setServerConflicts(conflict.conflicts);
            return;
          }
          const legacy = parseOverlapDetail(err.body);
          if (legacy) {
            setServerOverlaps(legacy.overlaps);
            return;
          }
        }
        // それ以外 (500/ネットワーク/ack 再送でも通らなかった 422 など) は
        // 無音にせずトーストで必ず知らせる。無音の失敗は「押したのに保存された
        // つもり」を生む。
        toast.error(`同行の保存に失敗しました: ${apiErrorMessage(err)}`);
      }
    };
    void run(false);
  }, [
    selectedStaffId,
    selectedCourses,
    selectedVisitIds,
    setDefaultChecked,
    isoYear,
    isoWeek,
    updateMut,
    resetSelection,
    constraintConfirm,
  ]);

  const serverOverlapMessages = useMemo(
    () => [
      ...serverConflicts.map((c) => `⚠ ${formatAccompanimentConflict(c)}`),
      ...serverOverlaps.map(serverOverlapToMessage),
    ],
    [serverConflicts, serverOverlaps],
  );

  // 「毎週の既定にする」時、同一曜日に2コース選択があると BE の UNIQUE(staff, weekday)
  // で 422 になる (設計 §4.1「1スタッフ×1曜日=1コース既定」)。cryptic な英語 422 を
  // 見せないよう、FE で事前検知して日本語警告＋確定ブロックにする。
  const defaultsDuplicateMessages = useMemo(() => {
    if (!setDefaultChecked) return [] as string[];
    const seen = new Map<number, number>();
    for (const meta of selectedCourses.values()) {
      if (!meta.templateId) continue;
      seen.set(meta.weekday, (seen.get(meta.weekday) ?? 0) + 1);
    }
    const dup = [...seen.entries()].filter(([, n]) => n > 1);
    if (dup.length === 0) return [] as string[];
    const days = ['月', '火', '水', '木', '金', '土', '日'];
    return dup.map(
      ([wd]) =>
        `毎週の既定は1曜日につき1コースまでです（${days[wd] ?? wd}曜に${seen.get(wd)}コース選択中）。チェックを外すか、コース選択を1つにしてください`,
    );
  }, [setDefaultChecked, selectedCourses]);

  const canConfirm =
    active &&
    !!selectedStaffId &&
    overlap.messages.length === 0 &&
    serverOverlaps.length === 0 &&
    serverConflicts.length === 0 &&
    defaultsDuplicateMessages.length === 0 &&
    !updateMut.isPending;

  const bar: AccompanimentBarProps | null = active
    ? {
        staffOptions,
        selectedStaffId,
        onSelectStaff,
        targetMode,
        onChangeTargetMode,
        courseCount: selectedCourses.size,
        visitCount: selectedVisitIds.size,
        overlapMessages: overlap.messages,
        // 既定の曜日重複警告も同じ警告領域に出す (表示上は区別しない)。
        serverOverlapMessages: [...serverOverlapMessages, ...defaultsDuplicateMessages],
        canConfirm,
        isSaving: updateMut.isPending,
        setDefaultChecked,
        onToggleSetDefault: setDefaultCheckedState,
        onConfirm,
        onCancel,
        isLoadingLinks: !!selectedStaffId && editingQuery.isLoading,
      }
    : null;

  return {
    available,
    active,
    enter,
    binding,
    bar,
    constraintDialogProps: constraintConfirm.dialogProps,
  };
}
