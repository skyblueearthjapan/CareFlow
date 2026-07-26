'use client';

/**
 * TimelineDayBoard — 縦タイムライン日ビュー (T-1・読み取り専用)。
 *
 * docs/plans/schedule-timeline-redesign-design.md / docs/mockups/timeline-mock.html。
 * 列=コース (ヘッダ=担当スタッフ主・コース記号従)、縦=時間軸 9:00〜18:00・30分格子、
 * カード高さ=所要時間に比例、地色=患者性別、勤務外=斜線、空き=「＋n分空き」、
 * 会議・イベント=担当スタッフの列内の藤色帯 (カイポケ反映外・クリックで編集/削除)、
 * 現在時刻ライン。
 *
 * T-1 は読み取り専用: カード / 空き枠クリックで既存の患者詳細 (onPatientClick) を開くのみ。
 * T-2 ②-a: 空き枠クリック→登録 (onFreeSlotClick) を解禁。ハンドラ未指定 (read-only ロール)
 * では従来どおり表示のみ。ドラッグ移動は ②-b で解禁する。データ変換・API・ソルバは
 * 一切持たない (CourseDayTablePanel が組んだ CourseGridVisit をそのまま受け取る = 表示専用)。
 */

import {
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';

import { useDraggable, useDroppable } from '@dnd-kit/core';

import {
  eventDraggableId,
  type CourseGridVisit,
  type PartnerLocation,
} from '@/components/schedule/v2/courseGrid';
import type { CourseV2Read } from '@/lib/queries/courses';
import type { CourseTemplateRead } from '@/lib/schemas/v2/course_template';
import type { StaffRead } from '@/lib/schemas/staff';
import type { EventRead } from '@/lib/schemas/staff-events';
import type { FreeGap } from '@/lib/scheduling/freeGaps';
import { CornerPushPin } from '@/components/ui/push-pin';
import { parseHM, SAME_ADDRESS_PAIR_MIN_OCCUPANCY } from '@/lib/scheduling/freeGaps';
import {
  assignLanes,
  durationToHeight,
  genderKey,
  genderPalette,
  minutesToY,
  TL_DAY_END_MIN,
  TL_DAY_START_MIN,
  TL_MIN_CARD_PX,
  TL_ROW_PX,
  TL_SHOW_ADDR_PX,
  TL_SHOW_PILLS_PX,
  TL_SHOW_SVC_PX,
  timelineHeightPx,
} from '@/lib/scheduling/timeline';
import { cn } from '@/lib/utils';

import type { AccompanimentBinding } from './accompaniment/types';

const COL_MIN_W = 172;
const TIME_RAIL_W = 54;

/**
 * G2: 訪問削除 (×) を出す最小カード高さ。極小カード (15分 = TL_MIN_CARD_PX まで
 * クランプされる) では × が名前を潰すため出さない (リスト表示に削除導線がある)。
 * 30分カード (49px) には出る。
 */
const TL_SHOW_DELETE_PX = 40;

/** G3: 「今週のみ」→ 固定昇格の確認文 (CourseDayTable と同一文言)。 */
const PROMOTE_WEEK_ONLY_CONFIRM = 'この配置を固定訪問週間（毎週の型）に反映しますか？';

// ─────────────────────────────────────────────────────────────────────────
// T-2 ②-b: DnD id 規約 (既存の visit:/pool-patient:/course-day-cell: と非衝突)
// ─────────────────────────────────────────────────────────────────────────

const TL_VISIT_PREFIX = 'tl-visit:';
const TL_COL_PREFIX = 'tl-col:';

export function tlVisitDraggableId(visitId: string): string {
  return `${TL_VISIT_PREFIX}${visitId}`;
}

export function parseTlVisitDraggableId(id: string): string | null {
  return id.startsWith(TL_VISIT_PREFIX) ? id.slice(TL_VISIT_PREFIX.length) : null;
}

/** 同住所ペアボックス (2名セット移動)。id には両 visit id を埋める。 */
const TL_PAIR_PREFIX = 'tl-pair:';

export function tlPairDraggableId(visitId1: string, visitId2: string): string {
  return `${TL_PAIR_PREFIX}${visitId1}:${visitId2}`;
}

export function parseTlPairDraggableId(id: string): [string, string] | null {
  if (!id.startsWith(TL_PAIR_PREFIX)) return null;
  const rest = id.slice(TL_PAIR_PREFIX.length);
  const sep = rest.indexOf(':');
  if (sep <= 0 || sep >= rest.length - 1) return null;
  return [rest.slice(0, sep), rest.slice(sep + 1)];
}

/** 列 droppable id。colKey = `${template.id}:${weekday}` (TimelineCourseColumn.key)。 */
export function tlColDroppableId(colKey: string): string {
  return `${TL_COL_PREFIX}${colKey}`;
}

export function parseTlColDroppableId(id: string): string | null {
  return id.startsWith(TL_COL_PREFIX) ? id.slice(TL_COL_PREFIX.length) : null;
}

/** VisitCard へ渡すドラッグ束 (DraggableVisitCard が組む)。 */
type DragBindings = Pick<
  ReturnType<typeof useDraggable>,
  'attributes' | 'listeners' | 'setNodeRef' | 'isDragging'
> & { disabled: boolean };

export interface TimelineCourseColumn {
  key: string;
  template: CourseTemplateRead;
  course: CourseV2Read | null;
  officeName: string;
  visits: CourseGridVisit[];
  /** 担当スタッフ (course.assigned_staff_id を解決したもの)。ヘッダのアバター色に sex を使う。 */
  assignedStaff: StaffRead | null;
  /**
   * PO 2026-07-09: course.assigned_staff_id が「削除済み staff」を指す (= active な
   * staff 一覧に見つからない stale ID) 場合 true。列ヘッダで名前の代わりに
   * （担当不在）を琥珀色で出す（担当 null の「（未割当）」とは区別する）。
   */
  assignedStaffMissing?: boolean;
  freeGaps: FreeGap[];
  capacity: { filled: number; max: number };
  /** 当該コース担当スタッフの当日イベント (勤務外/会議帯として描く元)。 */
  staffEvents: EventRead[];
  /** G1: 列ヘッダの担当スタッフ変更 <select> の選択肢 (拠点スコープ済み)。 */
  staffOptions: StaffRead[];
}

/**
 * スタッフ枠 (PO確定 2026-07-26): その日コースを持たないがイベント (休み/面談/📝) の
 * あるスタッフを、コース列と同格の「枠」として盤面に編み込む。
 * その人にもその日のスケジュールがある、をUIで表現する。読み取り専用。
 */
export interface StaffEventFrame {
  staff: StaffRead;
  events: EventRead[];
}

export interface TimelineDayBoardProps {
  columns: TimelineCourseColumn[];
  /** コース無し・イベントありスタッフの枠 (コース列の右に描画)。 */
  staffFrames?: StaffEventFrame[];
  weekdayLabel: string;
  /** カード / 空き枠クリック → 患者詳細 (既存ダイアログ)。T-1 は詳細を開くのみ。 */
  onPatientClick?: (patientId: string) => void;
  /** 現在時刻ライン (0時起点の分)。当日でないときは null で非表示。 */
  nowMinutes?: number | null;
  /**
   * T-2 ②-a: 空き枠クリック → 登録モーダル。canEdit のときだけ Panel が渡す
   * (未指定 = read-only ロール = 従来の表示専用のまま)。
   */
  onFreeSlotClick?: (col: TimelineCourseColumn, gap: FreeGap) => void;
  /**
   * イベント帯クリック → 編集/削除ダイアログ (既存 EventEditDialog)。canEdit のときだけ
   * Panel が渡す。未指定時は従来どおり表示専用 (pointer-events-none)。
   */
  onEventClick?: (ev: EventRead, col: TimelineCourseColumn) => void;
  /**
   * T-2 ②-b: カード DnD (15分スナップ移動)。true のとき単独カードが draggable になり
   * 列が droppable になる (親 Panel の DndContext / handleDragEnd が tl-visit:/tl-col: を
   * 解釈)。canEdit のときだけ Panel が true を渡す。同住所ペアボックスは
   * 余白=2名セット移動・各行の ⠿ ハンドル=1名ずつ個別移動 (T-6 パリティ③)。
   */
  dndEnabled?: boolean;
  /**
   * G1 (T-6 パリティ): 列ヘッダの担当スタッフ変更。canEdit のときだけ Panel が渡す。
   * 未指定 (= read-only ロール) では従来どおり担当名のテキスト表示のまま。
   */
  onChangeAssignedStaff?: (col: TimelineCourseColumn, staffId: string | null) => void;
  /** 担当変更 mutation 実行中 (select を一時 disable する)。 */
  isStaffMutating?: boolean;
  /**
   * G2 (T-6 パリティ): 訪問削除 (カード右下の ×)。canEdit のときだけ Panel が渡す。
   * 未指定なら × を描画しない。確認ダイアログは Panel 側 (handleDeleteVisit) が持つ。
   */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /**
   * G3 (T-6 パリティ): source='manual_week' のカードに出す「今週のみ」チップの昇格。
   * 未指定なら非クリックの表示専用チップになる (テーブルの read-only 表示と同じ)。
   */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /**
   * 新人同行 (§7.1/§7.2)。active なら列ヘッダ/カードが選択トグル (通常操作は親が抑止)、
   * inactive なら常時表示バッジを描く。
   */
  accompaniment?: AccompanimentBinding;
  /** 同行モードの defaults 化に使う現在の曜日 (0=月..6=日)。 */
  accompanimentWeekday?: number;
}

/** カード / ペア行の右下に置く訪問削除 (×) ボタン (G2)。 */
function DeleteVisitButton({
  visit,
  onDeleteVisit,
}: {
  visit: CourseGridVisit;
  onDeleteVisit: (visitId: string, patientName: string) => void;
}) {
  const label = visit.patient_name ?? visit.patient_id;
  return (
    <button
      type="button"
      data-testid={`tl-delete-visit-${visit.id}`}
      aria-label={`${label} の訪問を削除`}
      title="この訪問を削除"
      className="absolute bottom-0.5 right-0.5 z-[3] grid h-[15px] w-[15px] place-items-center rounded-full border border-transparent bg-bg-base/80 text-[11px] font-bold leading-none text-error opacity-0 transition-opacity hover:border-error hover:bg-error-bg group-focus-within:opacity-100 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-error"
      onClick={(ev) => {
        // カード本体の onClick (患者詳細) を発火させない。
        ev.stopPropagation();
        onDeleteVisit(visit.id, label);
      }}
      onPointerDown={(ev) => {
        // dnd-kit の activator (カード側 listeners.onPointerDown) へ伝播させない。
        ev.stopPropagation();
      }}
    >
      ×
    </button>
  );
}

/**
 * 「今週のみ」チップ (G3)。source='manual_week' = この週だけの配置。
 * onPromoteWeekOnly 指定時のみクリック可 (confirm → 固定訪問週間へ昇格)。
 */
function WeekOnlyChip({
  visit,
  onPromoteWeekOnly,
  className,
}: {
  visit: CourseGridVisit;
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  className?: string;
}) {
  const base = cn(
    'shrink-0 rounded-full bg-amber-100 px-1.5 py-px text-[8.5px] font-bold text-amber-800 ring-1 ring-amber-300',
    className,
  );
  if (!onPromoteWeekOnly) {
    return (
      <span
        data-testid={`tl-week-only-chip-${visit.id}`}
        className={base}
        title="この週だけの配置です"
      >
        今週のみ
      </span>
    );
  }
  return (
    <button
      type="button"
      data-testid={`tl-week-only-chip-${visit.id}`}
      className={cn(base, 'cursor-pointer hover:bg-amber-200')}
      title="この週だけの配置です。クリックで固定訪問週間（毎週の型）に反映できます"
      onClick={(ev) => {
        ev.stopPropagation();
        if (window.confirm(PROMOTE_WEEK_ONLY_CONFIRM)) {
          onPromoteWeekOnly(visit.patient_id, visit.patient_name ?? visit.patient_id);
        }
      }}
      onPointerDown={(ev) => {
        ev.stopPropagation();
      }}
    >
      今週のみ
    </button>
  );
}

/** カードルート (div role=button) のキーボード操作 = Enter/Space で onClick 相当。 */
function cardKeyDownHandler(onClick?: () => void) {
  if (!onClick) return undefined;
  return (ev: ReactKeyboardEvent<HTMLDivElement>) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    // Space のページスクロール / Enter のフォーム送信を抑止。
    ev.preventDefault();
    onClick();
  };
}

