'use client';

/**
 * ScheduleGridV2 — Layer 1 時間配置グリッド (W3-FE5).
 *
 * 設計仕様書 ``docs/plans/v2-allocation-redesign.md`` v0.9 §3.6.2 に対応する
 * 「患者を時刻スロットへ」配置するメイン UI。
 *
 * レイアウト:
 *   ┌──────────────────────────────────────────────┐
 *   │ ヘッダー: 週切替 + コース行プレビュー + 固定ボタン │
 *   ├──────────────────────────────────────────────┤
 *   │      月  火  水  木  金  土  日                │
 *   │ 8:00 [ ][ ][ ][ ][ ][ ][ ]                     │ ← 15 分セル
 *   │ 8:15 [ ][ ][ ][ ][ ][ ][ ]                     │
 *   │ ...                                            │
 *   │ 19:45[ ][ ][ ][ ][ ][ ][ ]                     │
 *   ├──────────────────────────────────────────────┤
 *   │ 保留プール: [患者A][患者B]…                    │
 *   └──────────────────────────────────────────────┘
 *
 * 主機能:
 *   - dnd-kit による D&D (プール ↔ セル, セル ↔ セル, セル → プール)
 *   - 配置済みカードの +1 トグル (staff_count 1 ↔ 2)
 *   - 「固定」ボタンで POST /api/v1/schedule/fix
 *
 * 状態管理:
 *   - placements: ローカル state ( Map<patientId, Placement> )
 *   - 初期値は患者マスタの weekly_pattern.entries から復元
 *   - 「固定」成功後に再 hydrate される (useFixSchedule の onSuccess で
 *     patients クエリが invalidate されるため、refetch 完了後に effect で再構築)
 *
 * 限界 (Wave 4 で解消予定):
 *   - コース行 (M / A / B / C / D) は仮表示のラベルのみ。患者紐付けは未実装
 *   - 1 セルに複数患者が積まれた場合の衝突解決 UI なし (見えるが推奨されない)
 *   - 同行スタッフ数を 3 以上にする UI は無し (BE schema が 1..2 のみ許容)
 */
import { useEffect, useMemo, useState } from 'react';
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { format } from 'date-fns';
import { ja } from 'date-fns/locale';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { usePatients } from '@/lib/queries/patients';
import type { ScheduleFixRequest, FixedVisitV2 } from '@/lib/queries/schedule_v2';
import { useVisits } from '@/lib/queries/visits';
import { addDays, toWeekStart, WeekSelector } from '@/components/schedule/WeekSelector';

import { FixButton } from './FixButton';
import { PatientCard } from './PatientCard';
import { PoolPanel, POOL_DROPPABLE_ID } from './PoolPanel';
import { TimeSlotCell, rejectDuplicateDrop, type VisitEntry } from './TimeSlotCell';
import { ScheduleChangeDialog } from './ScheduleChangeDialog';

// ─────────────────────────────────────────────────────────────────────────
// Time / weekday utilities
// ─────────────────────────────────────────────────────────────────────────

/** 0=Mon..6=Sun と日本語ラベルの対応. */
const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** WeeklyPatternEntryV2.weekday 文字列 → 0..6. */
const WEEKDAY_CODE_TO_INT: Record<string, number> = {
  Mon: 0,
  Tue: 1,
  Wed: 2,
  Thu: 3,
  Fri: 4,
  Sat: 5,
  Sun: 6,
};

/** Layer 1 で扱う表示時間帯 (8:00 〜 20:00 の 15 分刻み, 末尾は 19:45). */
const SLOT_START_HOUR = 8;
const SLOT_END_HOUR = 20; // exclusive (= 19:45 が最終スロット)
const SLOT_MINUTES = 15;

/** "HH:MM" の配列を生成. */
function buildSlots(): string[] {
  const out: string[] = [];
  for (let h = SLOT_START_HOUR; h < SLOT_END_HOUR; h += 1) {
    for (let m = 0; m < 60; m += SLOT_MINUTES) {
      out.push(`${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`);
    }
  }
  return out;
}

const TIME_SLOTS = buildSlots();

/** "HH:MM" を 0:00 起点の分にする. */
function hhmmToMin(s: string): number {
  const parts = s.split(':');
  const h = parseInt(parts[0] ?? '0', 10);
  const m = parseInt(parts[1] ?? '0', 10);
  return h * 60 + m;
}

