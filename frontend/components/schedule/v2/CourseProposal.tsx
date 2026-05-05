'use client';

/**
 * CourseProposal — Layer 2 のコース案生成 + 微調整 + 確定 UI (W4-FE8).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.3 に対応。
 * 1 曜日に対し「コース案を生成 (POST /courses/generate) → 案を 4 行に表示
 * → ドラッグでコース間移動 → コース固定 (POST /courses)」の一連フローを
 * 1 コンポーネントで担う。
 *
 * レイアウト:
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ 曜日選択 [月] [火] [水] [木] [金] [土] [日]              │
 *   │ 稼働スタッフ数: [4 ▼]   [コース案を生成]  [コース固定]   │
 *   ├──────────────────────────────────────────────────────────┤
 *   │ M | … (オーバーフロー)                                    │
 *   │ A | 患A 患B 患C 患D 患E 患F                              │
 *   │ B | 患G 患H 患I 患J 患K 患L                              │
 *   │ C | …                                                     │
 *   │ D | …                                                     │
 *   └──────────────────────────────────────────────────────────┘
 *
 * 状態:
 *   - assignmentByCode: Map<CourseCodeV2, patient_ids[]> — ローカル唯一の真実
 *   - 「コース案を生成」成功時に proposal で上書き
 *   - ドラッグで Map を更新 (UI のみ。BE 反映は「コース固定」時)
 *   - 「コース固定」で useFixCourses を実行
 *
 * RBAC: `canEdit=false` の場合はボタン / ドラッグを全部無効化。
 *
 * W4-BE8 並行作業中の挙動:
 *   - POST /courses/generate が 404 を返した場合、UI 側でエラー toast 表示し
 *     空の案 (各行 0 名) で初期化されるようハンドリングする。BE 完了前でも
 *     ドラッグ操作 / 固定ボタン (= 既存 CRUD のみ使用) は動作する。
 *
 * W4-FE9 への申し送り:
 *   - CourseRow.tsx に `assignedStaffSlot` prop が用意されている (W4-FE8 で
 *     先行整備済み)。本コンポーネントから CourseRow へ render slot を
 *     渡す経路はまだ無く、W4-FE9 側で props 拡張 or 親コンポーネント差し替え
 *     のいずれかを選んで実装する。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { Sparkles, Lock } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ApiError } from '@/lib/api-client';
import { usePatients } from '@/lib/queries/patients';
import {
  useCourses,
  useFixCourses,
  useGenerateCourseProposals,
  type CourseCodeV2,
  type CourseFixEntry,
} from '@/lib/queries/courses';
import { useStaffList } from '@/lib/queries/staff';

import { CourseRow, parseCourseDroppableId, parseCoursePatientDraggableId } from './CourseRow';
import type { PatientCardData } from './PatientCard';
import { StaffAssignButton, indexAssignmentsByWeekdayAndCourse } from './StaffAssignButton';

// ─────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** §3.6.3: 通常コース 4 つ + M (マネージャー枠). */
const ALL_CODES: CourseCodeV2[] = ['M', 'A', 'B', 'C', 'D'];

/** §3.6.3: 1 通常コースあたりの上限. M は枠外 (上限なし). */
const COURSE_CAPACITY = 6;

// ─────────────────────────────────────────────────────────────────────────
// State helpers
// ─────────────────────────────────────────────────────────────────────────

type AssignmentMap = Map<CourseCodeV2, string[]>;

function emptyAssignment(): AssignmentMap {
  return new Map<CourseCodeV2, string[]>(ALL_CODES.map((c) => [c, []]));
}

/** 案レスポンスを Map に変換. patient_ids が無いコースは空配列。 */
function proposalToAssignment(
  courses: Array<{ code: CourseCodeV2; patient_ids?: string[] }>,
): AssignmentMap {
  const m = emptyAssignment();
  for (const c of courses) {
    if (!ALL_CODES.includes(c.code)) continue;
    m.set(c.code, [...(c.patient_ids ?? [])]);
  }
  return m;
}

// ─────────────────────────────────────────────────────────────────────────
// W4-FE9: assigned staff slot renderer
// ─────────────────────────────────────────────────────────────────────────

