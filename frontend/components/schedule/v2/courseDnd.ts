/**
 * courseDnd — 職員スケジュール盤面のコース/訪問 DnD 共有ユーティリティ (週空間 A1/A2)。
 *
 * 旧 WeekCoursePalette.tsx (コースの表) から独立させたもの。パレットは
 * PO 判断で撤去 (2026-08-21)。未割当コースの置き場は盤面の「（担当なし）」行、
 * 担当解除は帯の「×」または「（担当なし）」行へのドラッグで行う。
 *
 * 2026-09-08 (`docs/plans/dnd-all-views-design-2026-09-08.md` §2-1): ⭐/プールカードを
 * 全ビューへ落とせるようにするため、ここが **droppable 名前空間と共通リゾルバ**の
 * 単一ソースになった (盤面の `handleDragEnd` は先頭で 1 回 `resolveDropTarget` を
 * 呼ぶだけでよい)。
 */
import { snapYOffsetToMinutes, TL_DAY_START_MIN } from '@/lib/scheduling/timeline';

/**
 * 「（担当なし）」行のキー (盤面 = StaffWeekBoard / タイムライン =
 * StaffTimelineView.UNASSIGNED_ROW_KEY の**単一ソース**)。
 * 盤面とタイムラインで行キーが食い違うとゴースト/DnD の宛先がズレるため、
 * どちらもここを import する (週空間 Phase E・FE-C)。
 */
export const UNASSIGNED_ROW_KEY = '__unassigned__';

/**
 * ⭐特別訪問週間チケットの dnd-kit draggable id 接頭辞
 * (`docs/plans/special-ticket-dnd-design-2026-09-08.md` §2)。
 * プール患者 (`pool-patient:`) / 訪問 (`tl-visit:`) と衝突しない専用の名前空間。
 * 盤面 (CourseDayTablePanel) とプール側カード (SpecialTicketPlacePanel) の
 * **単一ソース** なのでここに置く (UNASSIGNED_ROW_KEY と同じ理由)。
 */
export const SPECIAL_TICKET_DND_PREFIX = 'special-ticket:';

/** markId → draggable id (`special-ticket:{markId}`)。 */
export function buildSpecialTicketDraggableId(markId: string): string {
  return `${SPECIAL_TICKET_DND_PREFIX}${markId}`;
}

/** draggable id → markId。⭐チケット以外の id は null。 */
export function parseSpecialTicketDraggableId(id: string): string | null {
  if (!id.startsWith(SPECIAL_TICKET_DND_PREFIX)) return null;
  const markId = id.slice(SPECIAL_TICKET_DND_PREFIX.length);
  return markId.length > 0 ? markId : null;
}

/** 0=月..6=日 (⭐/確認モーダルは日曜まで持つ)。盤面の 6 要素 (月〜土) とは別物。 */
const SPECIAL_WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/**
 * ⭐チケット / 配置の確認モーダルの曜日ラベル (0=月..6=日)。範囲外は '?'。
 * トーストやカードから曜日が消えないようにするための**単一ソース**
 * (`SpecialTicketPlacePanel` は互換のためここを re-export する)。
 */
export function specialTicketWeekdayLabel(weekday: number): string {
  return SPECIAL_WEEKDAY_LABELS[weekday] ?? '?';
}

// ───────────────────────────────────────────────────────────────────────────
// droppable 名前空間 + 共通リゾルバ (設計 §2-1)
// ───────────────────────────────────────────────────────────────────────────

/**
 * 職員スケジュール (`StaffWeekBoard`) のセル droppable id 接頭辞。
 * `sw-cell:{rowKey}:{weekday}` — rowKey = staffId または `UNASSIGNED_ROW_KEY`。
 * 時間軸を持たない盤面なので、ここへ落ちたドロップは **時刻なし** で解決され、
 * 呼び出し側が「配置の確認」モーダルで時刻を決める (設計 §2-2)。
 */
export const SW_CELL_DND_PREFIX = 'sw-cell:';

/**
 * 日タイムラインの列 droppable id 接頭辞 (`tl-col:{templateId}:{weekday}`)。
 * id を作るのは `TimelineDayBoard.tlColDroppableId` だが、リゾルバのために
 * courseDnd → TimelineDayBoard の import を張ると DnD の土台が重いコンポーネントに
 * 依存してしまうため、接頭辞だけ写している (値は同一)。
 */