/** 分を "HH:MM" にする. clamp はしない (呼び出し側で範囲ガード). */
function minToHHMM(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** start_time から service_minutes 後の end_time を導出. デフォルト 60 分. */
function deriveEndTime(start: string, serviceMinutes: number): string {
  const total = hhmmToMin(start) + Math.max(15, serviceMinutes);
  // 23:59 を超えないようクランプ (実用上 20:00 を超えない想定だが防御的)
  const clamped = Math.min(total, 23 * 60 + 59);
  return minToHHMM(clamped);
}

/** Date → ISO year/week を JS だけで計算 (date-fns に依存しないシンプル実装). */
function toIsoYearWeek(d: Date): { iso_year: number; iso_week: number } {
  // ISO 8601: 木曜日が属する年 = ISO year. その年最初の木曜の属する週 = week 1.
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = date.getUTCDay() || 7; // Sun=0 → 7
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const year = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(year, 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return { iso_year: year, iso_week: week };
}

// ─────────────────────────────────────────────────────────────────────────
// Local state shape
// ─────────────────────────────────────────────────────────────────────────

/**
 * 1 患者の現在の配置状態 (ローカル state).
 *
 * 簡易のため「1 患者 = 1 配置」とする。複数曜日に同じ患者を置きたい場合は
 * 後続イテレーションで `Placement[]` に拡張予定。
 */
interface Placement {
  patientId: string;
  weekday: number;
  /** "HH:MM" */
  startTime: string;
  /** "HH:MM" */
  endTime: string;
  staffCount: 1 | 2;
}

interface PoolEntry {
  patientId: string;
  /** 念のため weekly_pattern.service_minutes を覚えておく (再配置時に使う). */
  serviceMinutes: number;
}

/** patient_id ベースで Placement を引ける Map. */
type PlacementMap = Map<string, Placement>;

/** weekly_pattern.entries[] → Placement[] への hydrate. */
function hydratePlacementsFromPatients(
  patients: Array<{
    id: string;
    weekly_pattern?: Record<string, unknown> | null | undefined;
  }>,
): {
  placements: PlacementMap;
  pool: PoolEntry[];
} {
  const placements = new Map<string, Placement>();
  const pool: PoolEntry[] = [];

  for (const p of patients) {
    const wp = p.weekly_pattern;
    const entries = (wp?.entries as Array<Record<string, unknown>> | undefined) ?? [];
    const wpServiceMinutes = Number(wp?.service_minutes ?? 0);

    // 「固定」配置として認識できるエントリ (weekday + preferred_start が揃う) を 1 つだけ採用。
    let placed = false;
    for (const entry of entries) {
      const wdRaw = entry.weekday;
      const start = entry.preferred_start;
      if (typeof start !== 'string') continue;

      let weekday: number | null = null;
      if (typeof wdRaw === 'number' && wdRaw >= 0 && wdRaw <= 6) {
        weekday = wdRaw;
      } else if (typeof wdRaw === 'string') {
        weekday = WEEKDAY_CODE_TO_INT[wdRaw] ?? null;
      }
      if (weekday === null) continue;

      const sm = Number(entry.service_minutes ?? wpServiceMinutes ?? 60);
      const end =
        typeof entry.preferred_end === 'string' && entry.preferred_end.length > 0
          ? (entry.preferred_end as string)
          : deriveEndTime(start, sm > 0 ? sm : 60);

      const sc = Number(entry.staff_count ?? 1);
      placements.set(p.id, {
        patientId: p.id,
        weekday,
        startTime: start,
        endTime: end,
        staffCount: sc === 2 ? 2 : 1,
      });
      placed = true;
      break;
    }

    if (!placed) {
      pool.push({ patientId: p.id, serviceMinutes: wpServiceMinutes > 0 ? wpServiceMinutes : 60 });
    }
  }

  return { placements, pool };
}

// ─────────────────────────────────────────────────────────────────────────
// Drag id helpers (cell vs pool, draggable vs droppable)
// ─────────────────────────────────────────────────────────────────────────

const CELL_ID_PREFIX = 'cell:';

function cellDroppableId(weekday: number, time: string): string {
  return `${CELL_ID_PREFIX}${weekday}:${time}`;
}

/** "cell:3:09:30" → { weekday: 3, time: "09:30" } | null */
function parseCellId(id: string): { weekday: number; time: string } | null {
  if (!id.startsWith(CELL_ID_PREFIX)) return null;
  const rest = id.slice(CELL_ID_PREFIX.length);
  // weekday は 1 文字, あとは "HH:MM"
  const colonAt = rest.indexOf(':');
  if (colonAt < 0) return null;
  const weekday = parseInt(rest.slice(0, colonAt), 10);
  const time = rest.slice(colonAt + 1);
  if (Number.isNaN(weekday) || weekday < 0 || weekday > 6) return null;
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return null;
  return { weekday, time };
}

/**
 * 患者 1 人につき draggable id は固定 (= `patient:<id>`).
 * dnd-kit は同一画面に同じ id の draggable が複数あると warn を出すので、
 * 1 患者 1 配置の前提を活かして id を共通化する。
 */
function patientDraggableId(patientId: string): string {
  return `patient:${patientId}`;
}

function parsePatientDraggableId(id: string): string | null {
  if (!id.startsWith('patient:')) return null;
  return id.slice('patient:'.length);
}

// ─────────────────────────────────────────────────────────────────────────
// Course row (仮表示) — Wave 4 で実データ連動
// ─────────────────────────────────────────────────────────────────────────

const COURSE_ROWS: Array<{ key: string; label: string; tone: string }> = [
  { key: 'M', label: 'M (マネージャー)', tone: 'bg-warning/10 text-warning' },
  { key: 'A', label: 'A コース', tone: 'bg-brand-primary/10 text-brand-primary' },
  { key: 'B', label: 'B コース', tone: 'bg-success/10 text-success' },
  { key: 'C', label: 'C コース', tone: 'bg-info/10 text-info' },
  { key: 'D', label: 'D コース', tone: 'bg-bg-muted text-text-secondary' },
];

// ─────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────

interface ScheduleGridV2Props {
  /** RBAC: admin/manager 以外は drag / 固定を無効化. */
  canEdit: boolean;
  /** W3-FE7 で `<PendingRequestPanel />` をマウントするための制御フラグ.
   *  デフォルト false (= 何も表示しない). */
  showPendingPanel?: boolean;
  /** W3-FE7 が組み立てた申請パネル要素を render slot として受け取る.
   *  本ファイルは PendingRequestPanel コンポーネント自体を import しない
   *  (ファイル所有違反を避ける) ため、props 経由で受け取る形にする. */
  pendingPanelSlot?: React.ReactNode;
  /**
   * W8-FE2: controlled モード用 props.
   * 指定しない場合は uncontrolled (= 従来動作) にフォールバックする.
   * 親 (/schedule/page.tsx) から渡すことで、全タブが同一の週 state を共有できる.
   */
  weekStart?: Date;
  onWeekChange?: (next: Date) => void;
}

export function ScheduleGridV2({
  canEdit,
  showPendingPanel = false,
  pendingPanelSlot,
  weekStart: weekStartProp,
  onWeekChange,
}: ScheduleGridV2Props) {
  // W8-FE2: controlled / uncontrolled 切替パターン.
  // weekStart prop が渡された場合は controlled、渡されない場合は uncontrolled (従来動作).
  const [internalWeekStart, setInternalWeekStart] = useState<Date>(() => toWeekStart(new Date()));
  const weekStart = weekStartProp ?? internalWeekStart;
  const setWeekStart = (d: Date) => {
    if (onWeekChange) {
      onWeekChange(d);
    } else {
      setInternalWeekStart(d);
    }
  };

  // 患者マスタを取得 (limit 500). Layer 1 の対象は active 患者だけだが、
  // UI 側で簡易フィルタ。
  const patientsQuery = usePatients({ limit: 500 });
  const allPatients = useMemo(() => patientsQuery.data?.items ?? [], [patientsQuery.data]);

  // 患者マスタから配置 / プール初期状態を復元する.
  // patientsQuery.data が確定したタイミングで 1 度だけ実行する.
  // (dataUpdatedAt をキーにすることで「固定」成功後の refetch にも追従)
  const [placements, setPlacements] = useState<PlacementMap>(() => new Map());
  const [pool, setPool] = useState<PoolEntry[]>([]);
  const [hydratedAt, setHydratedAt] = useState<number>(0);

  useEffect(() => {
    if (!patientsQuery.dataUpdatedAt || patientsQuery.dataUpdatedAt === hydratedAt) {
      return;
    }
    const { placements: p, pool: q } = hydratePlacementsFromPatients(allPatients);
    setPlacements(p);
    setPool(q);
    setHydratedAt(patientsQuery.dataUpdatedAt);
  }, [patientsQuery.dataUpdatedAt, allPatients, hydratedAt]);

  // 患者 id → 表示用メタ
  const patientMeta = useMemo(() => {
    const map = new Map<string, { name: string; caption?: string }>();
    for (const p of allPatients) {
      map.set(p.id, {
        name: p.name,
        caption: p.kana ?? undefined,
      });
    }
    return map;
  }, [allPatients]);

  // ─────────────── 当該週の visit 一覧 (visit_id 解決用, M1) ───────────────
  // D&D 後に ScheduleChangeDialog へ渡す visit_id を取得するため、
  // 現在表示週の visits を fetch する。
  const weekStartStr = format(weekStart, 'yyyy-MM-dd');
  const weekEndStr = format(addDays(weekStart, 6), 'yyyy-MM-dd');
  const weekVisitsQuery = useVisits({ week_start: weekStartStr, week_end: weekEndStr });

  /** patient_id → visit.id (その週の最初の visit を使う). */
  const patientVisitIdMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const v of weekVisitsQuery.data?.items ?? []) {
      if (!map.has(v.patient_id)) {
        map.set(v.patient_id, v.id);
      }
    }
    return map;
  }, [weekVisitsQuery.data]);

  // ─────────────── D&D 後の変更反映ダイアログ state ───────────────
  /**
   * D&D でセル間移動が確定したときに開くダイアログ用の pending 情報。
   * プール → セル / セル → セル (異なるスロット) の場合にのみ使用する。
   */
  interface PendingMove {
    /** 当該週の Visit.id (UUID). BE の fix-or-pattern API が期待する visit_id. */
    visitId: string;
    patientId: string;
    weekday: number;
    startTime: string;
    serviceMinutes: number;
    staffCount: 1 | 2;
  }

  const [pendingMove, setPendingMove] = useState<PendingMove | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  /** ダイアログ閉 / キャンセル時に楽観的更新を revert する。 */
  const handleDialogClose = () => {
    setDialogOpen(false);
    if (pendingMove) {
      // 元の配置に戻す (before snapshot を使う)
      setPlacements((prev) => {
        const next = new Map(prev);
        next.delete(pendingMove.patientId);
        return next;
      });
      setPool((prev) =>
        prev.some((p) => p.patientId === pendingMove.patientId)
          ? prev
          : [
              ...prev,
              { patientId: pendingMove.patientId, serviceMinutes: pendingMove.serviceMinutes },
            ],
      );
      setPendingMove(null);
    }
  };

  /** ダイアログで「適用」が押された後の後処理 (楽観的更新確定)。 */
  const handleDialogApplied = () => {
    // 楽観的更新はすでに setPlacements 済みなので追加処理不要。
    setPendingMove(null);
    setDialogOpen(false);
  };

  // ─────────────── DnD handlers ───────────────
  const sensors = useSensors(
    useSensor(PointerSensor, {
      // 8px 以上動かしてからドラッグ開始 → クリック (= +1 ボタン) と区別.
      activationConstraint: { distance: 6 },
    }),
  );

  const [activePatientId, setActivePatientId] = useState<string | null>(null);

  const handleDragStart = (e: DragStartEvent) => {
    const pid = parsePatientDraggableId(String(e.active.id));
    setActivePatientId(pid);
  };

  const handleDragEnd = (e: DragEndEvent) => {
    setActivePatientId(null);
    const { active, over } = e;
    if (!over) return;
    const patientId = parsePatientDraggableId(String(active.id));
    if (!patientId) return;
    const overId = String(over.id);

    // Drop on pool → 配置解除 (ダイアログ不要、即時 revert)
    if (overId === POOL_DROPPABLE_ID) {
      setPlacements((prev) => {
        if (!prev.has(patientId)) return prev;
        const next = new Map(prev);
        next.delete(patientId);
        return next;
      });
      setPool((prev) => {
        if (prev.some((p) => p.patientId === patientId)) return prev;
        const sm = patientMeta.has(patientId) ? 60 : 60;
        return [...prev, { patientId, serviceMinutes: sm }];
      });
      return;
    }

    // Drop on cell → 配置 / 移動
    const cell = parseCellId(overId);
    if (!cell) return;

    // 同一患者が同一スロットに既に存在する場合は drop 拒否
    const targetKey = `${cell.weekday}:${cell.time}`;
    const occupantsAtTarget = cellOccupants.get(targetKey) ?? [];
    // 自分自身 (= 現在の配置先) への移動は拒否しない (移動なし = 同位置 drop)
    const currentPlacement = placements.get(patientId);
    const isSameSlot =
      currentPlacement !== undefined &&
      currentPlacement.weekday === cell.weekday &&
      currentPlacement.startTime === cell.time;
    if (isSameSlot) return; // 同スロットへの drop は何もしない

    // 既に別の配置として同 patientId が同スロットにある場合を拒否
    const otherOccupants = occupantsAtTarget.filter((id) => id !== patientId);
    const dummyEntries: VisitEntry[] = otherOccupants.map((id) => ({
      patientId: id,
      draggableId: `patient:${id}`,
      patient: { id, name: id },
      staffCount: 1,
    }));
    if (rejectDuplicateDrop(patientId, dummyEntries)) return;

    // 楽観的更新: ダイアログが表示されるまで先に UI に反映する
    const existingBefore = currentPlacement;
    const serviceMinutes = (() => {
      if (existingBefore) {
        return Math.max(
          15,
          hhmmToMin(existingBefore.endTime) - hhmmToMin(existingBefore.startTime),
        );
      }
      const inPool = pool.find((p) => p.patientId === patientId);
      return inPool?.serviceMinutes ?? 60;
    })();
    const staffCount: 1 | 2 = existingBefore?.staffCount ?? 1;

    setPlacements((prev) => {
      const next = new Map(prev);
      next.set(patientId, {
        patientId,
        weekday: cell.weekday,
        startTime: cell.time,
        endTime: deriveEndTime(cell.time, serviceMinutes),
        staffCount,
      });
      return next;
    });
    // プールに居た場合は除外
    setPool((prev) => prev.filter((p) => p.patientId !== patientId));

    // ダイアログを開く。当該週の visit_id を patientVisitIdMap から解決する (M1)。
    // 当該週にまだ visit が存在しない場合 (新規配置直後など) は空文字列を渡し、
    // ScheduleChangeDialog 側で disabled になる。
    const visitId = patientVisitIdMap.get(patientId) ?? '';
    setPendingMove({
      visitId,
      patientId,
      weekday: cell.weekday,
      startTime: cell.time,
      serviceMinutes,
      staffCount,
    });
    setDialogOpen(true);
  };

  // ─────────────── 操作ハンドラ ───────────────
  const toggleStaffCount = (patientId: string) => {
    setPlacements((prev) => {
      const cur = prev.get(patientId);
      if (!cur) return prev;
      const next = new Map(prev);
      next.set(patientId, { ...cur, staffCount: cur.staffCount === 2 ? 1 : 2 });
      return next;
    });
  };

  const unassign = (patientId: string) => {
    setPlacements((prev) => {
      if (!prev.has(patientId)) return prev;
      const next = new Map(prev);
      next.delete(patientId);
      return next;
    });
    setPool((prev) =>
      prev.some((p) => p.patientId === patientId)
        ? prev
        : [...prev, { patientId, serviceMinutes: 60 }],
    );
  };

  // ─────────────── 固定ボタン用 payload 構築 ───────────────
  const buildFixRequest = (): ScheduleFixRequest => {
    const { iso_year, iso_week } = toIsoYearWeek(weekStart);
    const layout: FixedVisitV2[] = [];
    for (const pl of placements.values()) {
      layout.push({
        patient_id: pl.patientId,
        weekday: pl.weekday,
        start_time: pl.startTime,
        end_time: pl.endTime,
        staff_count: pl.staffCount,
      });
    }
    return { iso_year, iso_week, layout };
  };

  const handleFixed = () => {
    // patients クエリの refetch 完了後に hydrate effect が走る.
    // ここでは追加のスナップショットリセットは不要.
  };

  // ─────────────── 描画用に 配置 を セル単位 に集約 ───────────────
  /** key = "weekday:HH:MM" → patient_ids[] */
  const cellOccupants = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const pl of placements.values()) {
      const key = `${pl.weekday}:${pl.startTime}`;
      const arr = map.get(key) ?? [];
      arr.push(pl.patientId);
      map.set(key, arr);
    }
    return map;
  }, [placements]);

  // ─────────────── L1: isSpecialWeek を patient.special_week_active から判定 ───────────────
  const isSpecialWeek = useMemo(() => {
    if (!pendingMove) return false;
    const patient = allPatients.find((p) => p.id === pendingMove.patientId);
    const activeList = patient?.special_week_active as
      | Array<{ iso_year: number; iso_week: number }>
      | undefined;
    if (!activeList || activeList.length === 0) return false;
    const { iso_year, iso_week } = toIsoYearWeek(weekStart);
    return activeList.some((w) => w.iso_year === iso_year && w.iso_week === iso_week);
  }, [pendingMove, allPatients, weekStart]);

  // ─────────────── render ───────────────
  if (patientsQuery.isLoading && allPatients.length === 0) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  const isoWeek = toIsoYearWeek(weekStart);

  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <section className="space-y-3">
        {/* ヘッダー */}
        <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
          <div className="flex flex-wrap items-center gap-3">
            <WeekSelector weekStart={weekStart} onChange={setWeekStart} />
            <span className="tnum text-xs text-text-muted">
              ISO {isoWeek.iso_year}-W{String(isoWeek.iso_week).padStart(2, '0')}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="tnum text-xs text-text-muted">
              配置 {placements.size} 名 / 保留 {pool.length} 名
            </span>
            <FixButton
              buildRequest={buildFixRequest}
              pendingCount={pool.length}
              onFixed={handleFixed}
              disabled={!canEdit || placements.size === 0}
            />
          </div>
        </Card>

        {/* W3-FE7 がここに <PendingRequestPanel /> を統合する想定の slot.
            親 (page.tsx) から `pendingPanelSlot` 経由で要素を受け取る形。
            ファイル所有: ScheduleGridV2.tsx は W3-FE5 所有なので、W3-FE7 の
            ファイルを直接 import せず、props 経由で構成を渡す。 */}
        {showPendingPanel && pendingPanelSlot ? <div>{pendingPanelSlot}</div> : null}

        {/* コース行 (仮表示, Wave 4 で実データ連動) */}
        <Card className="overflow-hidden p-0">
          <div className="grid grid-cols-[80px_repeat(7,minmax(0,1fr))] border-b border-border-default">
            <div className="border-r border-border-default bg-bg-muted px-2 py-1 text-[10px] font-semibold text-text-muted">
              コース
            </div>
            {WEEKDAY_LABELS.map((lbl, i) => (
              <div
                key={lbl}
                className="border-r border-border-default bg-bg-muted px-2 py-1 text-center text-xs font-semibold text-text-secondary"
              >
                {lbl}{' '}
                <span className="tnum text-text-muted">
                  {format(addDays(weekStart, i), 'M/d', { locale: ja })}
                </span>
              </div>
            ))}
          </div>
          {COURSE_ROWS.map((row) => (
            <div
              key={row.key}
              className="grid grid-cols-[80px_repeat(7,minmax(0,1fr))] border-b border-border-default/50"
            >
              <div
                className={`border-r border-border-default px-2 py-1 text-[10px] font-semibold ${row.tone}`}
              >
                {row.label}
              </div>
              {WEEKDAY_LABELS.map((_, i) => (
                <div
                  key={i}
                  className="border-r border-border-default px-1 py-1 text-[10px] text-text-muted"
                >
                  {/* Wave 4: コース構成 (Course / VisitStaffAssignment) を描画 */}
                  --
                </div>
              ))}
            </div>
          ))}
        </Card>

        {/* 時間軸グリッド */}
        <Card className="overflow-x-auto p-0">
          <div className="grid grid-cols-[60px_repeat(7,minmax(0,1fr))]">
            {/* ヘッダー行 (時刻列ラベル + 曜日ラベル) */}
            <div className="border-b border-border-default bg-bg-muted px-2 py-1 text-[10px] font-semibold text-text-muted">
              時刻
            </div>
            {WEEKDAY_LABELS.map((lbl) => (
              <div
                key={lbl}
                className="border-b border-r border-border-default bg-bg-muted px-2 py-1 text-center text-xs font-semibold text-text-secondary"
              >
                {lbl}
              </div>
            ))}

            {/* 時間スロット行 */}
            {TIME_SLOTS.map((time) => {
              const isHourBoundary = time.endsWith(':00');
              return (
                <FragmentRow
                  key={time}
                  time={time}
                  isHourBoundary={isHourBoundary}
                  cellOccupants={cellOccupants}
                  placements={placements}
                  patientMeta={patientMeta}
                  canEdit={canEdit}
                  onToggleStaffCount={toggleStaffCount}
                  onUnassign={unassign}
                />
              );
            })}
          </div>
        </Card>

        {/* プール */}
        <PoolPanel count={pool.length} disabled={!canEdit}>
          {pool.map((entry) => {
            const meta = patientMeta.get(entry.patientId);
            return (
              <PatientCard
                key={entry.patientId}
                draggableId={patientDraggableId(entry.patientId)}
                patient={{
                  id: entry.patientId,
                  name: meta?.name ?? entry.patientId,
                  caption: meta?.caption,
                }}
                disabled={!canEdit}
              />
            );
          })}
        </PoolPanel>

        {/* DragOverlay: ドラッグ中のカード見た目を全画面に固定. */}
        <DragOverlay>
          {activePatientId ? (
            <div className="rounded border border-brand-primary bg-brand-primary/10 px-2 py-1 text-xs shadow-lg">
              {patientMeta.get(activePatientId)?.name ?? activePatientId}
            </div>
          ) : null}
        </DragOverlay>
      </section>

      {/* D&D 後の変更反映モード選択ダイアログ */}
      {pendingMove ? (
        <ScheduleChangeDialog
          open={dialogOpen}
          visitId={pendingMove.visitId}
          patientName={patientMeta.get(pendingMove.patientId)?.name ?? pendingMove.patientId}
          isSpecialWeek={isSpecialWeek}
          newWeekday={pendingMove.weekday}
          newStartTime={pendingMove.startTime}
          newDurationMin={pendingMove.serviceMinutes}
          isoYear={isoWeek.iso_year}
          isoWeek={isoWeek.iso_week}
          onClose={handleDialogClose}
          onApplied={handleDialogApplied}
        />
      ) : null}
    </DndContext>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// 1 時刻スロット行 (= 7 セル分) を切り出した内部コンポーネント.