interface RenderSlotArgs {
  code: CourseCodeV2;
  weekday: number;
  /** 親から prop で渡された slot. 指定があれば最優先で使う (案 A). */
  externalSlot: ReactNode | undefined;
  /** (weekday:code) → course_id 解決テーブル. */
  courseIdByDayCode: Map<string, string>;
  staffAssignByDay: Map<number, Map<string, string>>;
  staffOverrideByDay: Map<number, Map<string, string>>;
  staffMeta: Map<string, { id: string; name: string; code: string | null }>;
  overrideCandidates: ReadonlyArray<{ id: string; name: string; code?: string | null }>;
  canEdit: boolean;
  onOverride: (courseId: string, nextStaffId: string) => void;
}

/**
 * 1 行分の「割付済みスタッフ」表示 slot を組み立てる。
 *
 * 優先順位:
 *   1. 親からの `externalSlot` (prop) があればそれを返す (テスト用エスケープハッチ)
 *   2. 当該 (weekday, code) に対応する Course レコードが無い場合 → null (空 slot)
 *   3. ユーザの手動上書き (`staffOverrideByDay`) があればそれを優先
 *   4. 自動割付結果 (`staffAssignByDay`) があれば表示
 *   5. いずれも無ければ「未割当」プレースホルダ
 */