const TL_COL_DND_PREFIX = 'tl-col:';

/** rowKey + weekday → 職員スケジュールのセル droppable id。 */
export function buildStaffWeekCellDroppableId(rowKey: string, weekday: number): string {
  return `${SW_CELL_DND_PREFIX}${rowKey}:${weekday}`;
}

/**
 * `sw-cell:` id → `{ rowKey, weekday }`。それ以外の id / 壊れた id は null。
 * rowKey (staffId = UUID) に `:` は含まれないが、末尾の weekday から切ることで
 * 将来 rowKey が複合キーになっても壊れないようにする。
 * 職員スケジュールの列は月〜土の 6 本なので weekday は 0..5 に限る。
 */
export function parseStaffWeekCellDroppableId(
  id: string,
): { rowKey: string; weekday: number } | null {
  if (!id.startsWith(SW_CELL_DND_PREFIX)) return null;
  const rest = id.slice(SW_CELL_DND_PREFIX.length);
  const sep = rest.lastIndexOf(':');
  if (sep <= 0 || sep >= rest.length - 1) return null;
  const rowKey = rest.slice(0, sep);
  const weekday = Number(rest.slice(sep + 1));
  if (!Number.isInteger(weekday) || weekday < 0 || weekday > 5) return null;
  return { rowKey, weekday };
}

/** リゾルバに渡す矩形 (dnd-kit の `ClientRect` の必要な部分だけ)。 */
export interface DropRectLike {
  top: number;
}

/** リゾルバの調整値 (既定は日タイムラインと同じ 15 分スナップ / 9:00 起点)。 */
export interface ResolveDropTargetContext {
  snapMin?: number;
  dayStartMin?: number;
}

/** ドロップ先の正規形。`time === null` = 時間軸のないビュー (= 確認モーダル行き)。 */
export interface ResolvedDropTarget {
  kind: 'tl-col' | 'sw-cell';
  weekday: number;
  /** コースが確定している場合のみ (職員スケジュールのセルは null)。 */
  courseTemplateId: string | null;
  /** 職員スケジュールの行スタッフ。「（担当なし）」行と時間軸ビューは null。 */
  staffId: string | null;
  /** "HH:MM"。時間軸のないビューでは null。 */
  time: string | null;
}

