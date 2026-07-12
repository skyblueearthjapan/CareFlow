'use client';

/**
 * 新人同行モードの司令塔 (§7.1)。
 *
 * 親 (CourseDayTablePanel) から週の visits / コース解決関数などを受け取り、
 *   - 週の全新人の同行リンク取得 (常時表示 §7.2 用)
 *   - モード中は選択中新人のリンクを取得して初期選択に反映
 *   - 選択状態 (コース/個別) の管理・実効集合・時間重複のリアルタイム判定
 *   - 確定 (PUT) と 422 重複の同 UI 表示 (二重防御)
 * をまとめて担い、盤へ渡す 1 つの `binding` と下部バー用の `bar` を返す。
 *
 * 盤 (WeekTimelineBoard/TimelineDayBoard) 本体は表示専用のまま保つ。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { ApiError } from '@/lib/api-client';
import {
  useTraineeAccompaniments,
  useUpdateTraineeAccompaniments,
} from '@/lib/queries/trainee_accompaniments';
import {
  parseOverlapDetail,
  type TraineeAccompanimentOverlap,
} from '@/lib/schemas/trainee_accompaniment';
import type { StaffRead } from '@/lib/schemas/staff';
import {
  computeAccompanimentOverlaps,
  type AccompanimentOverlapEntry,
} from '@/lib/scheduling/accompanimentOverlap';

import type { AccompanimentBinding, AccompanimentWeekVisit } from './types';

export interface UseAccompanimentControllerParams {
  isoYear: number;
  isoWeek: number;
  canEdit: boolean;
  /** active な新人スタッフ (is_trainee=true)。 */
  trainees: StaffRead[];
  /** 週の全訪問 (親が overviewVisits から組む)。 */
  weekVisits: AccompanimentWeekVisit[];
  /** (course_template_id, weekday) → コースインスタンス id。 */
  resolveCourseId: (templateId: string, weekday: number) => string | null;
  /** weekday → 表示用日付ラベル (例: '7/14(月)')。 */
  weekdayDateLabel: (weekday: number) => string;
}