// memo はせず、placements が変わるたびに再描画する (Map 比較が浅いため).
// ─────────────────────────────────────────────────────────────────────────

interface FragmentRowProps {
  time: string;
  isHourBoundary: boolean;
  cellOccupants: Map<string, string[]>;
  placements: PlacementMap;
  patientMeta: Map<string, { name: string; caption?: string }>;
  canEdit: boolean;
  onToggleStaffCount: (patientId: string) => void;
  onUnassign: (patientId: string) => void;
}

function FragmentRow({
  time,
  isHourBoundary,
  cellOccupants,
  placements,
  patientMeta,
  canEdit,
  onToggleStaffCount,
  onUnassign,
}: FragmentRowProps) {
  return (
    <>
      <div
        className={`border-r border-border-default px-2 py-0.5 text-right text-[10px] tnum text-text-muted ${
          isHourBoundary
            ? 'border-t border-t-border-default font-semibold text-text-secondary'
            : 'border-t border-t-border-default/30'
        }`}
      >
        {isHourBoundary ? time : ''}
      </div>
      {[0, 1, 2, 3, 4, 5, 6].map((wd) => {
        const key = `${wd}:${time}`;
        const occupantIds = cellOccupants.get(key) ?? [];
        // VisitEntry[] を構築して TimeSlotCell に渡す
        const entries: VisitEntry[] = occupantIds.flatMap((pid) => {
          const placement = placements.get(pid);
          if (!placement) return [];
          const meta = patientMeta.get(pid);
          return [
            {
              patientId: pid,
              draggableId: patientDraggableId(pid),
              patient: {
                id: pid,
                name: meta?.name ?? pid,
                caption: meta?.caption,
              },
              staffCount: placement.staffCount,
              onToggleStaffCount: canEdit ? () => onToggleStaffCount(pid) : undefined,
              onUnassign: canEdit ? () => onUnassign(pid) : undefined,
            } satisfies VisitEntry,
          ];
        });
        return (
          <TimeSlotCell
            key={key}
            droppableId={cellDroppableId(wd, time)}
            weekday={wd}
            time={time}
            entries={entries}
            isHourBoundary={isHourBoundary}
            disabled={!canEdit}
          />
        );
      })}
    </>
  );
}
