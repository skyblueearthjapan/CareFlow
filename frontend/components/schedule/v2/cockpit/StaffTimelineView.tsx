'use client';

/**
 * StaffTimelineView — 「今週の運転席」曜日1日分の横タイムライン (Phase E / FE-B)。
 *
 * 設計 = `docs/plans/week-cockpit-design.md` §1 D5 / §3 / §4(FE-B 行)。
 * 動きの正 = `docs/mockups/staff-schedule-week-cockpit-mock.html` の
 * `renderTl` / `bindTlDrag` / `.bar` `.lane` の CSS。
 *
 * 盤面の読み方:
 *   - 横 = 時刻 (8:00〜19:00 / `TL_COCKPIT_START_MIN`〜`TL_COCKPIT_END_MIN`)
 *   - 縦 = スタッフ行 (先頭に「全員（固定）」帯・末尾に「（担当なし）」)
 *   - 色 = 訪問は患者性別 (`genderPalette`)・イベントは緑・固定はグレー
 *
 * 操作 (D5):
 *   - 横ドラッグ = 時刻変更 (15分スナップ・週のみ)
 *   - 縦ドラッグ (別のスタッフ行へドロップ) = 担当変更 (週のみ)。行の外で離したら担当は変えない。
 *   - 3px 以内の動き = クリック扱い → `onVisitClick` (親が VisitActionMenu を開く)
 *   - 青ピン (week_pinned) / 今週だけ取消 (status='cancelled') / 過去日 / 閲覧のみ
 *     はドラッグ不可。カーソル・title・aria-label で理由を示し `aria-disabled` を付ける。
 *   - 氏名列の ⠿ を別のスタッフ行の氏名セルへドロップ = **その日の 2 人の予定を丸ごと入れ替え**
 *     (PO 要望 2026-08-22)。バーの DnD (1 訪問) とは別物で、HTML5 DnD を使う。
 *     ここでは `onStaffSwap` を呼ぶだけ (確認ダイアログ・API は親 = FE-C の担当)。
 *
 * **キーボード操作はドラッグでは行わない**: バーは button なので Enter/Space で
 * `onVisitClick` が起き、時刻変更・担当変更・取消はすべて `VisitActionMenu`
 * (FE-A) のメニュー項目が担う。ここはポインタ操作の近道でしかない。
 *
 * この部品は API を呼ばない (結線は FE-C = CourseDayTablePanel)。
 * 曜日跨ぎの移動はリスト側 (盤面) の担当で、ここでは扱わない。
 */
import * as React from 'react';
import { addDays, format } from 'date-fns';

import type { WeekOverrideRead } from '@/lib/queries/staff-overrides';
import type { EventRead } from '@/lib/schemas/staff-events';
import { parseHM } from '@/lib/scheduling/freeGaps';
import {
  assignLanes,
  genderPalette,
  TL_COCKPIT_END_MIN,
  TL_COCKPIT_START_MIN,
  type TimelineBlock,
} from '@/lib/scheduling/timeline';

import type { WeekOverviewVisit } from '../CourseWeekOverview';
import { UNASSIGNED_ROW_KEY } from '../courseDnd';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { SyncedHScroll } from '@/components/ui/synced-h-scroll';

// ─────────────────────────────────────────────────────────────────────────
// 型
// ─────────────────────────────────────────────────────────────────────────

/**
 * 「（担当なし）」行のキー。StaffWeekBoard と**同一定数**を使う
 * (FE-C 結線 2026-08-22: 実体は `courseDnd.ts` の単一ソースへ寄せた)。
 */
export { UNASSIGNED_ROW_KEY };

/** 盤面に載せる訪問。運転席では取消 (D1) とコース見出しを追加で使う。 */
export type TimelineVisit = WeekOverviewVisit & {
  /** `visits.status`。'cancelled' = 今週だけ取消 (D1) → 打消線・ドラッグ不可。 */
  status?: string | null;
  /** コース見出し (例: 稲毛A)。バーの小見出しに出す。省略可。 */
  course_label?: string | null;
};

/** 盤面に載せるイベント。`cancelled_at` は mig 0075 (D2) の「今週だけ外す」。 */
export type TimelineEvent = EventRead & {
  cancelled_at?: string | null;
};

/** スタッフ行 1 本。`staffId` は UUID か `UNASSIGNED_ROW_KEY`。 */
export interface StaffTimelineRow {
  staffId: string;
  name: string;
  /** 拠点名など、氏名の下に出す補足。 */
  office?: string | null;
  /** その日の休み/時間変更。あれば行を網掛けし、補足に理由を出す。 */
  off?: WeekOverrideRead | null;
  /**
   * 新人 (staff.is_trainee)。新人は担当になれない (同行で割り当てる) ため、
   * スタッフ入れ替えの掴み元にもドロップ先にもしない。
   */
  isTrainee?: boolean;
}

/** 「全員（固定）」帯 (朝会など・先頭レーン)。 */
export interface FixedAllDayBand {
  /** 'HH:MM'。 */
  start: string;
  /** 'HH:MM'。 */
  end: string;
  title: string;
}

/**
 * カイポケ突合のゴーストマーカー (C1 の `ReconcileMarker` を訪問へ拡張した形)。
 * before = 青点線 (今ここ) / after = 紫実線 (こう変わる) / delete = 青の打消し。
 */
export interface TimelineMarker {
  action: 'add' | 'update' | 'delete';
  /** 一意キー (突合の external_id)。 */
  externalId: string;
  title: string;
  /** after 側 'HH:MM'。 */
  start: string;
  end: string;
  /** before 側 'HH:MM' (update / delete)。 */
  beforeStart?: string | null;
  beforeEnd?: string | null;
  /** after 側の行。省略時は「（担当なし）」行。 */
  staffId?: string | null;
  /** before 側の行 (担当変更を伴う差分)。省略時は `staffId`。 */
  beforeStaffId?: string | null;
  /** 0=月..5=土。指定があり `day` と違えば描かない。 */
  weekday?: number | null;
  kind?: 'visit' | 'event';
  patientName?: string | null;
  courseLabel?: string | null;
}

/**
 * `onVisitMove` の引数 (親が `visit-move-week-only` / `visit-assign-staff-week` を呼ぶ)。
 * 変わった軸だけを載せる = 親は「必要な API だけ」を呼べる:
 *   - `toStart === null` → 時刻は変えていない (move API を呼ばない)
 *   - `toStaffId === undefined` → 担当は変えていない (assign API を呼ばない)
 */
export interface VisitMovePayload {
  /** 動かした訪問そのもの (親が患者名・コース等を再検索しなくて済む)。 */
  visit: TimelineVisit;
  /** 移動前の開始時刻 'HH:MM' (トースト/undo 文言用)。 */
  fromStart: string;
  /** 移動後の開始時刻 'HH:MM' (15分スナップ済み)。変化なしは null。 */
  toStart: string | null;
  /** 担当変更を伴うときだけ入る。null = （担当なし）へ。undefined = 担当変更なし。 */
  toStaffId?: string | null;
}