export interface AccompanimentBarProps {
  trainees: StaffRead[];
  selectedTraineeId: string | null;
  onSelectTrainee: (id: string) => void;
  courseCount: number;
  visitCount: number;
  /** クライアント側リアルタイム重複メッセージ。 */
  overlapMessages: string[];
  /** サーバ 422 由来メッセージ (二重防御)。 */
  serverOverlapMessages: string[];
  canConfirm: boolean;
  isSaving: boolean;
  setDefaultChecked: boolean;
  onToggleSetDefault: (checked: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
  /** 選択中新人のリンク初期取得中。 */
  isLoadingLinks: boolean;
}

export interface AccompanimentController {
  available: boolean;
  active: boolean;
  enter: () => void;
  binding: AccompanimentBinding;
  bar: AccompanimentBarProps | null;
}

interface SelectedCourseMeta {
  templateId: string;
  weekday: number;
}

/** サーバ 422 overlap → 下部バー文言。 */
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
  trainees,
  weekVisits,
  resolveCourseId,
  weekdayDateLabel,
}: UseAccompanimentControllerParams): AccompanimentController {
  const [active, setActive] = useState(false);
  const [selectedTraineeId, setSelectedTraineeId] = useState<string | null>(null);
  const [selectedCourses, setSelectedCourses] = useState<Map<string, SelectedCourseMeta>>(
    () => new Map(),
  );
  const [selectedVisitIds, setSelectedVisitIds] = useState<Set<string>>(() => new Set());
  const [setDefaultChecked, setDefaultCheckedState] = useState(false);
  const [serverOverlaps, setServerOverlaps] = useState<TraineeAccompanimentOverlap[]>([]);

  const available = canEdit && trainees.length > 0;

  // 週の全新人リンク (常時表示 §7.2)。モード外でも取得する。
  const displayQuery = useTraineeAccompaniments({ isoYear, isoWeek });

  // 選択中新人のリンク (初期選択の種)。モード中のみ。
  const editingQuery = useTraineeAccompaniments({
    isoYear,
    isoWeek,
    traineeStaffId: selectedTraineeId,
    enabled: active && !!selectedTraineeId,
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

  // --- 常時表示バッジの解決マップ (全新人リンクから) ---------------------------
  const { visitNameMap, courseNameMap } = useMemo(() => {
    const vMap = new Map<string, string>();
    const cMap = new Map<string, string>();
    for (const it of displayQuery.data ?? []) {
      const name = it.trainee_staff_name ?? '';
      if (it.target_type === 'visit' && it.visit?.id) vMap.set(it.visit.id, name);
      if (it.target_type === 'course' && it.course?.id) cMap.set(it.course.id, name);
    }
    return { visitNameMap: vMap, courseNameMap: cMap };
  }, [displayQuery.data]);

  // --- 選択中新人のリンクを初期選択へ (トレーニー切替ごとに 1 回だけ種まき) -------
  const seededForRef = useRef<string | null>(null);
  useEffect(() => {
    if (!active || !selectedTraineeId) return;
    if (editingQuery.data === undefined) return;
    if (seededForRef.current === selectedTraineeId) return;
    seededForRef.current = selectedTraineeId;
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
  }, [active, selectedTraineeId, editingQuery.data]);

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

  const clearServerOverlaps = useCallback(() => {
    setServerOverlaps((prev) => (prev.length ? [] : prev));
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
      clearServerOverlaps();
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
    [clearServerOverlaps, visitsByCourseId],
  );

  const toggleVisit = useCallback(
    (visitId: string) => {
      const v = visitById.get(visitId);
      // 選択済みコース内の訪問は個別トグル不可 (§7.1)。
      if (v?.courseId && selectedCourses.has(v.courseId)) return;
      clearServerOverlaps();
      setSelectedVisitIds((prev) => {
        const next = new Set(prev);
        if (next.has(visitId)) next.delete(visitId);
        else next.add(visitId);
        return next;
      });
    },
    [visitById, selectedCourses, clearServerOverlaps],
  );

  const visitBadgeName = useCallback(
    (visitId: string) => visitNameMap.get(visitId) ?? null,
    [visitNameMap],
  );
  const courseBadgeName = useCallback(
    (courseId: string | null) => (courseId ? (courseNameMap.get(courseId) ?? null) : null),
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
      visitBadgeName,
      courseBadgeName,
      resolveCourseId,
    ],
  );

  // --- モード制御 ------------------------------------------------------------
  const resetSelection = useCallback(() => {
    setSelectedCourses(new Map());
    setSelectedVisitIds(new Set());
    setServerOverlaps([]);
    setDefaultCheckedState(false);
    seededForRef.current = null;
  }, []);

  const enter = useCallback(() => {
    resetSelection();
    setSelectedTraineeId(trainees.length === 1 ? (trainees[0]?.id ?? null) : null);
    setActive(true);
  }, [resetSelection, trainees]);

  const onCancel = useCallback(() => {
    setActive(false);
    setSelectedTraineeId(null);
    resetSelection();
  }, [resetSelection]);

  const onSelectTrainee = useCallback(
    (id: string) => {
      seededForRef.current = null;
      setSelectedCourses(new Map());
      setSelectedVisitIds(new Set());
      setServerOverlaps([]);
      setSelectedTraineeId(id);
    },
    [],
  );

  const onConfirm = useCallback(() => {
    if (!selectedTraineeId) return;
    const courseMetas = [...selectedCourses.values()];
    updateMut.mutate(
      {
        trainee_staff_id: selectedTraineeId,
        iso_year: isoYear,
        iso_week: isoWeek,
        course_ids: [...selectedCourses.keys()],
        visit_ids: [...selectedVisitIds],
        defaults: setDefaultChecked
          ? courseMetas
              .filter((m) => m.templateId)
              .map((m) => ({ weekday: m.weekday, course_template_id: m.templateId }))
          : null,
      },
      {
        onSuccess: () => {
          setActive(false);
          setSelectedTraineeId(null);
          resetSelection();
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 422) {
            const detail = parseOverlapDetail(err.body);
            if (detail) setServerOverlaps(detail.overlaps);
          }
        },
      },
    );
  }, [
    selectedTraineeId,
    selectedCourses,
    selectedVisitIds,
    setDefaultChecked,
    isoYear,
    isoWeek,
    updateMut,
    resetSelection,
  ]);

  const serverOverlapMessages = useMemo(
    () => serverOverlaps.map(serverOverlapToMessage),
    [serverOverlaps],
  );

  // 「毎週の既定にする」時、同一曜日に2コース選択があると BE の UNIQUE(trainee, weekday)
  // で 422 になる (設計 §4.1「1新人×1曜日=1コース既定」)。cryptic な英語 422 を
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
    !!selectedTraineeId &&
    overlap.messages.length === 0 &&
    serverOverlaps.length === 0 &&
    defaultsDuplicateMessages.length === 0 &&
    !updateMut.isPending;

  const bar: AccompanimentBarProps | null = active
    ? {
        trainees,
        selectedTraineeId,
        onSelectTrainee,
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
        isLoadingLinks: !!selectedTraineeId && editingQuery.isLoading,
      }
    : null;

  return { available, active, enter, binding, bar };
}