/**
 * 会議・イベント帯は「そのイベントを持つ担当スタッフの列」にだけ描く (TimelineColumn 内)。
 * 旧実装は全列共通の全幅帯 (モックの全社会議相当) だったが、実データは per-staff の
 * staff_events であり「全員に入った」ように誤読されるため列内表示へ改めた (PO指摘 2026-07-08)。
 */

/**
 * T-6 撤去に伴う移設: 2 名体制 visit の「相方の現在地」と ①/② スロット。
 *
 * 旧 CourseDayTable (Wave 37 P3-C / Wave 38) がセル下部に出していた情報で、
 * タイムライン/日リストには無かった。テーブル撤去でこの運用情報が失われるのを防ぐ。
 *   - kind='cell' → 相方は別セルに配置済み ("相方: 本店-A 15:00")
 *   - kind='pool' → 相方がプール残存 = 片側しか置けていない ⇒ 警告 ("複数 ① のみ")
 */
export function partnerInfo(visit: {
  group_slot_label?: 1 | 2;
  partner_location?: PartnerLocation | null;
  partner_label?: string | null;
}): {
  slotMark: string | null;
  text: string | null;
  warn: boolean;
  tooltip: string | null;
} {
  const slotMark = visit.group_slot_label === 1 ? '①' : visit.group_slot_label === 2 ? '②' : null;
  const loc = visit.partner_location ?? null;
  if (!loc) return { slotMark, text: null, warn: false, tooltip: null };
  const warn = loc.kind === 'pool';
  const where = loc.kind === 'cell' ? `${loc.cellLabel} ${loc.time}` : 'プール';
  const text = warn && slotMark ? `複数 ${slotMark} のみ (相方未配置)` : `相方: ${where}`;
  const pairName = visit.partner_label ? `ペア: ${visit.partner_label} / ` : '';
  return { slotMark, text, warn, tooltip: `${pairName}相方: ${where}` };
}

function PersonMark() {
  return (
    <svg viewBox="0 0 10 10" width="9" height="9" aria-hidden="true" className="inline-block">
      <circle cx="5" cy="3" r="2.1" fill="currentColor" />
      <path d="M1.2 9.4c.5-2 2-3 3.8-3s3.3 1 3.8 3z" fill="currentColor" />
    </svg>
  );
}

/** レーン (時間帯が重なる訪問の左右分割)。lane=0/laneCount=1 のとき全幅。 */
interface CardLane {
  lane: number;
  laneCount: number;
}