/** 分 → "HH:MM"。 */
function formatHM(totalMinutes: number): string {
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/**
 * ドロップ先 id (+ ドラッグ中カードと列の矩形) を 1 つの形に解決する (設計 §2-1)。
 *
 * - `sw-cell:` → 時刻なし・行スタッフあり (`UNASSIGNED_ROW_KEY` は staffId=null)。
 * - `tl-col:`  → 既存のスナップ計算 (`snapYOffsetToMinutes`) で時刻あり。矩形が
 *   取れないときは「列の上で離せていない」とみなし null を返す (従来の案内と同じ)。
 * - それ以外の droppable (プール等) は null (呼び出し側が個別に処理する)。
 *
 * id は**画面に描かれている droppable が自分で作ったもの**しか来ない前提で解く
 * (template / staff の実在チェックは呼び出し側の候補作成が担う)。
 */
export function resolveDropTarget(
  overId: string,
  activeRect: DropRectLike | null | undefined,
  overRect: DropRectLike | null | undefined,
  ctx: ResolveDropTargetContext = {},
): ResolvedDropTarget | null {
  const cell = parseStaffWeekCellDroppableId(overId);
  if (cell) {
    return {
      kind: 'sw-cell',
      weekday: cell.weekday,
      courseTemplateId: null,
      staffId: cell.rowKey === UNASSIGNED_ROW_KEY ? null : cell.rowKey,
      time: null,
    };
  }
  if (overId.startsWith(TL_COL_DND_PREFIX)) {
    const rest = overId.slice(TL_COL_DND_PREFIX.length);
    const sep = rest.lastIndexOf(':');
    if (sep <= 0 || sep >= rest.length - 1) return null;
    const courseTemplateId = rest.slice(0, sep);
    const weekday = Number(rest.slice(sep + 1));
    if (!Number.isInteger(weekday) || weekday < 0 || weekday > 6) return null;
    if (activeRect == null || overRect == null) return null;
    const startMin = snapYOffsetToMinutes(
      activeRect.top - overRect.top,
      ctx.snapMin ?? 15,
      ctx.dayStartMin ?? TL_DAY_START_MIN,
    );
    return {
      kind: 'tl-col',
      weekday,
      courseTemplateId,
      staffId: null,
      time: formatHM(startMin),
    };
  }
  return null;
}

/** コース帯の DnD payload MIME。 */
export const COURSE_DND_MIME = 'application/x-rakusuke-course';
/** 訪問 1 件 (患者個別) のドラッグ用 MIME (週空間 A2)。 */
export const VISIT_DND_MIME = 'application/x-rakusuke-visit';

export interface CourseDragPayload {
  courseId: string;
  weekday: number;
  /**
   * セルの帯から掴んだ場合の行スタッフ。担当解除ドロップ時、コース行の担当
   * (assigned_staff_id) が空でも訪問 primary ベースで帯が出ているケース
   * (取込由来など) を個別解除でフォールバックするために使う。
   */
  fromStaffId?: string;
}

/** 訪問 1 件のドラッグ payload (週空間 A2: 患者個別の貼り替え)。 */
export interface VisitDragPayload {
  visitId: string;
  weekday: number;
}

/**
 * ドラッグ中のハイライト共有用: コース/訪問どちらのドラッグかを
 * optional キーで区別する (weekday は両者共通 = ドロップ可能列の判定に使う)。
 */
export interface BoardDragState {
  courseId?: string;
  visitId?: string;
  weekday: number;
}

/** dataTransfer からコース payload を安全に読む。 */
export function readCourseDragPayload(dt: DataTransfer | null): CourseDragPayload | null {
  if (!dt) return null;
  try {
    const raw = dt.getData(COURSE_DND_MIME);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CourseDragPayload>;
    if (typeof parsed.courseId !== 'string' || typeof parsed.weekday !== 'number') return null;
    return {
      courseId: parsed.courseId,
      weekday: parsed.weekday,
      ...(typeof parsed.fromStaffId === 'string' ? { fromStaffId: parsed.fromStaffId } : {}),
    };
  } catch {
    return null;
  }
}

/** dataTransfer から訪問 payload を安全に読む。 */
export function readVisitDragPayload(dt: DataTransfer | null): VisitDragPayload | null {
  if (!dt) return null;
  try {
    const raw = dt.getData(VISIT_DND_MIME);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<VisitDragPayload>;
    if (typeof parsed.visitId !== 'string' || typeof parsed.weekday !== 'number') return null;
    return { visitId: parsed.visitId, weekday: parsed.weekday };
  } catch {
    return null;
  }
}

/**
 * ドラッグ中に掴んで見えるゴーストカードを設定する。
 * ブラウザ既定のスナップショット (小さく半透明で貧弱・PO指摘 2026-08-21) を、
 * ブランド色の縁取り + 影つきカードに差し替える。
 * jsdom (テスト) には setDragImage が無いためガードして no-op。
 */
export function applyCourseDragImage(dt: DataTransfer, label: string, sub?: string): void {
  if (typeof document === 'undefined' || typeof dt.setDragImage !== 'function') return;
  const ghost = document.createElement('div');
  ghost.setAttribute('data-testid', 'course-drag-ghost');
  // らく助ブランド色 #e15a7f (ハイブリッド配色・2026-07-10 リブランディング)。
  ghost.style.cssText = [
    'position:fixed',
    'top:-200px',
    'left:-200px',
    'z-index:9999',
    'pointer-events:none',
    'padding:8px 14px',
    'border-radius:10px',
    'background:#ffffff',
    'border:1.5px solid #e15a7f',
    'border-left:6px solid #e15a7f',
    'box-shadow:0 10px 28px rgba(0,0,0,0.22)',
    'font-family:inherit',
    'max-width:260px',
    'white-space:nowrap',
  ].join(';');
  const title = document.createElement('div');
  title.textContent = `⠿ ${label}`;
  title.style.cssText = 'font-size:14px;font-weight:700;color:#1f2937;';
  ghost.appendChild(title);
  if (sub) {
    const subEl = document.createElement('div');
    subEl.textContent = sub;
    subEl.style.cssText = 'font-size:11px;color:#6b7280;margin-top:2px;';
    ghost.appendChild(subEl);
  }
  document.body.appendChild(ghost);
  dt.setDragImage(ghost, 18, 18);
  // setDragImage はこの時点でスナップショット済みのため次 tick で破棄してよい。
  window.setTimeout(() => ghost.remove(), 0);
}