/**
 * スタッフ入れ替え DnD の MIME (PO 要望 2026-08-22)。
 * バーの DnD (pointer) と混ざらないよう、行見出しの DnD だけ HTML5 DnD + 専用 MIME で扱う。
 */
export const STAFF_SWAP_MIME = 'application/x-rakusuke-staff-swap';

/** `onStaffSwap` の引数。「その日の 2 人の予定を丸ごと入れ替える」。 */
export interface StaffSwapPayload {
  fromStaffId: string;
  toStaffId: string;
  /** 0=月..5=土。 */
  day: number;
}

export interface StaffTimelineViewProps {
  /** 対象週の月曜。 */
  weekStart: Date;
  /** 表示する曜日 (0=月..5=土)。 */
  day: number;
  onDayChange: (day: number) => void;
  staffRows: StaffTimelineRow[];
  /** その日の全訪問 (weekday が違うものは無視する)。 */
  visits: TimelineVisit[];
  /** staffId → その週のイベント (日付でこの日の分だけ拾う)。 */
  events?: Map<string, TimelineEvent[]>;
  fixedAllDay?: FixedAllDayBand[];
  markers?: TimelineMarker[];
  canEdit: boolean;
  /** 過去日 (JST 判定は親)。true ならドラッグ不可。 */
  isPast?: boolean;
  /**
   * `${templateId}:${weekday}` → assigned_staff_id。訪問の `primary_staff_id` が
   * 無いときの行帰属に使う (StaffWeekBoard の cellMap と同じ規則)。
   */
  assignedStaffByTemplateWeekday?: Map<string, string>;
  /**
   * 「（担当なし）」行を未割当ゼロでも常に出す (StaffWeekBoard と同じ思想・
   * 担当解除のドロップ先になる)。既定 false = 未割当があるときだけ出す。
   */
  alwaysShowUnassignedRow?: boolean;
  /**
   * ●未送信 (カイポケへまだ送っていない) の訪問/イベント id。
   * `null`/未指定 = 同期バーがまだ数えていない → ドットを出さない (断定しない)。
   */
  unsentIds?: Set<string> | null;
  onVisitClick: (visit: TimelineVisit, anchorEl: HTMLElement) => void;
  onVisitMove: (payload: VisitMovePayload) => void;
  onEventClick?: (ev: TimelineEvent, staffId: string) => void;
  /**
   * 氏名 ⠿ を別のスタッフ行へ落としたとき = その日の 2 人の予定を丸ごと入れ替え。
   * 未指定なら ⠿ を出さない (= 機能ごと無効)。API は呼ばず親へ委ねる。
   */
  onStaffSwap?: (payload: StaffSwapPayload) => void;
  /**
   * 行アクション (PO 要望 2026-08-22): リスト盤面のセルと同じ 🛌休みにする /
   * ＋訪問 / ＋イベント を氏名セルにも置く。渡されたものだけ描く。
   * `canEdit=false` と過去日では出さない (盤面と同じ扱い)。
   */
  onMarkOff?: (staffId: string, day: number, anchorEl: HTMLElement) => void;
  onAddVisit?: (staffId: string, day: number, anchorEl: HTMLElement) => void;
  onAddEvent?: (staffId: string, day: number) => void;
}

// ─────────────────────────────────────────────────────────────────────────
// 定数・純関数
// ─────────────────────────────────────────────────────────────────────────

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

const TOTAL_MIN = TL_COCKPIT_END_MIN - TL_COCKPIT_START_MIN;
const HOUR_COUNT = Math.round(TOTAL_MIN / 60);

/** 氏名列の幅 (px)。モックの 112px を日本語フル氏名向けに少し広げる。 */
const NAME_COL_PX = 160;
/** バー高さ / レーン送り (px)。 */
const BAR_H = 48;
const LANE_STEP = 54;
const LANE_PAD = 8;
/** 時間軸の最小幅 (px)。PO 要望 2026-08-22: 患者氏名が読める大きさ (1時間 ≒ 200px・PO 再要望で 1600→2400)。 */
const MIN_TRACK_PX = 2400;
/** 15分スナップ。 */
const SNAP_MIN = 15;
/** これ以下の移動はクリック扱い (モック bindTlDrag と同値)。 */
const CLICK_SLOP_PX = 3;
/** 終了時刻が無い訪問の既定の長さ (分)。 */
const DEFAULT_DURATION_MIN = 30;

/** 行アクション (🛌休みにする / ＋訪問 / ＋イベント) の見た目 = 盤面セルと同じ。 */
const ROW_ACTION_CLASS =
  'rounded border border-dashed border-border-default px-1 py-0.5 text-[10px] font-normal text-text-muted/80 transition-colors hover:border-brand-primary hover:text-brand-primary focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary';

/** ゴーストの色 (モックの --del / --add)。トークン化前なので実値で持つ。 */
const GHOST_BEFORE = '#2f7fd1';
const GHOST_BEFORE_BG = '#e8f1fb';
const GHOST_AFTER = '#7c5cd6';
const GHOST_AFTER_BG = '#efeafc';