function VisitCard({
  visit,
  onClick,
  laneInfo,
  drag,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  visit: CourseGridVisit;
  onClick?: () => void;
  laneInfo?: CardLane;
  /** T-2 ②-b: DnD 有効時のみ DraggableVisitCard が渡す。無指定=従来の表示/クリック専用。 */
  drag?: DragBindings;
  /** G2: 訪問削除 (×)。未指定なら描画しない。 */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /** G3: 「今週のみ」チップの昇格ハンドラ。未指定でもチップ自体は表示する。 */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /** 新人同行 (§7.1/§7.2)。 */
  accompaniment?: AccompanimentBinding;
}) {
  // T-2 ②-c: ピン留めカードを掴もうとしたら shake で「不可侵」を伝える (ドラッグは
  // disabled で始まらないため、pointerdown を合図に演出だけ出す)。
  const [shake, setShake] = useState(false);
  const accActive = accompaniment?.active === true;
  const accSelected = accActive && accompaniment!.isVisitSelected(visit.id);
  const accInCourse = accActive && accompaniment!.isVisitInSelectedCourse(visit.id);
  const accOverlap = accActive && accompaniment!.isVisitOverlapping(visit.id);
  const accBadge =
    accompaniment && !accompaniment.active ? accompaniment.visitBadgeName(visit.id) : null;
  const startMin = parseHM(visit.start_time);
  const endMin = parseHM(visit.end_time);
  if (startMin === null || endMin === null || endMin <= startMin) return null;
  const isCancelled = visit.status === 'cancelled';
  const pal = genderPalette(visit.patient_sex);
  // 範囲外 (9:00前/18:00後) はカードを軸内にクランプして隠れ・はみ出しを防ぐ (LOW-4)。
  const clampedStart = Math.max(startMin, TL_DAY_START_MIN);
  const clampedEnd = Math.min(endMin, TL_DAY_END_MIN);
  const top = minutesToY(clampedStart) + 1;
  const rawH = durationToHeight(Math.max(clampedEnd - clampedStart, 0)) - 3;
  const height = Math.max(rawH, TL_MIN_CARD_PX);
  const durMin = endMin - startMin;
  const isMulti = visit.patient_requires_multiple_staff || visit.group_slot_label != null;
  // T-6撤去: 旧テーブルにしか無かった 2 名体制の運用情報をカードへ移設。
  //   ①/② = visit_group 内の slot、相方の現在地 = 別セル配置済み or プール残存 (= 未配置)。
  const partner = partnerInfo(visit);

  // 重なり時のみ左右に分割 (MED-1)。laneCount=1 は全幅 (従来どおり)。
  const lanes = laneInfo?.laneCount ?? 1;
  const lane = laneInfo?.lane ?? 0;
  const laneStyle =
    lanes > 1
      ? {
          left: `calc(3px + ${(lane / lanes) * 100}% - ${(lane / lanes) * 6}px)`,
          width: `calc(${100 / lanes}% - ${6 / lanes}px)`,
          right: 'auto' as const,
        }
      : { left: '3px', right: '3px' };

  const pills: string[] = [];
  if (visit.patient_sex_restriction_label) pills.push(visit.patient_sex_restriction_label);
  if (isMulti) pills.push('2名');
  if (visit.same_address_group_id) pills.push('📍同住所');
  const isWeekOnly = visit.source === 'manual_week';
  // 同行モード中はカードクリック=選択トグル (選択済みコース内は個別不可)。
  const accClick = accActive
    ? accInCourse
      ? undefined
      : () => accompaniment!.toggleVisit(visit.id)
    : onClick;

  return (
    // G2: 内部に × ボタンを置くため、ルートは <button> ではなく div role=button。
    // (button in button は無効 HTML。キーボード操作は onKeyDown で維持する)
    // dnd-kit の setNodeRef / listeners / attributes はルートのまま = DnD 不変。
    <div
      role="button"
      tabIndex={0}
      ref={drag?.setNodeRef}
      onClick={accClick}
      // DnD 有効カード = dnd-kit の listeners/attributes、ピン留め = shake ハンドラ、を
      // 1 つの spread に合成する。別プロップで onPointerDown を書くと undefined でも
      // 後勝ちで listeners の onPointerDown を上書きし、ドラッグが死ぬ (②-c で実バグ化)。
      {...(drag
        ? drag.disabled
          ? visit.is_pinned === true
            ? { onPointerDown: () => setShake(true) }
            : {}
          : { ...drag.listeners, ...drag.attributes }
        : {})}
      // onKeyDown は spread の「後」に置く (後勝ち)。将来 KeyboardSensor を足しても
      // listeners.onKeyDown に黙って上書きされずキーボード操作が生き残る (レビューMED)。
      onKeyDown={cardKeyDownHandler(accClick)}
      onAnimationEnd={shake ? () => setShake(false) : undefined}
      data-testid={`tl-visit-${visit.id}`}
      data-accompaniment-selected={accSelected ? 'true' : undefined}
      data-tl-drag={drag ? (drag.disabled ? 'disabled' : 'enabled') : undefined}
      // 小さいカードは行が出ないため、相方情報はツールチップで必ず補完する。
      title={
        accInCourse
          ? 'コース丸ごとに含まれています（個別解除はコース選択を外してください）'
          : [
          drag?.disabled
            ? visit.is_pinned
              ? 'ピン留め中のため移動できません（ピンを解除してから移動）'
              : visit.visit_group_id
                ? '2名体制（ペア配置）のため個別移動できません'
                : visit.status === 'cancelled'
                  ? 'キャンセル済みのため移動できません'
                  : visit.patient_address
            : visit.patient_address,
          partner.tooltip,
        ]
          .filter(Boolean)
          .join(' / ') || undefined
      }
      className={cn(
        // group: × ボタンを hover / focus-within で出すため (G2)。
        'group absolute z-[2] flex flex-col gap-px rounded-lg border border-l-[3px] px-2 py-[3px] text-left shadow-[var(--shadow-xs)] transition-shadow hover:z-[4] hover:shadow-[var(--shadow-md)] focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
        drag && !drag.disabled && 'cursor-grab touch-none active:cursor-grabbing',
        drag?.isDragging && 'opacity-40',
        shake && 'tl-shake',
        accActive && !accInCourse && 'cursor-pointer',
        accOverlap && 'z-[4] ring-2 ring-error',
        accSelected && !accOverlap && 'z-[4] ring-2 ring-brand-primary',
      )}
      style={{
        top,
        height,
        ...laneStyle,
        background: isCancelled ? 'var(--bg-muted)' : pal.bg,
        borderColor: accOverlap ? 'var(--error)' : isCancelled ? 'var(--border-default)' : pal.ln,
        borderLeftColor: accOverlap
          ? 'var(--error)'
          : isCancelled
            ? 'var(--text-muted)'
            : pal.bar,
        color: isCancelled ? 'var(--text-muted)' : pal.ink,
        opacity: isCancelled ? 0.7 : 1,
      }}
    >
      {/* 同行モード: 選択チェック / 常時表示: 個別同行バッジ (§7.2)。 */}
      {accActive && accSelected && (
        <span
          className="absolute left-0.5 top-0.5 z-[3] grid h-4 w-4 place-items-center rounded-full bg-brand-primary text-[9px] font-bold text-white"
          aria-hidden="true"
        >
          ✓
        </span>
      )}
      {/* 個別同行バッジは右上へ (患者名との被り解消・PO要望)。名は左寄せ truncate なので
          右上は空く。ピン留めカードは右上の画鋲を避けて少し左へ寄せる。 */}
      {accBadge && (
        <span
          className={cn(
            'absolute top-0.5 z-[3] max-w-[70%] truncate rounded-full bg-info-bg px-1 text-[8.5px] font-bold text-info',
            visit.is_pinned ? 'right-[16px]' : 'right-0.5',
          )}
          data-testid={`tl-accompaniment-badge-${visit.id}`}
          title={`同行: ${accBadge}`}
        >
          👥{accBadge}
        </span>
      )}
      {/* ピン留め: 右上に打ち込んだ画鋲 (📍住所と誤認しない・PO要望 2026-07-08)。 */}
      {visit.is_pinned && <CornerPushPin />}
      {/* 1行目: アイコン + 患者名 (名前に行を専有させフル表示。切れにくくする)。 */}
      <span className="flex min-w-0 items-center gap-1">
        {isMulti && (
          <span className="inline-flex shrink-0 gap-px text-brand-primary" aria-label="2名体制">
            <PersonMark />
            <PersonMark />
          </span>
        )}
        <span
          className={cn(
            'truncate text-[13px] font-bold leading-tight',
            isCancelled && 'line-through',
          )}
        >
          {visit.patient_name ?? '—'}
        </span>
        {/* ①/② = 2名体制の slot (旧テーブルから移設)。相方未配置なら警告色。 */}
        {partner.slotMark && (
          <span
            data-testid={`tl-slot-mark-${visit.id}`}
            aria-label={`2名体制 ${partner.slotMark}`}
            className={cn(
              'shrink-0 text-[11px] font-bold leading-none',
              partner.warn ? 'text-error' : 'opacity-70',
            )}
          >
            {partner.slotMark}
          </span>
        )}
      </span>
      {/* 2行目: 時刻・所要分 ＋ サービス種別 (高さがあるとき)。住所は常に3行目 (重複させない)。 */}
      {height >= TL_SHOW_SVC_PX && (
        <span className="flex min-w-0 items-center gap-1.5 text-[10px] opacity-80">
          <span className="tnum shrink-0 font-semibold">
            {(visit.start_time ?? '').slice(0, 5)}・{durMin}分
          </span>
          <span className="truncate">
            {isCancelled ? 'キャンセル' : (visit.patient_time_type ?? '')}
          </span>
        </span>
      )}
      {/* 3行目: 住所 (30分カードから表示・PO要望。極小カードは title ツールチップで補完)。
          30分カード(49px)は3行でほぼ満杯のため 9px + leading-tight で見切れを防ぐ。 */}
      {visit.patient_address && height >= TL_SHOW_ADDR_PX && (
        <span className="flex min-w-0 items-center gap-0.5 text-[9px] leading-tight opacity-75">
          <span className="shrink-0">📍</span>
          <span className="truncate">{visit.patient_address}</span>
        </span>
      )}
      {/* 相方の現在地 (旧テーブルの「相方: 本店-A 15:00」注記を移設)。
          プール残存 = 片側しか配置できていない運用エラーなので警告色で出す。
          小さいカードは title ツールチップ側で補完する。 */}
      {partner.text && height >= TL_SHOW_ADDR_PX && (
        <span
          data-testid={`tl-partner-note-${visit.id}`}
          className={cn(
            'flex min-w-0 items-center gap-0.5 text-[9px] leading-tight',
            partner.warn ? 'font-bold text-error' : 'opacity-75',
          )}
        >
          <span className="shrink-0">👥</span>
          <span className="truncate">{partner.text}</span>
        </span>
      )}
      {(pills.length > 0 || isWeekOnly) && height >= TL_SHOW_PILLS_PX && (
        <span className="mt-auto flex flex-wrap items-center gap-[3px] pb-px">
          {/* G3: 「今週のみ」= この週だけの配置。クリックで毎週の型へ昇格 (confirm あり)。 */}
          {isWeekOnly && <WeekOnlyChip visit={visit} onPromoteWeekOnly={onPromoteWeekOnly} />}
          {pills.map((p) => (
            <span
              key={p}
              className="rounded-full px-1.5 py-px text-[8.5px] font-bold text-white"
              style={{ background: pal.bar }}
            >
              {p}
            </span>
          ))}
        </span>
      )}
      {/* G2: 訪問削除 (×)。右上は CornerPushPin が刺さっているため右下に置く。
          極小カード (< TL_SHOW_DELETE_PX) では潰れるので出さない。 */}
      {onDeleteVisit && height >= TL_SHOW_DELETE_PX && (
        <DeleteVisitButton visit={visit} onDeleteVisit={onDeleteVisit} />
      )}
    </div>
  );
}

/**
 * T-2 ②-b: ドラッグ可能な訪問カード。掴めない条件 = ピン留め / キャンセル / 2名ペア
 * (visit_group_id)。既存テーブル (OccupantNameDraggable) と同じガード。
 * dnd-kit の DndContext は親 (CourseDayTablePanel) が持つ — dndEnabled のときのみ
 * このコンポーネントが描画されるため、テスト等の DndContext 外では VisitCard を直接使う。
 */
function DraggableVisitCard({
  visit,
  onClick,
  laneInfo,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  visit: CourseGridVisit;
  onClick?: () => void;
  laneInfo?: CardLane;
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  accompaniment?: AccompanimentBinding;
}) {
  const dragDisabled =
    visit.is_pinned === true || visit.status === 'cancelled' || Boolean(visit.visit_group_id);
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: tlVisitDraggableId(visit.id),
    disabled: dragDisabled,
    data: { kind: 'tl-visit', visitId: visit.id },
  });
  return (
    <VisitCard
      visit={visit}
      onClick={onClick}
      laneInfo={laneInfo}
      drag={{ attributes, listeners, setNodeRef, isDragging, disabled: dragDisabled }}
      onDeleteVisit={onDeleteVisit}
      onPromoteWeekOnly={onPromoteWeekOnly}
      accompaniment={accompaniment}
    />
  );
}