function renderAssignedStaffSlot(args: RenderSlotArgs): ReactNode | undefined {
  const {
    code,
    weekday,
    externalSlot,
    courseIdByDayCode,
    staffAssignByDay,
    staffOverrideByDay,
    staffMeta,
    overrideCandidates,
    canEdit,
    onOverride,
  } = args;

  // 1. 外部 slot が最優先
  if (externalSlot !== undefined) return externalSlot;

  // 2. course_id を引く
  const courseId = courseIdByDayCode.get(`${weekday}:${code}`);
  if (!courseId) {
    // 該当 Course レコード未存在 (= まだ「コース固定」していない / Layer 3 未実行).
    // assignedStaffSlot は undefined にすることで CourseRow 側で枠を非表示にする.
    return undefined;
  }

  // 3. override > 4. auto-assign の優先順
  const overrideStaffId = staffOverrideByDay.get(weekday)?.get(courseId);
  const autoStaffId = staffAssignByDay.get(weekday)?.get(courseId);
  const effectiveStaffId = overrideStaffId ?? autoStaffId ?? null;

  // 5. 未割当
  const meta = effectiveStaffId ? staffMeta.get(effectiveStaffId) : null;
  const labelMain = meta ? (meta.code ?? meta.name) : effectiveStaffId ? '(不明)' : '未割当';
  const labelSub = meta ? meta.name : null;

  return (
    <div className="flex w-full flex-col items-stretch gap-1 text-[10px]">
      <div
        className={
          'flex flex-col rounded border px-1.5 py-1 leading-tight ' +
          (effectiveStaffId
            ? overrideStaffId
              ? 'border-warning/60 bg-warning/5 text-warning'
              : 'border-brand-primary/40 bg-brand-primary/5 text-brand-primary'
            : 'border-dashed border-border-default bg-bg-base text-text-muted')
        }
        title={
          overrideStaffId
            ? '手動で上書きされたスタッフ (BE 未反映)'
            : autoStaffId
              ? '自動割付されたスタッフ'
              : 'まだスタッフが割り当てられていません'
        }
      >
        <span className="truncate text-[11px] font-semibold">{labelMain}</span>
        {labelSub ? <span className="truncate text-text-muted">{labelSub}</span> : null}
      </div>

      {/* 基本的な UI 上の上書き (受入基準: 異常時に手動で staff_id 変更可能). */}
      {canEdit ? (
        <select
          value={effectiveStaffId ?? ''}
          onChange={(e) => onOverride(courseId, e.target.value)}
          aria-label={`${code} コースのスタッフを上書き`}
          className="min-w-0 rounded border border-border-default bg-bg-base px-1 py-0.5 text-[10px]"
        >
          <option value="">— 未割当 —</option>
          {overrideCandidates.map((s) => (
            <option key={s.id} value={s.id}>
              {(s.code ? `${s.code} / ` : '') + s.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────────────────────────────────

export interface CourseProposalProps {
  /** ISO 8601 年週. 親 (page.tsx) で WeekSelector と同期する想定. */
  isoYear: number;
  isoWeek: number;
  /** 初期表示曜日 (0=Mon..6=Sun). 内部で切替可能. */
  initialWeekday?: number;
  /** RBAC: admin / manager のみ true. */
  canEdit: boolean;
  /** 稼働スタッフ数 (= 通常コース数) のデフォルト. 未指定なら 4. */
  defaultStaffCount?: number;
  /**
   * W4-FE9: 各コースに対応するスタッフ表示要素を外側から差し込むための slot.
   *
   * 通常は本コンポーネント内部で `StaffAssignButton` の結果を保持して
   * 自動表示するため未指定で良い。テスト・Storybook・上位コンポーネント
   * からの強制表示が必要な場合のみ指定する (案 A: prop による拡張).
   */
  assignedStaffByCode?: Partial<Record<CourseCodeV2, ReactNode>>;
}

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

export function CourseProposal({
  isoYear,
  isoWeek,
  initialWeekday = 0,
  canEdit,
  defaultStaffCount = 4,
  assignedStaffByCode,
}: CourseProposalProps) {
  const [weekday, setWeekday] = useState<number>(initialWeekday);
  const [staffCount, setStaffCount] = useState<number>(defaultStaffCount);

  // 各コードの患者 ID 一覧 (= ローカル唯一の真実).
  const [assignment, setAssignment] = useState<AssignmentMap>(() => emptyAssignment());

  // 曜日切替時にリセット (案を引き継がない).
  useEffect(() => {
    setAssignment(emptyAssignment());
  }, [weekday, isoYear, isoWeek]);

  // ─────────────── W4-FE9: スタッフ割付状態 ───────────────
  // 当該週分の割付結果。weekday → courseId → staffId.
  // StaffAssignButton の onAssigned で更新され、
  // CourseRow の `assignedStaffSlot` 経由で表示する。
  const [staffAssignByDay, setStaffAssignByDay] = useState<Map<number, Map<string, string>>>(
    () => new Map(),
  );
  // ユーザが UI 上で手動で staff_id を上書きした分。同じく weekday → courseId → staffId.
  // BE 反映は本チケット範囲外。「異常時に手動で staff_id 変更可能」 (受入基準) のため
  // 純粋に画面表示だけ差し替えるのが目的。
  const [staffOverrideByDay, setStaffOverrideByDay] = useState<Map<number, Map<string, string>>>(
    () => new Map(),
  );

  // 週が変わったらクリア (曜日切替では維持して同じ週内の表示を保つ).
  useEffect(() => {
    setStaffAssignByDay(new Map());
    setStaffOverrideByDay(new Map());
  }, [isoYear, isoWeek]);

  // 患者マスタ (表示用 name/kana 解決).
  const patientsQuery = usePatients({ limit: 500 });
  const patientMeta = useMemo(() => {
    const map = new Map<string, PatientCardData>();
    for (const p of patientsQuery.data?.items ?? []) {
      map.set(p.id, {
        id: p.id,
        name: p.name,
        caption: p.kana ?? undefined,
      });
    }
    return map;
  }, [patientsQuery.data]);

  // W4-FE9: 当該週の courses を取得し、course.id → code の逆引きと
  //          weekday → code → course.id の対応表を作る。
  //          BE が返す StaffAssignmentEntry.course_id を code に変換するのに使う。
  const coursesQuery = useCourses({ iso_year: isoYear, iso_week: isoWeek, limit: 100 });
  const courseRows = coursesQuery.data ?? [];

  const courseIdByDayCode = useMemo(() => {
    // (weekday, code) → course_id (= 当該週で唯一の Course レコード).
    const map = new Map<string, string>();
    for (const c of courseRows) {
      map.set(`${c.weekday}:${c.code}`, c.id);
    }
    return map;
  }, [courseRows]);

  // W4-FE9: スタッフマスタ (staff_id → 表示名解決).
  const staffListQuery = useStaffList({ limit: 500 });
  const staffMeta = useMemo(() => {
    const map = new Map<string, { id: string; name: string; code: string | null }>();
    for (const s of staffListQuery.data ?? []) {
      map.set(s.id, { id: s.id, name: s.name, code: s.code ?? null });
    }
    return map;
  }, [staffListQuery.data]);

  // 同 weekday に勤務可能なスタッフ一覧 (手動上書きの選択肢候補).
  // 厳密な勤務曜日フィルタは backend 側で持っているため、UI では
  // active かつ role !== manager のみ抜粋する簡易版とする (受入基準は
  // 「基本的な UI 上の上書き」程度)。
  const overrideCandidates = useMemo(() => {
    return (staffListQuery.data ?? []).filter((s) => s.status === 'active' && s.role !== 'manager');
  }, [staffListQuery.data]);

  // ─────────────── Mutations ───────────────
  const generateMut = useGenerateCourseProposals();
  const fixMut = useFixCourses();

  const handleGenerate = async () => {
    if (!canEdit || generateMut.isPending) return;
    try {
      const res = await generateMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        weekday,
        staff_count: staffCount,
      });
      setAssignment(proposalToAssignment(res.courses));
      toast.success(
        `案を生成しました: 総距離 ${res.total_distance_km.toFixed(1)} km / 妥当性 ${(
          res.validity_score * 100
        ).toFixed(0)}%`,
      );
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`コース案生成に失敗しました: ${msg}`);
    }
  };

  const handleFix = async () => {
    if (!canEdit || fixMut.isPending) return;
    const entries: CourseFixEntry[] = ALL_CODES.map((code) => ({
      code,
      patient_ids: assignment.get(code) ?? [],
    }));
    try {
      const res = await fixMut.mutateAsync({
        iso_year: isoYear,
        iso_week: isoWeek,
        weekday,
        entries,
      });
      const skippedNote =
        res.visits_skipped > 0 ? ` (visit 紐付けスキップ: ${res.visits_skipped})` : '';
      toast.success(
        `コース構成を確定しました: ${res.course_ids.length} コース / 訪問 ${res.visits_updated} 件更新${skippedNote}`,
      );
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? `${err.status} ${err.message}`
          : err instanceof Error
            ? err.message
            : '不明なエラー';
      toast.error(`コース固定に失敗しました: ${msg}`);
    }
  };

  // ─────────────── DnD ───────────────
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
  );
  const [activePatientId, setActivePatientId] = useState<string | null>(null);

  const handleDragStart = (e: DragStartEvent) => {
    const pid = parseCoursePatientDraggableId(String(e.active.id));
    setActivePatientId(pid);
  };

  const handleDragEnd = (e: DragEndEvent) => {
    setActivePatientId(null);
    const { active, over } = e;
    if (!over) return;
    const patientId = parseCoursePatientDraggableId(String(active.id));
    if (!patientId) return;
    const targetCode = parseCourseDroppableId(String(over.id));
    if (!targetCode) return;

    setAssignment((prev) => {
      // 既に target に属していれば no-op.
      const targetList = prev.get(targetCode) ?? [];
      if (targetList.includes(patientId)) return prev;

      const next = new Map(prev);
      // 全コードから除去
      for (const code of ALL_CODES) {
        const list = next.get(code) ?? [];
        const filtered = list.filter((id) => id !== patientId);
        if (filtered.length !== list.length) {
          next.set(code, filtered);
        }
      }
      // target に追記
      next.set(targetCode, [...(next.get(targetCode) ?? []), patientId]);
      return next;
    });
  };

  // ─────────────── Derived ───────────────
  const totalAssigned = useMemo(() => {
    let n = 0;
    for (const list of assignment.values()) n += list.length;
    return n;
  }, [assignment]);

  const hasOverflow = useMemo(() => {
    for (const code of ALL_CODES) {
      if (code === 'M') continue; // M は上限なし
      const list = assignment.get(code) ?? [];
      if (list.length > COURSE_CAPACITY) return true;
    }
    return false;
  }, [assignment]);

  const isGenerating = generateMut.isPending;
  const isFixing = fixMut.isPending;

  // ─────────────── render ───────────────
  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <Card className="space-y-3 p-3">
        {/* ヘッダー: 曜日 / スタッフ数 / アクション */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-text-secondary">曜日</span>
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="曜日">
              {WEEKDAY_LABELS.map((lbl, i) => (
                <button
                  key={lbl}
                  type="button"
                  role="tab"
                  aria-selected={weekday === i}
                  onClick={() => setWeekday(i)}
                  className={
                    'rounded border px-2 py-1 text-xs font-semibold ' +
                    (weekday === i
                      ? 'border-brand-primary bg-brand-primary text-white'
                      : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted')
                  }
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-1 text-xs text-text-secondary">
              スタッフ数
              <select
                value={staffCount}
                onChange={(e) => setStaffCount(Number(e.target.value))}
                disabled={!canEdit || isGenerating}
                className="rounded border border-border-default bg-bg-base px-1 py-0.5 text-xs"
              >
                {[2, 3, 4, 5, 6].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>

            <Button
              type="button"
              variant="outline"
              onClick={handleGenerate}
              disabled={!canEdit || isGenerating}
              className="gap-1"
            >
              <Sparkles className="h-4 w-4" aria-hidden />
              {isGenerating ? '生成中…' : 'コース案を生成'}
            </Button>

            <Button
              type="button"
              onClick={handleFix}
              disabled={!canEdit || isFixing || totalAssigned === 0}
              className="gap-1"
              title={hasOverflow ? '通常コースの上限 (6 名) を超えています' : undefined}
            >
              <Lock className="h-4 w-4" aria-hidden />
              {isFixing ? '保存中…' : 'コース固定'}
            </Button>

            {/* W4-FE9: スタッフ割付実行 (Layer 3). */}
            <StaffAssignButton
              isoYear={isoYear}
              isoWeek={isoWeek}
              canEdit={canEdit}
              totalCourses={courseRows.length}
              onAssigned={(res) => {
                // 受け取った assignments を weekday → courseId → staffId に展開して保持
                setStaffAssignByDay(indexAssignmentsByWeekdayAndCourse(res.assignments));
                // 自動再割付したので、過去の手動上書きはクリアする
                setStaffOverrideByDay(new Map());
              }}
            />
          </div>
        </div>

        {/* メタ情報 */}
        <div className="flex flex-wrap items-center gap-3 text-xs text-text-muted">
          <span className="tnum">
            {WEEKDAY_LABELS[weekday]}曜 / ISO {isoYear}-W{String(isoWeek).padStart(2, '0')}
          </span>
          <span className="tnum">割当 {totalAssigned} 名</span>
          {hasOverflow ? (
            <span className="font-semibold text-error">
              ※ 上限超過コースあり (1 コース最大 {COURSE_CAPACITY} 名)
            </span>
          ) : null}
        </div>

        {/* コース行 (M / A / B / C / D の順) */}
        <div className="space-y-2">
          {ALL_CODES.map((code) => {
            const ids = assignment.get(code) ?? [];
            const patients = ids.map(
              (id): PatientCardData => patientMeta.get(id) ?? { id, name: id, caption: undefined },
            );
            const slot = renderAssignedStaffSlot({
              code,
              weekday,
              externalSlot: assignedStaffByCode?.[code],
              courseIdByDayCode,
              staffAssignByDay,
              staffOverrideByDay,
              staffMeta,
              overrideCandidates,
              canEdit,
              onOverride: (courseId, nextStaffId) => {
                setStaffOverrideByDay((prev) => {
                  const next = new Map(prev);
                  const dayMap = new Map(next.get(weekday) ?? []);
                  if (nextStaffId === '') {
                    dayMap.delete(courseId);
                  } else {
                    dayMap.set(courseId, nextStaffId);
                  }
                  next.set(weekday, dayMap);
                  return next;
                });
              },
            });
            return (
              <CourseRow
                key={code}
                code={code}
                patients={patients}
                capacity={code === 'M' ? undefined : COURSE_CAPACITY}
                disabled={!canEdit}
                assignedStaffSlot={slot}
              />
            );
          })}
        </div>

        {/* DragOverlay: 横移動中のカード見た目を全画面に固定. */}
        <DragOverlay>
          {activePatientId ? (
            <div className="rounded border border-brand-primary bg-brand-primary/10 px-2 py-1 text-xs shadow-lg">
              {patientMeta.get(activePatientId)?.name ?? activePatientId}
            </div>
          ) : null}
        </DragOverlay>
      </Card>
    </DndContext>
  );
}
