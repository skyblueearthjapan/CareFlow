/**
 * courseDnd — 職員スケジュール盤面のコース/訪問 DnD 共有ユーティリティ (週空間 A1/A2)。
 *
 * 旧 WeekCoursePalette.tsx (コースの表) から独立させたもの。パレットは
 * PO 判断で撤去 (2026-08-21)。未割当コースの置き場は盤面の「（担当なし）」行、
 * 担当解除は帯の「×」または「（担当なし）」行へのドラッグで行う。
 */

/**
 * 「（担当なし）」行のキー (盤面 = StaffWeekBoard / タイムライン =
 * StaffTimelineView.UNASSIGNED_ROW_KEY の**単一ソース**)。
 * 盤面とタイムラインで行キーが食い違うとゴースト/DnD の宛先がズレるため、
 * どちらもここを import する (週空間 Phase E・FE-C)。
 */
export const UNASSIGNED_ROW_KEY = '__unassigned__';

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