/**
 * 同住所ペアの2名セットドラッグ (PO要望: 基本は2名一緒に動かす)。
 * 掴めない条件 = どちらかがピン留め or キャンセル。個別移動は将来対応
 * (どこを掴むと1名/2名かの UI 区分けが必要なため。当面は個別=テーブルビュー)。
 */
function DraggablePairBox({
  item,
  laneInfo,
  onPatientClick,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  item: Extract<RenderItem, { kind: 'pair' }>;
  laneInfo?: CardLane;
  onPatientClick?: (patientId: string) => void;
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  accompaniment?: AccompanimentBinding;
}) {
  const dragDisabled = item.visits.some(
    (v) => v.is_pinned === true || v.status === 'cancelled' || Boolean(v.visit_group_id),
  );
  const [id1, id2] = [item.visits[0]!.id, item.visits[1]?.id ?? item.visits[0]!.id];
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: tlPairDraggableId(id1, id2),
    disabled: dragDisabled,
    data: { kind: 'tl-pair', visitIds: [id1, id2] },
  });
  return (
    <PairBox
      item={item}
      laneInfo={laneInfo}
      onPatientClick={onPatientClick}
      drag={{ attributes, listeners, setNodeRef, isDragging, disabled: dragDisabled }}
      // T-6 パリティ③: 各行の ⠿ ハンドルから 1 名ずつ個別移動できる。
      memberDndEnabled
      onDeleteVisit={onDeleteVisit}
      onPromoteWeekOnly={onPromoteWeekOnly}
      accompaniment={accompaniment}
    />
  );
}