function hhmm(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

function leftPct(min: number): number {
  return ((min - TL_COCKPIT_START_MIN) / TOTAL_MIN) * 100;
}

function widthPct(durationMin: number): number {
  return (durationMin / TOTAL_MIN) * 100;
}

/**
 * 訪問の行帰属。StaffWeekBoard の cellMap と**同じ規則**:
 * 訪問自身の primary を最優先 → コース担当 → 「（担当なし）」。
 * (臨時テンプレは複数スタッフの臨N コースを束ねるため primary が正)
 */
export function resolveVisitRowKey(
  visit: TimelineVisit,
  assignedStaffByTemplateWeekday?: Map<string, string>,
): string {
  return (
    visit.primary_staff_id ??
    assignedStaffByTemplateWeekday?.get(`${visit.course_template_id}:${visit.weekday}`) ??
    UNASSIGNED_ROW_KEY
  );
}

/**
 * 横ドラッグのスナップ (`snapYOffsetToMinutes` の横向き版)。
 * 縦タイムラインは px/分が固定だが、横は列幅可変なので実測幅から比率で解く。
 * 時間軸の外へは出さない (クランプ)。純関数。
 */
export function snapXOffsetToMinutes(
  offsetPx: number,
  trackWidthPx: number,
  baseStartMin: number,
  durationMin: number,
  snapMin: number = SNAP_MIN,
): number {
  const pxPerMin = trackWidthPx > 0 ? trackWidthPx / TOTAL_MIN : 0;
  const raw = pxPerMin > 0 ? baseStartMin + offsetPx / pxPerMin : baseStartMin;
  const snapped = Math.round(raw / snapMin) * snapMin;
  const maxStart = TL_COCKPIT_END_MIN - durationMin;
  return Math.max(TL_COCKPIT_START_MIN, Math.min(maxStart, snapped));
}

/** 休み/時間変更の一言 (行の補足)。 */
function offLabel(off: WeekOverrideRead): string {
  if (off.type === '時間変更') {
    return `時間変更 ${(off.start_time ?? '').slice(0, 5)}〜${(off.end_time ?? '').slice(0, 5)}`;
  }
  return off.type;
}

// ─────────────────────────────────────────────────────────────────────────
// 内部レイアウト型
// ─────────────────────────────────────────────────────────────────────────

type BarItem =
  | { kind: 'visit'; id: string; startMin: number; endMin: number; visit: TimelineVisit }
  | {
      kind: 'event';
      id: string;
      startMin: number;
      endMin: number;
      event: TimelineEvent;
      staffId: string;
    };

interface PlacedBar {
  item: BarItem;
  top: number;
}

interface PlacedGhost {
  marker: TimelineMarker;
  side: 'before' | 'after';
  startMin: number;
  endMin: number;
  top: number;
}

interface RowLayout {
  row: StaffTimelineRow;
  bars: PlacedBar[];
  ghosts: PlacedGhost[];
  heightPx: number;
}

interface DragState {
  visitId: string;
  visit: TimelineVisit;
  /** ドラッグ元のバー要素。別バーへ飛んだ pointermove/up を無視するための照合キー。 */
  barEl: HTMLElement;
  x0: number;
  y0: number;
  startMin0: number;
  durationMin: number;
  fromRowKey: string;
  trackWidthPx: number;
  moved: boolean;
  toStartMin: number;
  toRowKey: string;
}

// ─────────────────────────────────────────────────────────────────────────
// 本体
// ─────────────────────────────────────────────────────────────────────────

export function StaffTimelineView({
  weekStart,
  day,
  onDayChange,
  staffRows,
  visits,
  events,
  fixedAllDay,
  markers,
  canEdit,
  isPast = false,
  assignedStaffByTemplateWeekday,
  alwaysShowUnassignedRow = false,
  unsentIds,
  onVisitClick,
  onVisitMove,
  onEventClick,
  onStaffSwap,
  onMarkOff,
  onAddVisit,
  onAddEvent,
}: StaffTimelineViewProps) {
  const dayIso = React.useMemo(
    () => format(addDays(weekStart, day), 'yyyy-MM-dd'),
    [weekStart, day],
  );

  const [drag, setDrag] = React.useState<DragState | null>(null);
  const dragRef = React.useRef<DragState | null>(null);
  /**
   * ドラッグ成立時に続けて飛んでくる click を握り潰す対象のバー要素。
   * boolean ではなく要素で持つ = 別のバーのクリックまで巻き込まない。
   */
  const suppressClickRef = React.useRef<HTMLElement | null>(null);

  const tblRef = React.useRef<HTMLDivElement | null>(null);
  const ghostRefs = React.useRef(new Map<string, HTMLElement>());
  const [arrows, setArrows] = React.useState<Array<{ id: string; d: string }>>([]);

  // ── 行 (足りなければ「（担当なし）」を自動で足す = ズレは隠さない) ──────────
  const rowKeys = React.useMemo(() => new Set(staffRows.map((r) => r.staffId)), [staffRows]);

  const dayVisits = React.useMemo(() => visits.filter((v) => v.weekday === day), [visits, day]);

  const visibleMarkers = React.useMemo(
    () => (markers ?? []).filter((m) => m.weekday == null || m.weekday === day),
    [markers, day],
  );

  const rows: StaffTimelineRow[] = React.useMemo(() => {
    const list = [...staffRows];
    // 未知の担当は潰さず行を生やす (StaffWeekBoard の cellMap と同じ・ズレは隠さない)。
    const unknown = new Set<string>();
    let needUnassigned = false;
    const note = (key: string) => {
      if (key === UNASSIGNED_ROW_KEY) needUnassigned = true;
      else if (!rowKeys.has(key)) unknown.add(key);
    };
    for (const v of dayVisits) note(resolveVisitRowKey(v, assignedStaffByTemplateWeekday));
    for (const m of visibleMarkers) {
      note(m.staffId ?? UNASSIGNED_ROW_KEY);
      if (m.beforeStaffId) note(m.beforeStaffId);
    }
    for (const key of [...unknown].sort()) {
      list.push({ staffId: key, name: '（不明なスタッフ）', office: key.slice(0, 8) });
    }
    if (!rowKeys.has(UNASSIGNED_ROW_KEY) && (needUnassigned || alwaysShowUnassignedRow)) {
      list.push({ staffId: UNASSIGNED_ROW_KEY, name: '（担当なし）' });
    }
    return list;
  }, [
    staffRows,
    rowKeys,
    dayVisits,
    visibleMarkers,
    assignedStaffByTemplateWeekday,
    alwaysShowUnassignedRow,
  ]);

  const rowKeySet = React.useMemo(() => new Set(rows.map((r) => r.staffId)), [rows]);
  /** ドラッグ中 (レンダー外) から現在の行集合を引くための参照。 */
  const rowKeySetRef = React.useRef(rowKeySet);
  rowKeySetRef.current = rowKeySet;

  /**
   * 保険: 行が引けないキーは「（担当なし）」へ寄せる。上の rows で未知キーの行を
   * 生やすので通常は素通しになる。
   */
  const normalizeKey = React.useCallback(
    (key: string): string => (rowKeySet.has(key) ? key : UNASSIGNED_ROW_KEY),
    [rowKeySet],
  );

  // ── スタッフ入れ替え DnD (氏名列の ⠿) ─────────────────────────────────
  /**
   * 入れ替えの相手になれる行 = 親が渡した実在スタッフだけ。
   * 「（担当なし）」と、ズレを見せるために生やした「（不明なスタッフ）」行は不可。
   */
  const isRealStaffRow = React.useCallback(
    (staffId: string): boolean => staffId !== UNASSIGNED_ROW_KEY && rowKeys.has(staffId),
    [rowKeys],
  );

  /** staffId → 行 (新人判定に使う)。 */
  const rowById = React.useMemo(() => {
    const m = new Map<string, StaffTimelineRow>();
    for (const r of rows) m.set(r.staffId, r);
    return m;
  }, [rows]);

  /**
   * 入れ替えの掴み元/ドロップ先になれるか。
   * 「（担当なし）」「（不明なスタッフ）」に加え、**新人**も不可
   * (新人は担当になれない = 同行で割り当てる)。
   */
  const canSwapRow = React.useCallback(
    (staffId: string): boolean =>
      onStaffSwap != null && isRealStaffRow(staffId) && rowById.get(staffId)?.isTrainee !== true,
    [onStaffSwap, isRealStaffRow, rowById],
  );

  /** ⠿ を掴めない理由 (掴めるなら null)。title に出す。 */
  const swapBlockReason: string | null = !canEdit
    ? '閲覧のみのため入れ替えできません。'
    : isPast
      ? '過去日は入れ替えできません。'
      : null;

  /** ドラッグ中の掴み元 staffId (ドロップ先のハイライト判定に使う)。 */
  const [swapFrom, setSwapFrom] = React.useState<string | null>(null);
  /** dragover 中のドロップ先 staffId。 */
  const [swapOver, setSwapOver] = React.useState<string | null>(null);
  /** キーボード代替: ⠿ クリックで開く「入れ替え相手を選ぶ」メニューの行。 */
  const [swapMenuFor, setSwapMenuFor] = React.useState<string | null>(null);

  // 曜日を切り替えたら操作中の状態は捨てる (別の日の相手を選ばせない)。
  React.useEffect(() => {
    setSwapFrom(null);
    setSwapOver(null);
    setSwapMenuFor(null);
  }, [day]);

  const handleSwapDragStart = React.useCallback(
    (e: React.DragEvent<HTMLElement>, staffId: string) => {
      e.dataTransfer?.setData(STAFF_SWAP_MIME, JSON.stringify({ staffId, day }));
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
      setSwapMenuFor(null);
      setSwapFrom(staffId);
    },
    [day],
  );

  const handleSwapDragEnd = React.useCallback(() => {
    setSwapFrom(null);
    setSwapOver(null);
  }, []);

  /** dataTransfer から掴み元を読む。この曜日の・別の実在スタッフのときだけ返す。 */
  const readSwapSource = React.useCallback(
    (e: React.DragEvent<HTMLElement>, toStaffId: string): string | null => {
      let raw = '';
      try {
        raw = e.dataTransfer?.getData(STAFF_SWAP_MIME) ?? '';
      } catch {
        // 一部ブラウザは dragover 中に getData を許さない。掴み元の state で代替する。
        raw = '';
      }
      let fromStaffId: string | null = null;
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as { staffId?: unknown; day?: unknown };
          if (typeof parsed.staffId === 'string' && parsed.day === day)
            fromStaffId = parsed.staffId;
        } catch {
          fromStaffId = null;
        }
      } else {
        fromStaffId = swapFrom;
      }
      if (!fromStaffId || fromStaffId === toStaffId) return null;
      if (!canSwapRow(fromStaffId) || !canSwapRow(toStaffId)) return null;
      return fromStaffId;
    },
    [day, swapFrom, canSwapRow],
  );

  const handleSwapDragOver = React.useCallback(
    (e: React.DragEvent<HTMLElement>, toStaffId: string) => {
      if (swapBlockReason !== null) return;
      if (readSwapSource(e, toStaffId) === null) return;
      e.preventDefault(); // = ここに落とせる
      setSwapOver((prev) => (prev === toStaffId ? prev : toStaffId));
    },
    [swapBlockReason, readSwapSource],
  );

  const handleSwapDrop = React.useCallback(
    (e: React.DragEvent<HTMLElement>, toStaffId: string) => {
      // 掴み元の判定は state を捨てる**前**に行う (getData が使えない環境では
      // swapFrom がフォールバックの唯一の手掛かりになる)。
      const fromStaffId = swapBlockReason === null ? readSwapSource(e, toStaffId) : null;
      setSwapOver(null);
      setSwapFrom(null);
      if (fromStaffId === null) return;
      e.preventDefault();
      onStaffSwap?.({ fromStaffId, toStaffId, day });
    },
    [swapBlockReason, readSwapSource, onStaffSwap, day],
  );

  /** キーボード代替メニューから相手を選んだ。 */
  const handleSwapPick = React.useCallback(
    (fromStaffId: string, toStaffId: string) => {
      setSwapMenuFor(null);
      if (fromStaffId === toStaffId) return;
      onStaffSwap?.({ fromStaffId, toStaffId, day });
    },
    [onStaffSwap, day],
  );

  // ── 行ごとのバー配置 ────────────────────────────────────────────────
  const layouts: RowLayout[] = React.useMemo(() => {
    const byRow = new Map<string, BarItem[]>();
    for (const r of rows) byRow.set(r.staffId, []);

    for (const v of dayVisits) {
      const startMin = parseHM(v.start_time);
      if (startMin === null) continue;
      const endRaw = parseHM(v.end_time);
      const endMin =
        endRaw !== null && endRaw > startMin ? endRaw : startMin + DEFAULT_DURATION_MIN;
      const key = normalizeKey(resolveVisitRowKey(v, assignedStaffByTemplateWeekday));
      byRow.get(key)?.push({ kind: 'visit', id: v.id, startMin, endMin, visit: v });
    }

    if (events) {
      for (const r of rows) {
        if (r.staffId === UNASSIGNED_ROW_KEY) continue;
        for (const ev of events.get(r.staffId) ?? []) {
          if (ev.date !== dayIso) continue;
          const startMin = parseHM(ev.start_time);
          if (startMin === null) continue;
          const endRaw = parseHM(ev.end_time);
          const endMin = endRaw !== null && endRaw > startMin ? endRaw : startMin + 15;
          byRow
            .get(r.staffId)
            ?.push({ kind: 'event', id: ev.id, startMin, endMin, event: ev, staffId: r.staffId });
        }
      }
    }

    const ghostsByRow = new Map<string, PlacedGhost[]>();
    for (const m of visibleMarkers) {
      const afterKey = normalizeKey(m.staffId ?? UNASSIGNED_ROW_KEY);
      const beforeKey = normalizeKey(m.beforeStaffId ?? m.staffId ?? UNASSIGNED_ROW_KEY);
      const push = (key: string, g: Omit<PlacedGhost, 'top'>) => {
        const arr = ghostsByRow.get(key) ?? [];
        arr.push({ ...g, top: 0 });
        ghostsByRow.set(key, arr);
      };
      if (m.action === 'delete') {
        const s = parseHM(m.beforeStart ?? m.start);
        const e = parseHM(m.beforeEnd ?? m.end);
        if (s !== null) {
          push(beforeKey, {
            marker: m,
            side: 'before',
            startMin: s,
            endMin: e !== null && e > s ? e : s + DEFAULT_DURATION_MIN,
          });
        }
        continue;
      }
      if (m.action === 'update') {
        const bs = parseHM(m.beforeStart);
        const be = parseHM(m.beforeEnd);
        if (bs !== null) {
          push(beforeKey, {
            marker: m,
            side: 'before',
            startMin: bs,
            endMin: be !== null && be > bs ? be : bs + DEFAULT_DURATION_MIN,
          });
        }
      }
      const as = parseHM(m.start);
      const ae = parseHM(m.end);
      if (as !== null) {
        push(afterKey, {
          marker: m,
          side: 'after',
          startMin: as,
          endMin: ae !== null && ae > as ? ae : as + DEFAULT_DURATION_MIN,
        });
      }
    }

    return rows.map((row) => {
      const items = (byRow.get(row.staffId) ?? []).slice().sort((a, b) => a.startMin - b.startMin);
      const blocks: TimelineBlock[] = items.map((it) => ({
        id: it.id,
        startMin: it.startMin,
        endMin: it.endMin,
      }));
      const placement = assignLanes(blocks);
      let maxLane = 0;
      const bars: PlacedBar[] = items.map((item) => {
        const lane = placement.get(item.id)?.lane ?? 0;
        maxLane = Math.max(maxLane, lane + 1);
        return { item, top: LANE_PAD + lane * LANE_STEP };
      });
      const rawGhosts = ghostsByRow.get(row.staffId) ?? [];
      const ghosts: PlacedGhost[] = rawGhosts.map((g, i) => ({
        ...g,
        top: LANE_PAD + (maxLane + i) * LANE_STEP,
      }));
      const laneTotal = Math.max(1, maxLane + ghosts.length);
      return { row, bars, ghosts, heightPx: laneTotal * LANE_STEP + LANE_PAD * 2 - 4 };
    });
  }, [
    rows,
    dayVisits,
    events,
    dayIso,
    visibleMarkers,
    normalizeKey,
    assignedStaffByTemplateWeekday,
  ]);

  // ── ゴーストの矢印 (before → after) ───────────────────────────────────
  React.useEffect(() => {
    const host = tblRef.current;
    if (!host) {
      setArrows([]);
      return;
    }
    const hostRect = host.getBoundingClientRect();
    const next: Array<{ id: string; d: string }> = [];
    for (const m of visibleMarkers) {
      if (m.action !== 'update') continue;
      const b = ghostRefs.current.get(`${m.externalId}:before`);
      const a = ghostRefs.current.get(`${m.externalId}:after`);
      if (!b || !a) continue;
      const br = b.getBoundingClientRect();
      const ar = a.getBoundingClientRect();
      const x1 = br.left + br.width / 2 - hostRect.left;
      const y1 = br.top + br.height / 2 - hostRect.top;
      const x2 = ar.left + ar.width / 2 - hostRect.left;
      const y2 = ar.top + ar.height / 2 - hostRect.top;
      const mx = (x1 + x2) / 2;
      const my = Math.min(y1, y2) - 28;
      next.push({ id: m.externalId, d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}` });
    }
    setArrows(next);
  }, [visibleMarkers, layouts]);

  // ── ドラッグ ────────────────────────────────────────────────────────
  const commitDrag = React.useCallback(
    (d: DragState) => {
      const timeChanged = d.toStartMin !== d.startMin0;
      const staffChanged = d.toRowKey !== d.fromRowKey;
      if (!timeChanged && !staffChanged) return; // 変化なし → 何も呼ばない
      const payload: VisitMovePayload = {
        visit: d.visit,
        fromStart: hhmm(d.startMin0),
        toStart: timeChanged ? hhmm(d.toStartMin) : null,
      };
      if (staffChanged) {
        payload.toStaffId = d.toRowKey === UNASSIGNED_ROW_KEY ? null : d.toRowKey;
      }
      onVisitMove(payload);
    },
    [onVisitMove],
  );

  const handlePointerDown = React.useCallback(
    (e: React.PointerEvent<HTMLButtonElement>, item: BarItem, rowKey: string, movable: boolean) => {
      // 早期 return より前に必ず状態を捨てる。前回のドラッグ/押下の残骸が
      // 次の操作へ漏れると「別のバーが動く」「クリックが無視される」事故になる。
      suppressClickRef.current = null;
      if (dragRef.current) {
        dragRef.current = null;
        setDrag(null);
      }
      if (!movable || item.kind !== 'visit') return;
      // ドラッグ中の文字選択・画像ドラッグを抑止 (クリック自体は殺さない)。
      e.preventDefault();
      const track = e.currentTarget.parentElement;
      const width = track ? track.getBoundingClientRect().width : 0;
      dragRef.current = {
        visitId: item.id,
        visit: item.visit,
        barEl: e.currentTarget,
        x0: e.clientX,
        y0: e.clientY,
        startMin0: item.startMin,
        durationMin: item.endMin - item.startMin,
        fromRowKey: rowKey,
        trackWidthPx: width,
        moved: false,
        toStartMin: item.startMin,
        toRowKey: rowKey,
      };
      try {
        e.currentTarget.setPointerCapture?.(e.pointerId);
      } catch {
        // jsdom / 一部ブラウザは capture 非対応。イベントは bubbling で拾えるので無視。
      }
    },
    [],
  );

  const handlePointerMove = React.useCallback((e: React.PointerEvent<HTMLButtonElement>) => {
    const d = dragRef.current;
    if (!d || e.currentTarget !== d.barEl) return; // 別のバーへ飛んだ move は無視
    const dx = e.clientX - d.x0;
    const dy = e.clientY - d.y0;
    if (!d.moved && Math.abs(dx) <= CLICK_SLOP_PX && Math.abs(dy) <= CLICK_SLOP_PX) return;

    // 行の外 (氏名列・盤面外) で離したら担当は変えない → 毎回元の行から解き直す。
    let toRowKey = d.fromRowKey;
    try {
      const el = document.elementFromPoint?.(e.clientX, e.clientY) as HTMLElement | null;
      const lane = el?.closest?.('[data-lane-key]') as HTMLElement | null;
      const key = lane?.dataset.laneKey;
      if (key && rowKeySetRef.current.has(key)) toRowKey = key;
    } catch {
      // elementFromPoint 未実装環境 (jsdom) では行は変えない。
    }

    const next: DragState = {
      ...d,
      moved: true,
      toRowKey,
      toStartMin: snapXOffsetToMinutes(dx, d.trackWidthPx, d.startMin0, d.durationMin),
    };
    dragRef.current = next;
    setDrag(next);
  }, []);

  const handlePointerUp = React.useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      const d = dragRef.current;
      if (!d || e.currentTarget !== d.barEl) return; // 別のバーの up は無視
      dragRef.current = null;
      setDrag(null);
      try {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      } catch {
        // 同上。
      }
      if (!d.moved) return; // クリック扱い → onClick で拾う
      // 直後に飛んでくる click を「そのバーの 1 回だけ」握り潰す。要素と 1 回で
      // 縛るので、click が来ないまま次の操作に移っても抑止は持ち越されない
      // (pointerdown でも必ず捨てる)。
      suppressClickRef.current = d.barEl;
      commitDrag(d);
    },
    [commitDrag],
  );

  const handlePointerCancel = React.useCallback(() => {
    dragRef.current = null;
    setDrag(null);
  }, []);

  const handleBarClick = React.useCallback(
    (e: React.MouseEvent<HTMLButtonElement>, item: BarItem, rowKey: string) => {
      const suppressed = suppressClickRef.current;
      suppressClickRef.current = null; // 1 回で必ず解除
      if (suppressed === e.currentTarget) return;
      if (item.kind === 'visit') onVisitClick(item.visit, e.currentTarget);
      else onEventClick?.(item.event, rowKey);
    },
    [onVisitClick, onEventClick],
  );

  // ── 描画 ───────────────────────────────────────────────────────────
  /**
   * 行アクション (🛌休みにする / ＋訪問 / ＋イベント) を出すか。
   * 盤面と同じく、閲覧のみ・過去日では出さない。
   */
  const showRowActions =
    canEdit && !isPast && (onMarkOff != null || onAddVisit != null || onAddEvent != null);
  /** aria-label 用の日付 (例: 8/17)。 */
  const dayLabel = format(addDays(weekStart, day), 'M/d');

  const gridColumns = `${NAME_COL_PX}px 1fr`;
  const trackBg: React.CSSProperties = {
    backgroundImage: 'linear-gradient(90deg, var(--border-subtle) 1px, transparent 1px)',
    backgroundSize: `calc(100% / ${HOUR_COUNT}) 100%`,
  };

  return (
    <section
      className="overflow-hidden rounded-lg border"
      style={{ borderColor: 'var(--border-default)', background: 'var(--bg-base)' }}
      data-testid="staff-timeline-view"
      aria-label="職員タイムライン"
    >
      {/* 曜日切替 */}
      <div
        className="flex flex-wrap items-center gap-1 border-b px-2 py-1.5"
        style={{ borderColor: 'var(--border-default)', background: 'var(--bg-muted)' }}
      >
        {WEEKDAY_LABELS.map((label, i) => {
          const d = addDays(weekStart, i);
          const on = i === day;
          return (
            <button
              key={label}
              type="button"
              onClick={() => onDayChange(i)}
              aria-pressed={on}
              className="rounded px-2.5 py-0.5 text-xs font-bold"
              style={{
                background: on ? 'var(--brand-primary, #e15a7f)' : 'var(--bg-base)',
                color: on ? '#fff' : 'var(--text-secondary)',
                border: `1px solid ${on ? 'var(--brand-primary, #e15a7f)' : 'var(--border-default)'}`,
              }}
            >
              {label} <span className="tabular-nums">{format(d, 'M/d')}</span>
            </button>
          );
        })}
        <span className="ml-auto text-[11px]" style={{ color: 'var(--text-muted)' }}>
          バーを横にドラッグ=時刻(15分刻み) / 別の行へ=担当変更 / クリック=メニュー
          {onStaffSwap && canEdit && !isPast
            ? ' ／ 氏名の ⠿ を別の行へ=その日の予定を丸ごと入れ替え'
            : ''}
        </span>
      </div>

      <SyncedHScroll>
        <div className="relative" style={{ minWidth: MIN_TRACK_PX }} ref={tblRef}>
          {/* 時刻ヘッダ */}
          <div
            className="grid border-b"
            style={{
              gridTemplateColumns: `${NAME_COL_PX}px repeat(${HOUR_COUNT}, 1fr)`,
              borderColor: 'var(--border-default)',
            }}
          >
            <div
              className="sticky left-0 z-[5]"
              style={{ background: 'var(--bg-base)' }}
              aria-hidden="true"
            />
            {Array.from({ length: HOUR_COUNT }, (_, i) => (
              <div
                key={i}
                className="border-r px-1.5 py-1 text-xs tabular-nums"
                style={{ borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }}
              >
                {TL_COCKPIT_START_MIN / 60 + i}:00
              </div>
            ))}
          </div>

          {/* 全員（固定）帯 */}
          {fixedAllDay && fixedAllDay.length > 0 && (
            <div
              className="grid border-b"
              style={{
                gridTemplateColumns: gridColumns,
                borderColor: 'var(--border-default)',
                background: 'var(--sched-neutral-bg)',
              }}
              data-testid="tl-lane-fixed"
            >
              <div
                className="sticky left-0 z-[5] border-r px-2 py-1.5 text-sm font-bold"
                style={{
                  borderColor: 'var(--border-default)',
                  color: 'var(--text-secondary)',
                  background: 'var(--sched-neutral-bg)',
                }}
              >
                全員（固定）
              </div>
              <div className="relative" style={{ ...trackBg, minHeight: 40 }}>
                {fixedAllDay.map((f, i) => {
                  const s = parseHM(f.start);
                  const e = parseHM(f.end);
                  if (s === null) return null;
                  const end = e !== null && e > s ? e : s + DEFAULT_DURATION_MIN;
                  return (
                    <div
                      key={`${f.start}-${f.title}-${i}`}
                      className="absolute overflow-hidden truncate rounded px-2 py-1 text-[12px] leading-tight"
                      style={{
                        left: `${leftPct(s)}%`,
                        width: `${widthPct(end - s)}%`,
                        top: 4,
                        height: 24,
                        background: 'var(--sched-neutral-bg)',
                        borderLeft: '3px solid var(--sched-neutral-bar)',
                        color: 'var(--sched-neutral-ink)',
                      }}
                    >
                      🔒 {f.start.slice(0, 5)} {f.title}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* スタッフ行 */}
          {layouts.map(({ row, bars, ghosts, heightPx }) => {
            // 元の行は光らせない (担当が変わるときだけ移動先を示す)。
            const isDropTarget =
              drag?.moved === true &&
              drag.toRowKey === row.staffId &&
              drag.toRowKey !== drag.fromRowKey;
            const swappable = canSwapRow(row.staffId);
            const isSwapOver = swappable && swapOver === row.staffId && swapFrom !== row.staffId;
            return (
              <div
                key={row.staffId}
                className="grid border-b"
                style={{ gridTemplateColumns: gridColumns, borderColor: 'var(--border-default)' }}
                data-lane-key={row.staffId}
                data-testid={`tl-lane-${row.staffId}`}
              >
                <div
                  // 横スクロール時も氏名列を固定 (PO 指摘 2026-08-22)。
                  className="group/tl-name relative sticky left-0 z-[5] border-r px-2 py-1.5 text-sm font-bold"
                  style={{
                    borderColor: 'var(--border-default)',
                    color: 'var(--text-primary)',
                    background: isSwapOver ? 'var(--bg-muted)' : 'var(--bg-base)',
                    ...(isSwapOver
                      ? { outline: '2px solid var(--brand-primary, #e15a7f)', outlineOffset: -2 }
                      : null),
                  }}
                  data-testid={`tl-name-${row.staffId}`}
                  data-swap-over={isSwapOver ? 'true' : undefined}
                  onDragOver={swappable ? (e) => handleSwapDragOver(e, row.staffId) : undefined}
                  onDragLeave={
                    swappable
                      ? () => setSwapOver((prev) => (prev === row.staffId ? null : prev))
                      : undefined
                  }
                  onDrop={swappable ? (e) => handleSwapDrop(e, row.staffId) : undefined}
                >
                  <span className="flex items-start gap-1">
                    {swappable ? (
                      <StaffSwapGrip
                        row={row}
                        rows={rows}
                        canSwapRow={canSwapRow}
                        blockReason={swapBlockReason}
                        open={swapMenuFor === row.staffId}
                        onOpenChange={(o) => setSwapMenuFor(o ? row.staffId : null)}
                        onDragStart={(e) => handleSwapDragStart(e, row.staffId)}
                        onDragEnd={handleSwapDragEnd}
                        onPick={(toStaffId) => handleSwapPick(row.staffId, toStaffId)}
                      />
                    ) : null}
                    <span className="min-w-0 flex-1">
                      {row.name}
                      <small
                        className="block text-xs font-normal"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        {row.off ? offLabel(row.off) : (row.office ?? '')}
                      </small>
                    </span>
                  </span>
                  {showRowActions && isRealStaffRow(row.staffId) ? (
                    // 行アクションはアイコンのみ + hover/フォーカス時だけ表示。
                    // 絶対配置なので行高は伸びない (M2)。
                    <span
                      className="pointer-events-none absolute bottom-0.5 right-1 flex gap-0.5 opacity-0 transition-opacity focus-within:pointer-events-auto focus-within:opacity-100 group-hover/tl-name:pointer-events-auto group-hover/tl-name:opacity-100"
                      data-testid={`tl-row-actions-${row.staffId}`}
                    >
                      {onMarkOff ? (
                        <button
                          type="button"
                          onClick={(e) => onMarkOff(row.staffId, day, e.currentTarget)}
                          className={ROW_ACTION_CLASS}
                          title={`${row.name} をこの日休みにして、予定の渡し先を選びます`}
                          aria-label={`${row.name} ${dayLabel} を休みにする`}
                          data-testid={`tl-off-action-${row.staffId}`}
                        >
                          🛌
                        </button>
                      ) : null}
                      {onAddVisit ? (
                        <button
                          type="button"
                          onClick={(e) => onAddVisit(row.staffId, day, e.currentTarget)}
                          className={ROW_ACTION_CLASS}
                          title={`${row.name} ${dayLabel} に今週だけの訪問を追加（毎週の型は変わりません）`}
                          aria-label={`${row.name} ${dayLabel} に訪問を追加`}
                          data-testid={`tl-add-visit-${row.staffId}`}
                        >
                          ＋👤
                        </button>
                      ) : null}
                      {onAddEvent ? (
                        <button
                          type="button"
                          onClick={() => onAddEvent(row.staffId, day)}
                          className={ROW_ACTION_CLASS}
                          title={`${row.name} ${dayLabel} にイベントを追加`}
                          aria-label={`${row.name} ${dayLabel} にイベントを追加`}
                          data-testid={`tl-add-event-${row.staffId}`}
                        >
                          ＋📅
                        </button>
                      ) : null}
                    </span>
                  ) : null}
                </div>
                <div
                  className="relative"
                  style={{
                    ...trackBg,
                    minHeight: heightPx,
                    ...(row.off
                      ? {
                          backgroundImage:
                            'repeating-linear-gradient(135deg, transparent 0 6px, var(--sched-offduty-hatch) 6px 7px)',
                        }
                      : null),
                    ...(isDropTarget ? { backgroundColor: 'var(--info-bg)' } : null),
                  }}
                >
                  {bars.map(({ item, top }) => (
                    <Bar
                      key={item.id}
                      item={item}
                      top={top}
                      rowKey={row.staffId}
                      rowName={row.name}
                      canEdit={canEdit}
                      isPast={isPast}
                      drag={drag && drag.visitId === item.id ? drag : null}
                      unsent={unsentIds?.has(item.id) === true}
                      onPointerDown={handlePointerDown}
                      onPointerMove={handlePointerMove}
                      onPointerUp={handlePointerUp}
                      onPointerCancel={handlePointerCancel}
                      onClick={handleBarClick}
                    />
                  ))}
                  {ghosts.map((g) => (
                    <Ghost
                      key={`${g.marker.externalId}:${g.side}`}
                      ghost={g}
                      registerRef={(el) => {
                        const key = `${g.marker.externalId}:${g.side}`;
                        if (el) ghostRefs.current.set(key, el);
                        else ghostRefs.current.delete(key);
                      }}
                    />
                  ))}
                </div>
              </div>
            );
          })}

          {/* before → after の矢印 */}
          <svg
            className="pointer-events-none absolute inset-0 h-full w-full"
            aria-hidden="true"
            data-testid="tl-arrows"
          >
            <defs>
              <marker
                id="tl-arrowhead"
                markerWidth="8"
                markerHeight="8"
                refX="6"
                refY="4"
                orient="auto"
              >
                <path d="M0,0 L8,4 L0,8 z" fill={GHOST_AFTER} />
              </marker>
            </defs>
            {arrows.map((a) => (
              <path
                key={a.id}
                d={a.d}
                fill="none"
                stroke={GHOST_AFTER}
                strokeWidth="2.5"
                strokeDasharray="6 4"
                markerEnd="url(#tl-arrowhead)"
                opacity="0.9"
              />
            ))}
          </svg>
        </div>
      </SyncedHScroll>
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// スタッフ入れ替えの ⠿ グリップ (+ キーボード代替メニュー)
// ─────────────────────────────────────────────────────────────────────────

/**
 * 氏名列の ⠿。ドラッグ (HTML5 DnD) と、クリック/Enter で開く
 * 「入れ替え相手を選ぶ」メニューの両方を担う。
 *
 * - `<button draggable>` は Firefox でドラッグが始まらないため `span role="button"`。
 * - メニューは Radix Popover (ポータル + Escape + 外側クリック + フォーカス管理)。
 *   `@radix-ui/react-dropdown-menu` は本リポジトリ未導入のため、同等の
 *   dismiss/focus 挙動を持つ Popover を使う (項目は `role="menuitem"`)。
 */
function StaffSwapGrip({
  row,
  rows,
  canSwapRow,
  blockReason,
  open,
  onOpenChange,
  onDragStart,
  onDragEnd,
  onPick,
}: {
  row: StaffTimelineRow;
  rows: StaffTimelineRow[];
  canSwapRow: (staffId: string) => boolean;
  blockReason: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDragStart: (e: React.DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onPick: (toStaffId: string) => void;
}) {
  const enabled = blockReason === null;
  const candidates = rows.filter((r) => r.staffId !== row.staffId && canSwapRow(r.staffId));
  return (
    <Popover open={open} onOpenChange={(o) => onOpenChange(enabled && o)}>
      <PopoverTrigger asChild>
        <span
          role="button"
          tabIndex={0}
          data-testid={`tl-swap-grip-${row.staffId}`}
          draggable={enabled}
          aria-label={`${row.name}さんの予定を入れ替える(ドラッグ、または Enter で相手を選ぶ)`}
          aria-disabled={enabled ? undefined : true}
          title={
            blockReason ??
            'ドラッグして別のスタッフへ落とす=その日の予定を丸ごと入れ替え / クリック・Enter=相手を選ぶ'
          }
          onDragStart={enabled ? onDragStart : undefined}
          onDragEnd={enabled ? onDragEnd : undefined}
          // span role="button" はブラウザが Enter/Space を click に変換しないので自前で。
          onKeyDown={(e) => {
            if (!enabled) return;
            if (e.key !== 'Enter' && e.key !== ' ') return;
            e.preventDefault();
            onOpenChange(!open);
          }}
          className={[
            'mt-0.5 shrink-0 select-none rounded px-0.5 text-xs leading-none',
            'focus:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary',
            enabled ? 'cursor-grab' : 'cursor-not-allowed',
          ].join(' ')}
          style={{ color: 'var(--text-muted)', opacity: enabled ? 1 : 0.4 }}
        >
          ⠿
        </span>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-56 max-h-64 overflow-auto p-1"
        data-testid={`tl-swap-menu-${row.staffId}`}
        aria-label={`${row.name}さんの入れ替え相手を選ぶ`}
      >
        {candidates.length === 0 ? (
          <p className="px-2 py-1 text-xs font-normal" style={{ color: 'var(--text-muted)' }}>
            入れ替えられる相手がいません
          </p>
        ) : (
          candidates.map((r) => (
            <button
              key={r.staffId}
              type="button"
              role="menuitem"
              data-testid={`tl-swap-option-${r.staffId}`}
              onClick={() => onPick(r.staffId)}
              className="block w-full truncate rounded px-2 py-1 text-left text-xs font-normal hover:bg-bg-muted"
            >
              {r.name}
              {r.off ? `（${offLabel(r.off)}）` : ''}さんと入れ替える
            </button>
          ))
        )}
        <button
          type="button"
          role="menuitem"
          data-testid={`tl-swap-cancel-${row.staffId}`}
          onClick={() => onOpenChange(false)}
          className="mt-1 block w-full rounded border-t px-2 py-1 text-left text-xs font-normal"
          style={{ borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }}
        >
          やめる
        </button>
      </PopoverContent>
    </Popover>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// バー
// ─────────────────────────────────────────────────────────────────────────

interface BarProps {
  item: BarItem;
  top: number;
  rowKey: string;
  rowName: string;
  canEdit: boolean;
  isPast: boolean;
  drag: DragState | null;
  /** ●未送信 (カイポケへまだ送っていない)。同期バーの集計が唯一の正。 */
  unsent?: boolean;
  onPointerDown: (
    e: React.PointerEvent<HTMLButtonElement>,
    item: BarItem,
    rowKey: string,
    movable: boolean,
  ) => void;
  onPointerMove: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onPointerUp: (e: React.PointerEvent<HTMLButtonElement>) => void;
  onPointerCancel: () => void;
  onClick: (e: React.MouseEvent<HTMLButtonElement>, item: BarItem, rowKey: string) => void;
}

const Bar = React.memo(function Bar({
  item,
  top,
  rowKey,
  rowName,
  canEdit,
  isPast,
  drag,
  unsent = false,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
  onClick,
}: BarProps) {
  const isVisit = item.kind === 'visit';
  const cancelled = isVisit ? item.visit.status === 'cancelled' : item.event.cancelled_at != null;
  const pinned = isVisit ? item.visit.week_pinned === true : false;

  // 状態の理由 (取消・青ピン) を種別の理由 (イベント) より先に出す。
  let blockReason: string | null = null;
  if (!canEdit) blockReason = '閲覧のみのため動かせません。';
  else if (isPast) blockReason = '過去日は変更できません。';
  else if (cancelled) blockReason = '今週だけ取消済みのため動かせません。';
  else if (pinned) blockReason = '青ピン（今週だけ固定）のため動かせません。';
  else if (!isVisit) blockReason = 'イベントはクリックして編集してください。';
  const movable = blockReason === null;

  const durationMin = item.endMin - item.startMin;
  const startMin = drag?.moved ? drag.toStartMin : item.startMin;
  const endMin = startMin + durationMin;

  const palette = isVisit
    ? genderPalette(item.visit.patient_sex)
    : {
        bg: 'var(--sched-event-bg)',
        bar: 'var(--sched-event-bar)',
        ink: 'var(--sched-event-ink)',
        ln: 'var(--sched-event-ln)',
      };

  const title = isVisit ? (item.visit.patient_name ?? '訪問') : item.event.title;
  const sub = isVisit ? (item.visit.course_label ?? '') : 'イベント';
  const timeLabel = `${hhmm(startMin)}〜${hhmm(endMin)}`;
  const ariaLabel = [
    timeLabel,
    title,
    `担当 ${rowName}`,
    pinned ? '青ピン' : '',
    cancelled ? '今週だけ取消' : '',
    blockReason ?? '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      type="button"
      data-testid={isVisit ? `tl-bar-${item.id}` : `tl-event-${item.id}`}
      data-visit-id={isVisit ? item.id : undefined}
      aria-label={ariaLabel}
      aria-disabled={movable ? undefined : true}
      title={blockReason ?? 'ドラッグ=時刻(15分刻み) / 別の行へ=担当変更 / クリック=メニュー'}
      onPointerDown={(e) => onPointerDown(e, item, rowKey, movable)}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onClick={(e) => onClick(e, item, rowKey)}
      className={[
        'absolute overflow-hidden rounded text-left text-[13px] leading-tight',
        'motion-safe:transition-shadow',
        movable ? 'cursor-grab' : 'cursor-not-allowed',
        drag?.moved ? 'z-10 opacity-75 shadow-lg' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      style={{
        left: `${leftPct(startMin)}%`,
        width: `${widthPct(durationMin)}%`,
        top,
        height: BAR_H,
        padding: '5px 8px',
        background: palette.bg,
        borderLeft: `3px solid ${palette.bar}`,
        color: palette.ink,
        textDecoration: cancelled ? 'line-through' : undefined,
        opacity: cancelled ? 0.55 : undefined,
        touchAction: 'none',
      }}
    >
      <b className="block truncate text-[11px] font-medium tabular-nums opacity-70">
        {timeLabel} {sub}
      </b>
      <span className="block truncate">
        {unsent ? (
          <i
            className="mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle"
            style={{ background: 'var(--sched-unsent-dot)' }}
            aria-hidden="true"
            data-testid={`tl-unsent-${item.id}`}
          />
        ) : null}
        {pinned ? '📌 ' : ''}
        {title}
      </span>
    </button>
  );
});

// ─────────────────────────────────────────────────────────────────────────
// ゴースト (突合マーカー)
// ─────────────────────────────────────────────────────────────────────────

function Ghost({
  ghost,
  registerRef,
}: {
  ghost: PlacedGhost;
  registerRef: (el: HTMLElement | null) => void;
}) {
  const { marker, side, startMin, endMin, top } = ghost;
  const isDelete = marker.action === 'delete';
  const before = side === 'before';
  const tag = isDelete
    ? '消えている'
    : before
      ? '今ここ'
      : marker.action === 'add'
        ? 'ここに入る'
        : 'こう変わる';
  const label = marker.patientName ?? marker.title;

  return (
    <div
      ref={registerRef}
      data-testid={`tl-marker-${isDelete ? 'delete' : side}`}
      data-marker-id={marker.externalId}
      className={[
        'absolute overflow-hidden truncate rounded px-2 text-[13px] leading-[44px]',
        !before && !isDelete ? 'motion-safe:animate-pulse' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      style={{
        left: `${leftPct(startMin)}%`,
        width: `${widthPct(endMin - startMin)}%`,
        top,
        height: BAR_H,
        border: before || isDelete ? `1.5px dashed ${GHOST_BEFORE}` : `1.5px solid ${GHOST_AFTER}`,
        borderStyle: isDelete ? 'solid' : before ? 'dashed' : 'solid',
        background: before || isDelete ? GHOST_BEFORE_BG : GHOST_AFTER_BG,
        color: before || isDelete ? GHOST_BEFORE : GHOST_AFTER,
        textDecoration: isDelete ? 'line-through' : undefined,
      }}
    >
      <span
        className="mr-1 rounded-full px-1 text-[9px] font-bold text-white"
        style={{
          background: before || isDelete ? GHOST_BEFORE : GHOST_AFTER,
        }}
      >
        {tag}
      </span>
      <span className="tabular-nums">{hhmm(startMin)}</span> {label}
    </div>
  );
}