/** DragOverlay 用: ペアボックス実寸ゴースト (2名セットのまま動く)。 */
export function TlPairDragGhost({ visits }: { visits: CourseGridVisit[] }) {
  return (
    <div
      className="flex h-full w-full cursor-grabbing flex-col rounded-lg border-2 border-amber-400 bg-amber-50/70 shadow-[var(--shadow-md)]"
      data-testid="tl-pair-drag-ghost"
    >
      <div className="flex items-center gap-1 px-1.5 pt-0.5 text-[9px] font-bold text-amber-700">
        📍 同住所 2名セット
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 px-1 pb-1">
        {visits.map((v) => {
          const pal = genderPalette(v.patient_sex);
          return (
            <div
              key={v.id}
              className="relative flex min-h-0 flex-1 items-center gap-1 rounded-md border border-l-[3px] px-1.5"
              style={{
                background: pal.bg,
                borderColor: pal.ln,
                borderLeftColor: pal.bar,
                color: pal.ink,
              }}
            >
              {v.is_pinned && <CornerPushPin />}
              <span className="truncate text-[12px] font-bold leading-tight">
                {v.patient_name ?? '—'}
              </span>
              <span className="tnum ml-auto shrink-0 text-[9px] opacity-75">
                {(v.start_time ?? '').slice(0, 5)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * T-2 ②-b: 列全体を 1 つの droppable にする透明レイヤ。ドロップ位置は Panel 側で
 * 「カードの translated top − 列 rect top」から 15 分スナップで算出する
 * (テーブルの15分固定行セルの代替 = 連続時間軸)。pointer-events は不要
 * (dnd-kit の衝突判定は rect ベース)。
 */
function ColumnDropLayer({ colKey }: { colKey: string }) {
  const { setNodeRef, isOver } = useDroppable({
    id: tlColDroppableId(colKey),
    data: { colKey },
  });
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'pointer-events-none absolute inset-0 z-[1] rounded transition-colors',
        isOver && 'bg-brand-primary/5 ring-2 ring-inset ring-brand-primary/60',
      )}
      data-testid={`tl-col-drop-${colKey}`}
      aria-hidden="true"
    />
  );
}

/**
 * DragOverlay 用のカード実寸ゴースト。dnd-kit の DragOverlay は掴んだノードと同じ
 * 幅・高さになるため、h-full/w-full で埋めると「時間=面積」のままカードごと動く。
 * (旧: 小さな名前チップ → カードのサイズ感が失われる、の PO 指摘対応)
 */
export function TlVisitDragGhost({ visit }: { visit: CourseGridVisit }) {
  const pal = genderPalette(visit.patient_sex);
  const s = parseHM(visit.start_time);
  const e = parseHM(visit.end_time);
  const durMin = s !== null && e !== null && e > s ? e - s : null;
  return (
    <div
      className="relative flex h-full w-full cursor-grabbing flex-col gap-px rounded-lg border border-l-[3px] px-2 py-[3px] shadow-[var(--shadow-md)]"
      style={{ background: pal.bg, borderColor: pal.ln, borderLeftColor: pal.bar, color: pal.ink }}
      data-testid="tl-drag-ghost"
    >
      {visit.is_pinned && <CornerPushPin />}
      <span className="flex min-w-0 items-center gap-1">
        <span className="truncate text-[13px] font-bold leading-tight">
          {visit.patient_name ?? '—'}
        </span>
      </span>
      {durMin !== null && (
        <span className="tnum text-[10px] font-semibold opacity-80">
          {(visit.start_time ?? '').slice(0, 5)}・{durMin}分
        </span>
      )}
      {visit.patient_address && (
        <span className="flex min-w-0 items-center gap-0.5 text-[9.5px] opacity-75">
          <span className="shrink-0">📍</span>
          <span className="truncate">{visit.patient_address}</span>
        </span>
      )}
    </div>
  );
}

/** 描画単位: 単独訪問 or 同住所・同時刻の 2 名ペア (90分占有ボックス)。 */
type RenderItem =
  | { kind: 'single'; id: string; v: CourseGridVisit; startMin: number; endMin: number }
  | {
      kind: 'pair';
      id: string;
      visits: CourseGridVisit[];
      startMin: number;
      endMin: number;
    };

/**
 * 同住所・同時刻の 2 名 (別患者・同 same_address_group_id・同 start) を 1 つの
 * 「90分占有ペア」にまとめる。1 スタッフが連続で回るため実占有は 90 分
 * (SAME_ADDRESS_PAIR_MIN_OCCUPANCY)。タイムライン上でも 90 分ぶんの高さで描く。
 * それ以外は単独訪問。開始時刻が不正/範囲外の訪問は除外 (カード側でも null)。
 */
function buildRenderItems(visits: ReadonlyArray<CourseGridVisit>): RenderItem[] {
  const sorted = [...visits].sort(
    (a, b) => (parseHM(a.start_time) ?? 0) - (parseHM(b.start_time) ?? 0),
  );
  const used = new Set<string>();
  const items: RenderItem[] = [];
  for (const v of sorted) {
    if (used.has(v.id)) continue;
    const s = parseHM(v.start_time);
    const e = parseHM(v.end_time);
    if (s === null || e === null || e <= s) continue;
    const gid = v.same_address_group_id ?? null;
    if (gid) {
      const mate = sorted.find(
        (o) =>
          o.id !== v.id &&
          !used.has(o.id) &&
          (o.same_address_group_id ?? null) === gid &&
          o.patient_id !== v.patient_id &&
          parseHM(o.start_time) === s,
      );
      if (mate) {
        used.add(v.id);
        used.add(mate.id);
        const me = parseHM(mate.end_time) ?? e;
        const endMin = Math.max(s + SAME_ADDRESS_PAIR_MIN_OCCUPANCY, e, me);
        items.push({
          kind: 'pair',
          id: `pair:${v.id}:${mate.id}`,
          visits: [v, mate],
          startMin: s,
          endMin,
        });
        continue;
      }
    }
    used.add(v.id);
    items.push({ kind: 'single', id: v.id, v, startMin: s, endMin: e });
  }
  return items;
}

/**
 * 同住所ペアの行 1 枚 (T-6 パリティ③)。drag 指定時は行頭の ⠿ ハンドルから
 * 1 名だけ個別に移動できる (行本体クリック=患者詳細・箱の余白=2名セット移動と共存)。
 * ハンドルは pointerdown/click を箱・行へバブリングさせない (Capture 側の別名
 * プロップで stopPropagation — dnd listeners の onPointerDown を上書きしない。
 * 64acdef の教訓)。
 */
function PairMemberRowView({
  v,
  onPatientClick,
  drag,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  v: CourseGridVisit;
  onPatientClick?: (patientId: string) => void;
  drag?: DragBindings;
  /** G2: ペア行からも訪問削除できる (テーブルの × と同じ機能パリティ)。 */
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  /** G3: ペア行の「今週のみ」チップ。 */
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /** 新人同行 (§7.1/§7.2)。 */
  accompaniment?: AccompanimentBinding;
}) {
  const pal = genderPalette(v.patient_sex);
  const s = parseHM(v.start_time);
  const e = parseHM(v.end_time);
  const dm = s !== null && e !== null && e > s ? e - s : null;
  const showHandle = drag != null && !drag.disabled;
  const accActive = accompaniment?.active === true;
  const accSelected = accActive && accompaniment!.isVisitSelected(v.id);
  const accInCourse = accActive && accompaniment!.isVisitInSelectedCourse(v.id);
  const accOverlap = accActive && accompaniment!.isVisitOverlapping(v.id);
  const accBadge =
    accompaniment && !accompaniment.active ? accompaniment.visitBadgeName(v.id) : null;
  const onRowClick = accActive
    ? accInCourse
      ? undefined
      : () => accompaniment!.toggleVisit(v.id)
    : onPatientClick
      ? () => onPatientClick(v.patient_id)
      : undefined;
  return (
    // G2: 行内に × ボタン (と 今週のみ チップ) を置くため div role=button 化。
    // drag の setNodeRef / listeners はルートのまま = ペア行の個別移動は不変。
    <div
      role="button"
      tabIndex={0}
      ref={drag?.setNodeRef}
      data-testid={`tl-visit-${v.id}`}
      data-tl-member-drag={drag ? (drag.disabled ? 'disabled' : 'enabled') : undefined}
      data-accompaniment-selected={accSelected ? 'true' : undefined}
      onClick={onRowClick}
      onKeyDown={cardKeyDownHandler(onRowClick)}
      title={
        accInCourse
          ? 'コース丸ごとに含まれています（個別解除はコース選択を外してください）'
          : undefined
      }
      className={cn(
        'group relative flex min-h-0 flex-1 flex-col justify-center gap-px rounded-md border border-l-[3px] px-1.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
        showHandle && 'pl-4',
        onDeleteVisit && 'pr-4',
        drag?.isDragging && 'opacity-40',
        accActive && !accInCourse && 'cursor-pointer',
        accOverlap && 'ring-2 ring-error',
        accSelected && !accOverlap && 'ring-2 ring-brand-primary',
      )}
      style={{
        background: pal.bg,
        borderColor: accOverlap ? 'var(--error)' : pal.ln,
        borderLeftColor: accOverlap ? 'var(--error)' : pal.bar,
        color: pal.ink,
      }}
    >
      {accActive && accSelected && (
        <span
          className="absolute left-0.5 top-0.5 z-[3] grid h-3 w-3 place-items-center rounded-full bg-brand-primary text-[7px] font-bold text-white"
          aria-hidden="true"
        >
          ✓
        </span>
      )}
      {accBadge && (
        <span
          className={cn(
            'absolute top-0.5 z-[3] max-w-[46px] truncate rounded-full bg-info-bg px-1 text-[7.5px] font-bold text-info',
            v.is_pinned ? 'right-[16px]' : 'right-0.5',
          )}
          data-testid={`tl-accompaniment-badge-${v.id}`}
          title={`同行: ${accBadge}`}
        >
          👥{accBadge}
        </span>
      )}
      {v.is_pinned && <CornerPushPin />}
      {showHandle ? (
        <span
          data-testid={`tl-pair-member-handle-${v.id}`}
          title="この1名だけ移動（ドラッグ）"
          className="absolute bottom-0 left-0 top-0 flex w-4 cursor-grab touch-none items-center justify-center text-[11px] leading-none opacity-50 hover:opacity-100 active:cursor-grabbing"
          // dnd の activator (listeners.onPointerDown) を呼んでから伝播を止める。
          // Capture 側の stopPropagation は React 合成イベントでは同一要素の bubble
          // ハンドラ (= activator) まで止めてしまうため不可。意図的な同名上書きだが
          // 原本を必ず呼ぶ (64acdef の教訓は「undefined での事故上書き」の禁止)。
          {...(drag
            ? {
                ...drag.attributes,
                ...drag.listeners,
                onPointerDown: (ev: ReactPointerEvent<HTMLSpanElement>) => {
                  (
                    drag.listeners as
                      | { onPointerDown?: (e: ReactPointerEvent<HTMLSpanElement>) => void }
                      | undefined
                  )?.onPointerDown?.(ev);
                  // 箱の 2 名セット drag へバブリングさせない。
                  ev.stopPropagation();
                },
                onClick: (ev: ReactMouseEvent) => {
                  // 行クリック (患者詳細) をハンドルでは発火させない。
                  ev.preventDefault();
                  ev.stopPropagation();
                },
              }
            : {})}
        >
          ⠿
        </span>
      ) : null}
      {/* 1行目: 右端の時刻が右上バッジと重ならないよう、バッジ有り時は右パディングで逃がす。 */}
      <span
        className={cn(
          'flex min-w-0 items-center gap-1',
          accBadge && (v.is_pinned ? 'pr-[64px]' : 'pr-[50px]'),
        )}
      >
        <span className="truncate text-[12px] font-bold leading-tight">
          {v.patient_name ?? '—'}
        </span>
        <span className="tnum ml-auto shrink-0 text-[9px] opacity-75">
          {(v.start_time ?? '').slice(0, 5)}
          {dm !== null ? `・${dm}分` : ''}
        </span>
      </span>
      {/* 2行目: 📍住所 (通常カードと同じ配置。同住所なので2行とも同じ住所・PO要望)。 */}
      {v.patient_address && (
        <span className="flex min-w-0 items-center gap-0.5 text-[9px] opacity-75">
          <span className="shrink-0">📍</span>
          <span className="truncate">{v.patient_address}</span>
        </span>
      )}
      {/* 3行目: 条件 (単独カードと同じ情報) + 「今週のみ」チップ (G3)。 */}
      {(v.patient_sex_restriction_label || v.patient_time_type || v.source === 'manual_week') && (
        <span className="flex min-w-0 items-center gap-1 text-[9px] opacity-80">
          {v.source === 'manual_week' ? (
            <WeekOnlyChip visit={v} onPromoteWeekOnly={onPromoteWeekOnly} />
          ) : null}
          {v.patient_sex_restriction_label ? (
            <span
              className="shrink-0 rounded-full px-1 py-px text-[8px] font-bold text-white"
              style={{ background: pal.bar }}
            >
              {v.patient_sex_restriction_label}
            </span>
          ) : null}
          <span className="truncate">{v.patient_time_type ?? ''}</span>
        </span>
      )}
      {/* G2: 訪問削除 (×)。ペア行は高さが小さいので常に出す (hover で可視化)。 */}
      {onDeleteVisit && <DeleteVisitButton visit={v} onDeleteVisit={onDeleteVisit} />}
    </div>
  );
}

/** DnD 有効時のみ mount する個別移動ラッパ (id 名前空間は単独カードと同じ tl-visit:)。 */
function DraggablePairMemberRow({
  v,
  onPatientClick,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  v: CourseGridVisit;
  onPatientClick?: (patientId: string) => void;
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  accompaniment?: AccompanimentBinding;
}) {
  const dragDisabled =
    v.is_pinned === true || v.status === 'cancelled' || Boolean(v.visit_group_id);
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: tlVisitDraggableId(v.id),
    disabled: dragDisabled,
    data: { kind: 'tl-visit', visitId: v.id },
  });
  return (
    <PairMemberRowView
      v={v}
      onPatientClick={onPatientClick}
      drag={{ attributes, listeners, setNodeRef, isDragging, disabled: dragDisabled }}
      onDeleteVisit={onDeleteVisit}
      onPromoteWeekOnly={onPromoteWeekOnly}
      accompaniment={accompaniment}
    />
  );
}

/** 同住所・同時刻ペアの 90分占有ボックス (上下2段に2名を並べ、大枠で囲む)。 */
function PairBox({
  item,
  laneInfo,
  onPatientClick,
  drag,
  memberDndEnabled,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  item: Extract<RenderItem, { kind: 'pair' }>;
  laneInfo?: CardLane;
  onPatientClick?: (patientId: string) => void;
  /** 2名セット移動 (PO要望: 基本は2名一緒に動かす)。 */
  drag?: DragBindings;
  /** T-6 パリティ③: 各行の ⠿ ハンドルから 1 名ずつ個別移動を解禁する。 */
  memberDndEnabled?: boolean;
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  /** 新人同行 (§7.1/§7.2)。 */
  accompaniment?: AccompanimentBinding;
}) {
  const [shake, setShake] = useState(false);
  const anyPinned = item.visits.some((v) => v.is_pinned === true);
  const anyGroup = item.visits.some((v) => Boolean(v.visit_group_id));
  const cs = Math.max(item.startMin, TL_DAY_START_MIN);
  const ce = Math.min(item.endMin, TL_DAY_END_MIN);
  if (ce <= cs) return null;
  const top = minutesToY(cs) + 1;
  const boxH = Math.max(durationToHeight(ce - cs) - 3, TL_MIN_CARD_PX * 2);
  const lanes = laneInfo?.laneCount ?? 1;
  const lane = laneInfo?.lane ?? 0;
  const laneStyle =
    lanes > 1
      ? {
          left: `calc(3px + ${(lane / lanes) * 100}% - ${(lane / lanes) * 6}px)`,
          width: `calc(${100 / lanes}% - ${6 / lanes}px)`,
          right: 'auto' as const,
        }
      : { left: '3px', right: '3px' };
  const durMin = item.endMin - item.startMin;
  return (
    <div
      ref={drag?.setNodeRef}
      data-testid={`tl-pair-${item.id}`}
      data-tl-drag={drag ? (drag.disabled ? 'disabled' : 'enabled') : undefined}
      // DnD 有効ペア = listeners/attributes、ピン含み = shake、を単一 spread に合成
      // (別プロップで書くと後勝ち上書きでドラッグが死ぬ — 64acdef の教訓)。
      {...(drag
        ? drag.disabled
          ? anyPinned || anyGroup
            ? { onPointerDown: () => setShake(true) }
            : {}
          : { ...drag.listeners, ...drag.attributes }
        : {})}
      onAnimationEnd={shake ? () => setShake(false) : undefined}
      title={
        drag?.disabled
          ? anyPinned
            ? 'ピン留めを含むため移動できません（ピンを解除してから移動）'
            : anyGroup
              ? '2名体制（ペア配置）を含むため移動できません'
              : 'キャンセル済みを含むため移動できません'
          : drag
            ? '2名セットで移動します（1名だけ動かすときは行頭の ⠿ をドラッグ）'
            : undefined
      }
      className={cn(
        'absolute z-[2] flex flex-col rounded-lg border-2 border-amber-400 bg-amber-50/40 shadow-[var(--shadow-xs)]',
        drag && !drag.disabled && 'cursor-grab touch-none active:cursor-grabbing',
        drag?.isDragging && 'opacity-40',
        shake && 'tl-shake',
      )}
      style={{ top, height: boxH, ...laneStyle }}
    >
      {/* 見出し: 占有時間のみ。住所は各カード行に出す (通常カードと同じ情報配置・PO要望)。 */}
      <div className="flex min-w-0 items-center gap-1 px-1.5 pt-0.5 text-[9px] font-bold text-amber-700">
        <span className="shrink-0">📍</span>
        <span className="truncate">同住所 {durMin}分占有</span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 px-1 pb-1">
        {item.visits.map((v) =>
          memberDndEnabled ? (
            <DraggablePairMemberRow
              key={v.id}
              v={v}
              onPatientClick={onPatientClick}
              onDeleteVisit={onDeleteVisit}
              onPromoteWeekOnly={onPromoteWeekOnly}
              accompaniment={accompaniment}
            />
          ) : (
            <PairMemberRowView
              key={v.id}
              v={v}
              onPatientClick={onPatientClick}
              onDeleteVisit={onDeleteVisit}
              onPromoteWeekOnly={onPromoteWeekOnly}
              accompaniment={accompaniment}
            />
          ),
        )}
      </div>
    </div>
  );
}

/**
 * 📝 メモチップ行 — 開始=終了 (ゼロ長) のイベントを列上部にまとめて表示する。
 *
 * カイポケ個別業務取込 (kaipoke-event-inbound-design.md E-3) のメモ系
 * (例: 00:00〜00:00「渡辺様：COCOLO3号館」) の受け皿。時間帯を持たないため
 * イベント帯にならず、従来は不可視だった。hover で全文 (title 属性)。
 */
export function MemoChipsRow({ events, colKey }: { events: EventRead[]; colKey: string }) {
  const memos = events.filter((ev) => {
    const s = parseHM(ev.start_time);
    const e = parseHM(ev.end_time);
    return s !== null && e !== null && e === s;
  });
  if (memos.length === 0) return null;
  return (
    <div
      data-testid={`tl-memos-${colKey}`}
      className="absolute inset-x-1 top-0.5 z-[3] flex flex-wrap gap-1"
    >
      {memos.map((ev) => (
        <span
          key={`memo-${ev.id}`}
          data-testid={`tl-memo-${colKey}-${ev.id}`}
          className="inline-flex max-w-full items-center gap-1 truncate rounded-full border bg-bg-base px-2 py-0.5 text-[10px] font-medium shadow-[var(--shadow-sm)]"
          style={{ color: 'var(--sched-event-ink)', borderColor: 'var(--sched-event-ln)' }}
          title={`📝 メモ: ${ev.title || ev.type}${ev.note ? `\n備考: ${ev.note}` : ''}\n（時間を持たない予定・カイポケ取込）`}
        >
          <span className="shrink-0">📝</span>
          <span className="min-w-0 truncate">{ev.title || ev.type}</span>
        </span>
      ))}
    </div>
  );
}

/**
 * イベント帯 1 本の描画 (表示専用/クリック編集/DnD 共用ビュー)。
 * drag 指定時は掴んだ帯自体が transform で動く (T-6 パリティ②)。
 */
function EventBandView({
  ev,
  col,
  onEventClick,
  drag,
}: {
  ev: EventRead;
  col: TimelineCourseColumn;
  onEventClick?: (ev: EventRead, col: TimelineCourseColumn) => void;
  drag?: ReturnType<typeof useDraggable>;
}) {
  const s = parseHM(ev.start_time);
  const e = parseHM(ev.end_time);
  if (s === null || e === null || e <= s) return null;
  // 帯上の主表示はタイトル (無ければ種別)。「イベント: 週次打合せ」の種別前置は
  // 狭い列で種別だけ見えて「イベント…」に切れる (PO指摘 2026-07-08) ため廃止。
  // 種別はツールチップ (と2行目のスペース次第) で読める。
  const evLabel = ev.title && ev.title.trim() !== '' ? ev.title : ev.type;
  const fmt = (m: number) =>
    `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
  const durMin = e - s;
  const bandH = Math.max(durationToHeight(e - s) - 3, 22);
  // 高さに応じて情報を段階表示 (訪問カードと同じ方針・PO要望 2026-07-08: 情報を増やす)。
  // 1段目 = タイトル (+低い帯は開始時刻) / 2段目 = 時刻・所要分・種別・担当 /
  // 3段目 = 備考 + カイポケ反映外バッジ。バッジは 1・2段目に置かない
  // (固定幅要素が並ぶと狭い列ではみ出して欠ける — PO指摘 2026-07-08 第2報)。
  const showTimeRow = bandH >= 40;
  const showMetaRow = bandH >= 48;
  const bandStyle = {
    top: minutesToY(s) + 1,
    height: bandH,
    background: 'var(--sched-event-bg)',
    borderColor: 'var(--sched-event-ln)',
    borderLeftColor: 'var(--sched-event-bar)',
  };
  // z-[1]: 訪問カード (z-[2]) の下に敷く。重なった時間帯でもカードのクリックを塞がない
  // (帯自体はカードに覆われていない領域でクリック可能)。ドラッグ中は最前面へ。
  const bandClass =
    'absolute left-1 right-1 z-[1] flex flex-col justify-center gap-px overflow-hidden rounded-lg border border-l-[3px] px-2 py-[3px] shadow-[var(--shadow-sm)]';
  const bandInner = (
    <>
      {/* 1行目はタイトル最優先 (PO指摘 2026-07-08: バッジ/種別に潰されて「イベント…」化)。
          2行目がある帯ではバッジ・時刻を2行目へ逃がし、タイトルに全幅を与える。 */}
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="shrink-0 text-[12px]" style={{ color: 'var(--sched-event-bar)' }}>
          👥
        </span>
        <span
          className="min-w-0 flex-1 truncate text-[11px] font-bold"
          style={{ color: 'var(--sched-event-ink)' }}
        >
          {evLabel}
        </span>
        {/* 低い帯 (2行目なし) だけ開始時刻を1行目末尾に置く (タイトル優先で truncate)。 */}
        {!showTimeRow && (
          <span
            className="tnum shrink-0 text-[9.5px] opacity-80"
            style={{ color: 'var(--sched-event-ink)' }}
          >
            {fmt(s)}〜
          </span>
        )}
      </span>
      {showTimeRow && (
        <span
          className="flex min-w-0 items-center gap-1.5 text-[9.5px] opacity-85"
          style={{ color: 'var(--sched-event-ink)' }}
        >
          <span className="tnum shrink-0 font-semibold">
            {fmt(s)}〜{fmt(e)}・{durMin}分
          </span>
          <span className="min-w-0 truncate">
            {ev.type}・担当: {col.assignedStaff?.name ?? '（未割当）'}
          </span>
        </span>
      )}
      {/* 3段目: 備考 (伸縮・truncate) + カイポケ反映外バッジ (この行にだけ置く)。
          固定幅はバッジのみなので狭い列でもはみ出さない。低い帯ではバッジ非表示
          (ツールチップに常に明記)。 */}
      {showMetaRow && (
        <span className="flex min-w-0 items-center gap-1.5">
          {ev.note ? (
            <span
              className="min-w-0 truncate text-[9px] opacity-75"
              style={{ color: 'var(--sched-event-ink)' }}
            >
              📝 {ev.note}
            </span>
          ) : null}
          <span
            className="ml-auto shrink-0 whitespace-nowrap rounded-full border bg-bg-base px-1.5 py-px text-[8.5px] font-bold"
            style={{ color: 'var(--sched-event-ink)', borderColor: 'var(--sched-event-ln)' }}
          >
            カイポケ反映外
          </span>
        </span>
      )}
    </>
  );
  // hover ツールチップは高さに関係なく全情報を読めるようにする。
  const fullTitle = [
    `${ev.type}${ev.title ? `: ${ev.title}` : ''}（${fmt(s)}〜${fmt(e)}・${durMin}分・カイポケ反映外）`,
    ev.note ? `備考: ${ev.note}` : null,
    `クリックで編集・削除${drag ? '・ドラッグで移動' : ''}`,
  ]
    .filter(Boolean)
    .join('\n');
  return onEventClick ? (
    <button
      type="button"
      ref={drag?.setNodeRef}
      data-testid={`tl-event-${col.key}-${ev.id}`}
      className={cn(
        bandClass,
        'text-left transition-shadow hover:shadow-[var(--shadow-md)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary',
        // PO要望 2026-07-08: ドラッグ可能は矢印でなく手のひらカーソルで伝える。
        drag ? 'cursor-grab touch-none active:cursor-grabbing' : 'cursor-pointer',
        drag?.isDragging && 'z-[5] cursor-grabbing opacity-90 shadow-[var(--shadow-md)]',
      )}
      style={{
        ...bandStyle,
        ...(drag?.transform
          ? { transform: `translate3d(${drag.transform.x}px, ${drag.transform.y}px, 0)` }
          : {}),
      }}
      onClick={() => onEventClick(ev, col)}
      title={fullTitle}
      aria-label={`イベント ${evLabel} を編集`}
      {...(drag ? { ...drag.listeners, ...drag.attributes } : {})}
    >
      {bandInner}
    </button>
  ) : (
    <div
      data-testid={`tl-event-${col.key}-${ev.id}`}
      // 表示専用時は下の訪問カードのクリック/hover を透過。
      className={cn(bandClass, 'pointer-events-none')}
      style={bandStyle}
      // 防御的: 現状は pointer-events-none で hover 不発だが、将来解除しても全情報が読める。
      title={fullTitle}
    >
      {bandInner}
    </div>
  );
}

/** DnD 有効時のみ mount する draggable ラッパ (read-only 描画では useDraggable を呼ばない)。 */
function DraggableEventBand({
  ev,
  col,
  onEventClick,
}: {
  ev: EventRead;
  col: TimelineCourseColumn;
  onEventClick: (ev: EventRead, col: TimelineCourseColumn) => void;
}) {
  const drag = useDraggable({ id: eventDraggableId(ev.id) });
  return <EventBandView ev={ev} col={col} onEventClick={onEventClick} drag={drag} />;
}

function TimelineColumn({
  col,
  onPatientClick,
  onFreeSlotClick,
  onEventClick,
  dndEnabled,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
}: {
  col: TimelineCourseColumn;
  onPatientClick?: (patientId: string) => void;
  onFreeSlotClick?: (col: TimelineCourseColumn, gap: FreeGap) => void;
  onEventClick?: (ev: EventRead, col: TimelineCourseColumn) => void;
  dndEnabled?: boolean;
  onDeleteVisit?: (visitId: string, patientName: string) => void;
  onPromoteWeekOnly?: (patientId: string, patientName: string) => void;
  accompaniment?: AccompanimentBinding;
}) {
  const height = timelineHeightPx();
  // 勤務外バンド: スタッフイベント以外に、コース未生成/担当なしを表す薄いハッチは出さない
  // (T-1 はデータのある勤務外のみ)。将来 shift 情報で拡張。
  const rows: number[] = [];
  for (let m = TL_DAY_START_MIN; m < TL_DAY_END_MIN; m += 30) rows.push(m);

  // 同住所・同時刻の2名は 90分占有ペアに束ね、それ以外は単独として描画単位化。
  const items = buildRenderItems(col.visits);
  // 描画単位 (単独 or ペアボックス) 同士で時間帯が重なる場合のみ左右レーンに分割 (MED-1)。
  const lanes = assignLanes(
    items.map((it) => ({ id: it.id, startMin: it.startMin, endMin: it.endMin })),
  );

  return (
    <div
      className="relative border-l border-[var(--border-subtle)]"
      style={{ flex: 1, minWidth: COL_MIN_W }}
      data-testid={`tl-col-${col.key}`}
    >
      {/* T-2 ②-b: DnD 有効時のみ列全体を droppable にする (isOver ハイライト付き)。 */}
      {dndEnabled ? <ColumnDropLayer colKey={col.key} /> : null}

      {rows.map((m) => (
        <div
          key={m}
          className={cn(
            'absolute left-0 right-0',
            m % 60 === 0
              ? 'border-t border-[var(--border-default)]'
              : 'border-t border-dashed border-[var(--border-subtle)]',
          )}
          style={{ top: minutesToY(m), height: TL_ROW_PX }}
        />
      ))}

      {/* 空き時間帯 (≥60分・remaining>0 のときだけ Panel が gap を渡す)。
          T-2 ②-a: ハンドラがあるときはクリック可能な「＋ここに追加」ボタンになる。 */}
      {col.freeGaps.map((g) => {
        const top = minutesToY(g.startMin) + 2;
        const h = durationToHeight(g.endMin - g.startMin) - 5;
        if (h < 14) return null;
        const gapClass =
          'absolute left-1 right-1 flex items-center justify-center gap-1.5 rounded-lg border border-dashed border-transparent text-[11px] font-bold text-brand-primary opacity-0 transition-opacity hover:border-brand-primary hover:bg-[color-mix(in_srgb,var(--brand-primary)_5%,transparent)] hover:opacity-100';
        const inner = (
          <>
            <span className="grid h-[18px] w-[18px] place-items-center rounded-full bg-brand-primary text-[13px] leading-none text-white">
              ＋
            </span>
            {onFreeSlotClick ? <span>ここに追加</span> : null}
            <span className="tnum text-[9.5px] font-semibold text-text-muted">
              {g.endMin - g.startMin}分空き
            </span>
          </>
        );
        return onFreeSlotClick ? (
          <button
            key={`gap-${g.startMin}`}
            type="button"
            data-testid={`tl-gap-${col.key}-${g.startMin}`}
            className={cn(
              gapClass,
              'cursor-pointer focus-visible:border-brand-primary focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand-primary',
            )}
            style={{ top, height: h }}
            onClick={() => onFreeSlotClick(col, g)}
            aria-label={`空き時間 ${g.endMin - g.startMin}分に追加`}
          >
            {inner}
          </button>
        ) : (
          <div
            key={`gap-${g.startMin}`}
            data-testid={`tl-gap-${col.key}-${g.startMin}`}
            className={gapClass}
            style={{ top, height: h }}
          >
            {inner}
          </div>
        );
      })}

      {/* 会議・イベント帯 (担当スタッフの列内・藤色・カイポケ反映外)。クリックで編集/削除。
          T-6 パリティ②: DnD 有効時は draggable (15分スナップ移動・掴んだ帯自体が動く)。 */}
      {col.staffEvents.map((ev) =>
        dndEnabled && onEventClick ? (
          <DraggableEventBand key={`ev-${ev.id}`} ev={ev} col={col} onEventClick={onEventClick} />
        ) : (
          <EventBandView key={`ev-${ev.id}`} ev={ev} col={col} onEventClick={onEventClick} />
        ),
      )}

      {/* 📝 メモチップ (開始=終了のゼロ長イベント・カイポケ個別業務取込のメモ系)。
          帯 (EventBandView) は e<=s で描かれないため、列上部にチップで表示する。
          割当への影響はゼロ (Layer3 の重複判定はゼロ長と重ならない)。表示専用。 */}
      <MemoChipsRow events={col.staffEvents} colKey={col.key} />

      {/* 訪問カード (単独 or 同住所90分ペアボックス)。DnD 有効時は単独カードが draggable。 */}
      {items.map((it) =>
        it.kind === 'pair' ? (
          dndEnabled ? (
            <DraggablePairBox
              key={it.id}
              item={it}
              laneInfo={lanes.get(it.id)}
              onPatientClick={onPatientClick}
              onDeleteVisit={onDeleteVisit}
              onPromoteWeekOnly={onPromoteWeekOnly}
              accompaniment={accompaniment}
            />
          ) : (
            <PairBox
              key={it.id}
              item={it}
              laneInfo={lanes.get(it.id)}
              onPatientClick={onPatientClick}
              onDeleteVisit={onDeleteVisit}
              onPromoteWeekOnly={onPromoteWeekOnly}
              accompaniment={accompaniment}
            />
          )
        ) : dndEnabled ? (
          <DraggableVisitCard
            key={it.id}
            visit={it.v}
            laneInfo={lanes.get(it.id)}
            onClick={onPatientClick ? () => onPatientClick(it.v.patient_id) : undefined}
            onDeleteVisit={onDeleteVisit}
            onPromoteWeekOnly={onPromoteWeekOnly}
            accompaniment={accompaniment}
          />
        ) : (
          <VisitCard
            key={it.id}
            visit={it.v}
            laneInfo={lanes.get(it.id)}
            onClick={onPatientClick ? () => onPatientClick(it.v.patient_id) : undefined}
            onDeleteVisit={onDeleteVisit}
            onPromoteWeekOnly={onPromoteWeekOnly}
            accompaniment={accompaniment}
          />
        ),
      )}

      <div style={{ height }} />
    </div>
  );
}

export function TimelineDayBoard({
  columns,
  staffFrames = [],
  weekdayLabel,
  onPatientClick,
  nowMinutes,
  onFreeSlotClick,
  onEventClick,
  dndEnabled,
  onChangeAssignedStaff,
  isStaffMutating,
  onDeleteVisit,
  onPromoteWeekOnly,
  accompaniment,
  accompanimentWeekday,
}: TimelineDayBoardProps) {
  const accActive = accompaniment?.active === true;
  const height = timelineHeightPx();
  const hours: number[] = [];
  for (let m = TL_DAY_START_MIN; m <= TL_DAY_END_MIN; m += 60) hours.push(m);

  if (columns.length === 0 && staffFrames.length === 0) {
    return (
      <div className="rounded-lg border border-border-default bg-bg-muted p-4 text-sm text-text-muted">
        {weekdayLabel}曜日の表示対象コースがありません。
      </div>
    );
  }

  const showNow =
    nowMinutes != null && nowMinutes >= TL_DAY_START_MIN && nowMinutes <= TL_DAY_END_MIN;

  return (
    <div
      // lg 以上は親 (course-day-timeline-view) の高さいっぱいで盤面内スクロール。
      // 列ヘッダ (sticky top-0) はこのスクロールコンテナ基準で固定される (PO要望)。
      className="overflow-auto rounded-lg border border-border-default bg-bg-base lg:h-full"
      data-testid="timeline-day-board"
    >
      {/* 列ヘッダ (担当スタッフ主・コース記号従) */}
      <div className="sticky top-0 z-[6] flex min-w-fit border-b border-border-default bg-bg-muted">
        <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} />
        {columns.map((col) => {
          const gk = genderKey(col.assignedStaff?.sex);
          const pal = genderPalette(col.assignedStaff?.sex);
          const staffName = col.assignedStaff?.name ?? '（未割当）';
          const full = col.capacity.filled >= col.capacity.max;
          // 同行モード: 列ヘッダ = その日のこのコース全体を選択トグル。
          const accCourseId = col.course?.id ?? null;
          const headerSelectable =
            accActive && !!accCourseId && typeof accompanimentWeekday === 'number';
          const headerSelected = accActive && accompaniment!.isCourseSelected(accCourseId);
          const courseBadge =
            accompaniment && !accompaniment.active
              ? accompaniment.courseBadgeName(accCourseId)
              : null;
          return (
            <div
              key={col.key}
              role={headerSelectable ? 'button' : undefined}
              tabIndex={headerSelectable ? 0 : undefined}
              onClick={
                headerSelectable
                  ? () =>
                      accompaniment!.toggleCourse(
                        accCourseId,
                        col.template.id,
                        accompanimentWeekday as number,
                      )
                  : undefined
              }
              onKeyDown={
                headerSelectable
                  ? (ev) => {
                      if (ev.key === 'Enter' || ev.key === ' ') {
                        ev.preventDefault();
                        accompaniment!.toggleCourse(
                          accCourseId,
                          col.template.id,
                          accompanimentWeekday as number,
                        );
                      }
                    }
                  : undefined
              }
              data-testid={accActive ? `tl-course-header-${col.key}` : undefined}
              data-accompaniment-selected={headerSelected ? 'true' : undefined}
              className={cn(
                'flex items-center gap-2 border-l border-[var(--border-subtle)] px-2.5 py-2',
                headerSelectable &&
                  'cursor-pointer outline-none hover:bg-brand-primary-light focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-primary',
                headerSelected && 'bg-brand-primary-light ring-2 ring-inset ring-brand-primary',
              )}
              style={{ flex: 1, minWidth: COL_MIN_W }}
            >
              {accActive && (
                <span
                  className={cn(
                    'grid h-4 w-4 shrink-0 place-items-center rounded border text-[9px] font-bold',
                    headerSelected
                      ? 'border-brand-primary bg-brand-primary text-white'
                      : 'border-border-default text-transparent',
                  )}
                  aria-hidden="true"
                >
                  ✓
                </span>
              )}
              <span
                className="grid h-7 w-7 shrink-0 place-items-center rounded-full border-[1.5px] text-[11px] font-bold"
                style={{
                  background: pal.bg,
                  borderColor: pal.bar,
                  color: pal.ink,
                }}
                data-gender={gk}
              >
                {staffName[0]}
              </span>
              <div className="min-w-0 flex-1">
                {/* G1: 担当スタッフ変更 (テーブルのヘッダ dropdown と同機能)。
                    onChangeAssignedStaff 未指定 (= read-only ロール) は従来のテキスト表示。
                    列ヘッダは droppable/draggable ではないため stopPropagation 不要。 */}
                {/* コース丸ごと同行 (§7.2) はスタッフ名の右隣に 👥アイコンのみで併記する
                    (別行に積むとこの列だけヘッダが縦に伸び、他コースと高さがズレるため・PO要望)。
                    日タイムラインは select と同じ行のため、名前を出すと select が潰れる →
                    アイコンのみに縮小し全文は title に保持 (PO指摘 2026-07-12)。 */}
                <div className="flex min-w-0 items-center gap-1">
                  {onChangeAssignedStaff ? (
                    <select
                      value={col.course?.assigned_staff_id ?? ''}
                      onChange={(e) => {
                        const v = e.target.value;
                        onChangeAssignedStaff(col, v === '' ? null : v);
                      }}
                      disabled={isStaffMutating === true}
                      data-testid={`tl-staff-select-${col.key}`}
                      aria-label={`${col.officeName}${col.template.label} コースの担当スタッフ`}
                      title={
                        col.course
                          ? '担当スタッフを変更します'
                          : '「週を生成」を実行するとコースが作成され担当を変更できます'
                      }
                      className={cn(
                        'min-w-0 flex-1 truncate rounded border bg-bg-base px-1 py-px text-[12px] font-bold disabled:cursor-not-allowed disabled:opacity-60',
                        col.assignedStaffMissing
                          ? 'border-warning text-warning'
                          : 'border-border-default text-text-primary',
                      )}
                    >
                      <option value="">（未割当）</option>
                      {/* PO 2026-07-09: 担当が削除済み (stale ID) の場合、選択肢に無いため
                          （担当不在）を現在値として表示する（再割当を促す）。 */}
                      {col.assignedStaffMissing && col.course?.assigned_staff_id ? (
                        <option value={col.course.assigned_staff_id}>（担当不在）</option>
                      ) : null}
                      {col.staffOptions.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div
                      className={cn(
                        'min-w-0 flex-1 truncate text-[12.5px] font-bold',
                        col.assignedStaffMissing ? 'text-warning' : 'text-text-primary',
                      )}
                    >
                      {col.assignedStaffMissing ? '（担当不在）' : staffName}
                    </div>
                  )}
                  {courseBadge && (
                    <span
                      className="inline-flex shrink-0 items-center rounded-full bg-info-bg px-1 text-[10px] font-bold text-info"
                      data-testid={`tl-course-accompaniment-${col.key}`}
                      title={`同行: ${courseBadge}（新人）`}
                      aria-label={`同行: ${courseBadge}（新人）`}
                    >
                      👥
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1.5 text-[9.5px] text-text-muted">
                  <span
                    className="rounded px-1.5 py-px text-[9px] font-extrabold text-white"
                    style={{ background: 'var(--brand-primary)' }}
                  >
                    {col.officeName}
                    {col.template.label}
                  </span>
                  <span className={cn('tnum font-bold', full && 'text-warning')}>
                    {col.capacity.filled}/{col.capacity.max}件
                  </span>
                </div>
              </div>
            </div>
          );
        })}
        {/* スタッフ枠ヘッダ (コース無し・イベントありのスタッフ)。緑=イベントの視覚言語。 */}
        {staffFrames.map((f) => (
          <div
            key={`frame-h-${f.staff.id}`}
            className="flex items-center gap-1.5 border-l border-border-subtle px-2 py-1.5"
            style={{ flex: 1, minWidth: COL_MIN_W }}
            data-testid={`tl-staff-frame-header-${f.staff.id}`}
          >
            <span
              className="grid h-7 w-7 shrink-0 place-items-center rounded-full border-[1.5px] text-[11px] font-bold"
              style={{
                background: 'var(--sched-event-bg)',
                borderColor: 'var(--sched-event-bar)',
                color: 'var(--sched-event-ink)',
              }}
              aria-hidden="true"
            >
              {f.staff.name[0]}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12.5px] font-bold text-text-primary">
                {f.staff.name}
              </div>
              <div className="flex items-center gap-1.5 text-[9.5px] text-text-muted">
                <span
                  className="rounded px-1.5 py-px text-[9px] font-extrabold"
                  style={{
                    background: 'var(--sched-event-bg)',
                    color: 'var(--sched-event-ink)',
                  }}
                >
                  イベント
                </span>
                <span className="tnum font-bold">{f.events.length}件</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 本体 (時間軸レール + コース列 + イベント帯 + 現在線) */}
      <div className="relative flex min-w-fit">
        <div style={{ width: TIME_RAIL_W, flex: `0 0 ${TIME_RAIL_W}px` }} className="relative">
          {hours.map((m) => (
            <span
              key={m}
              className="tnum absolute right-2 -translate-y-[7px] text-[10px] text-text-muted"
              style={{ top: minutesToY(m) }}
            >
              {String(Math.floor(m / 60)).padStart(2, '0')}:{String(m % 60).padStart(2, '0')}
            </span>
          ))}
          <div style={{ height }} />
        </div>

        {columns.map((col) => (
          <TimelineColumn
            key={col.key}
            col={col}
            onPatientClick={onPatientClick}
            onFreeSlotClick={onFreeSlotClick}
            onEventClick={onEventClick}
            dndEnabled={dndEnabled}
            onDeleteVisit={onDeleteVisit}
            onPromoteWeekOnly={onPromoteWeekOnly}
            accompaniment={accompaniment}
          />
        ))}

        {/* スタッフ枠本体: イベント帯 + 📝メモのみの読み取り専用列。 */}
        {staffFrames.map((f) => {
          const frameCol = {
            key: `staff-frame-${f.staff.id}`,
            assignedStaff: f.staff,
          } as unknown as TimelineCourseColumn;
          return (
            <div
              key={`frame-b-${f.staff.id}`}
              className="relative border-l border-border-subtle"
              style={{ flex: 1, minWidth: COL_MIN_W, height }}
              data-testid={`tl-staff-frame-${f.staff.id}`}
            >
              {f.events.map((ev) => (
                <EventBandView key={`fev-${ev.id}`} ev={ev} col={frameCol} />
              ))}
              <MemoChipsRow events={f.events} colKey={`staff-frame-${f.staff.id}`} />
            </div>
          );
        })}

        {/* 現在時刻ライン */}
        {showNow && nowMinutes != null && (
          <div
            data-testid="timeline-now-line"
            className="pointer-events-none absolute left-0 right-0 z-[5] border-t-2"
            style={{ top: minutesToY(nowMinutes), borderColor: 'var(--sched-now)' }}
          >
            <span
              className="tnum absolute left-0.5 -top-[9px] rounded px-1.5 py-px text-[9px] font-bold text-white"
              style={{ background: 'var(--sched-now)' }}
            >
              {String(Math.floor(nowMinutes / 60)).padStart(2, '0')}:
              {String(nowMinutes % 60).padStart(2, '0')}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
