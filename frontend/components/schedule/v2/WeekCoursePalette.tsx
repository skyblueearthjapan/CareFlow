'use client';

/**
 * WeekCoursePalette — 「コースの表」(週空間 A1・weekly-space-design.md §4-1)。
 *
 * 職員スケジュール盤面の下に置く、この週のコース一覧パレット。
 * 未割当コースをカードとしてドラッグし、上のスタッフ×曜日セルへ
 * 「ペタッと貼る」(= 今週のコース担当設定・PATCH /courses/{id})。
 * 割当済カードも掴んで別スタッフへ付け替え可能。パレット自体は
 * ドロップ先にもなり、割当済コースを落とすと担当解除 (未割当へ戻す)。
 *
 * マスタ (PFV / course_templates) には一切触れない — 週空間の憲法 §3。
 */
import * as React from 'react';

import { cn } from '@/lib/utils';

/** DnD payload の MIME。盤面 (StaffWeekBoard) と共有する。 */
export const COURSE_DND_MIME = 'application/x-rakusuke-course';

export interface CourseDragPayload {
  courseId: string;
  weekday: number;
}

/** dataTransfer から payload を安全に読む (盤面/パレット共用)。 */
export function readCourseDragPayload(dt: DataTransfer | null): CourseDragPayload | null {
  if (!dt) return null;
  try {
    const raw = dt.getData(COURSE_DND_MIME);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CourseDragPayload>;
    if (typeof parsed.courseId !== 'string' || typeof parsed.weekday !== 'number') return null;
    return { courseId: parsed.courseId, weekday: parsed.weekday };
  } catch {
    return null;
  }
}

/**
 * ドラッグ中に掴んで見えるゴーストカードを設定する (盤面/パレット共用)。
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

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土'] as const;

export interface PaletteCourse {
  id: string;
  weekday: number; // 0=Mon..5=Sat
  /** 例: "稲毛A" (拠点名 + コースコード)。 */
  label: string;
  assignedStaffId: string | null;
  assignedStaffName: string | null;
  visitCount: number;
  totalMinutes: number;
  /** 例: "09:00〜15:30"。訪問が無ければ null。 */
  timeRange: string | null;
}

export interface WeekCoursePaletteProps {
  courses: PaletteCourse[];
  canEdit: boolean;
  /** ドラッグ開始/終了の通知 (盤面のドロップ先ハイライト用)。 */
  onDragChange?: (drag: CourseDragPayload | null) => void;
  /** パレットへのドロップ = 担当解除 (割当済コースのみ意味を持つ)。 */
  onUnassignDrop?: (courseId: string) => void;
  /** ドラッグ中 payload (掴んでいるカードの淡色化 + 戻し先案内の強調)。 */
  activeDrag?: CourseDragPayload | null;
}

export function WeekCoursePalette({
  courses,
  canEdit,
  onDragChange,
  onUnassignDrop,
  activeDrag,
}: WeekCoursePaletteProps) {
  const [dropHover, setDropHover] = React.useState(false);

  const byWeekday = React.useMemo(() => {
    const m = new Map<number, PaletteCourse[]>();
    for (const c of courses) {
      if (c.weekday < 0 || c.weekday > 5) continue;
      const arr = m.get(c.weekday) ?? [];
      arr.push(c);
      m.set(c.weekday, arr);
    }
    // 未割当を先に、次にラベル順。
    for (const arr of m.values()) {
      arr.sort((a, b) => {
        const ua = a.assignedStaffId ? 1 : 0;
        const ub = b.assignedStaffId ? 1 : 0;
        if (ua !== ub) return ua - ub;
        return a.label.localeCompare(b.label, 'ja');
      });
    }
    return m;
  }, [courses]);

  const unassignedCount = React.useMemo(
    () => courses.filter((c) => !c.assignedStaffId).length,
    [courses],
  );

  return (
    <section
      aria-label="コースの表（この週のコース一覧）"
      data-testid="week-course-palette"
      className={cn(
        'rounded-lg border border-border-default bg-bg-base p-2 transition-shadow',
        // ドラッグ中はパレット全体を「戻し先」として穏やかに示し、
        // 実際に上を通ったら強調する (PO指摘 2026-08-21: 表現が貧弱)。
        activeDrag && !dropHover && 'ring-1 ring-brand-primary/30',
        dropHover && 'bg-brand-primary/5 ring-2 ring-brand-primary/70',
      )}
      onDragEnter={(e) => {
        if (!canEdit || !onUnassignDrop) return;
        if (!e.dataTransfer.types.includes(COURSE_DND_MIME)) return;
        e.preventDefault();
        setDropHover(true);
      }}
      onDragOver={(e) => {
        if (!canEdit || !onUnassignDrop) return;
        if (!e.dataTransfer.types.includes(COURSE_DND_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setDropHover(true);
      }}
      onDragLeave={(e) => {
        // 子要素間の移動で消えないよう、パレットの外へ出たときだけ解除する。
        const related = e.relatedTarget as Node | null;
        if (related && e.currentTarget.contains(related)) return;
        setDropHover(false);
      }}
      onDrop={(e) => {
        setDropHover(false);
        if (!canEdit || !onUnassignDrop) return;
        const payload = readCourseDragPayload(e.dataTransfer);
        if (!payload) return;
        e.preventDefault();
        onUnassignDrop(payload.courseId);
      }}
    >
      {/* ドラッグ中の大きな戻し先ゾーン (PO指摘 2026-08-21: 戻し先を明確に)。
          ゾーン自体に個別ハンドラは不要 — セクション全体が drop を受ける。 */}
      {canEdit && onUnassignDrop && activeDrag ? (
        <div
          className={cn(
            'mb-2 rounded-md border-2 border-dashed px-2 py-2.5 text-center text-[12px] font-bold transition-colors',
            dropHover
              ? 'border-brand-primary bg-brand-primary/10 text-brand-primary'
              : 'border-brand-primary/50 bg-brand-primary/5 text-brand-primary/80',
          )}
          data-testid="palette-unassign-dropzone"
        >
          ⤵ ここにドロップで担当解除（未割当へ戻す・今週のみ）
        </div>
      ) : null}
      <div className="mb-1.5 flex items-center gap-2 px-1">
        <h3 className="text-xs font-bold text-text-primary">コースの表</h3>
        <span className="text-[11px] text-text-muted">
          {unassignedCount > 0
            ? `未割当 ${unassignedCount} 件 — カードを上のスタッフのセルへドラッグして貼り付け`
            : 'すべてのコースに担当が付いています'}
        </span>
        {canEdit && onUnassignDrop ? (
          <span
            className={cn(
              'ml-auto text-[10px] transition-colors',
              activeDrag ? 'font-bold text-brand-primary' : 'text-text-muted/80',
            )}
          >
            {activeDrag ? '⤵ ここへ戻すと担当解除（今週のみ）' : 'ここへ戻すと担当解除（今週のみ・毎週の型には影響しません）'}
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-6">
        {WEEKDAY_LABELS.map((label, wd) => {
          const list = byWeekday.get(wd) ?? [];
          return (
            <div key={label} className="min-w-0 rounded border border-border-subtle bg-bg-muted/40 p-1.5">
              <div className="mb-1 text-[10px] font-medium text-text-secondary">{label}曜</div>
              {list.length === 0 ? (
                <div className="py-1 text-center text-[10px] text-text-muted">—</div>
              ) : (
                <ul className="space-y-1">
                  {list.map((c) => {
                    const assigned = !!c.assignedStaffId;
                    return (
                      <li
                        key={c.id}
                        draggable={canEdit}
                        onDragStart={(e) => {
                          const payload: CourseDragPayload = {
                            courseId: c.id,
                            weekday: c.weekday,
                          };
                          e.dataTransfer.setData(COURSE_DND_MIME, JSON.stringify(payload));
                          e.dataTransfer.effectAllowed = 'move';
                          applyCourseDragImage(
                            e.dataTransfer,
                            c.label,
                            c.visitCount > 0
                              ? `${c.visitCount}件・${c.totalMinutes}分${c.timeRange ? `・${c.timeRange}` : ''}`
                              : '訪問なし',
                          );
                          onDragChange?.(payload);
                        }}
                        onDragEnd={() => onDragChange?.(null)}
                        className={cn(
                          'rounded border px-1.5 py-1 text-[11px] leading-tight transition-opacity',
                          canEdit && 'cursor-grab active:cursor-grabbing',
                          assigned
                            ? 'border-border-subtle bg-bg-base text-text-muted'
                            : 'border-brand-primary/50 bg-brand-primary/5 text-text-primary',
                          // 掴んでいるカードは半透明 + 破線で「持ち出し中」を示す。
                          activeDrag?.courseId === c.id && 'border-dashed opacity-40',
                        )}
                        title={
                          canEdit
                            ? assigned
                              ? `${c.label}（${c.assignedStaffName ?? ''} 担当）— 別のスタッフのセルへドラッグで付け替え`
                              : `${c.label} — スタッフのセルへドラッグして貼り付け`
                            : c.label
                        }
                        data-testid={`palette-course-${c.id}`}
                        data-assigned={assigned ? 'true' : 'false'}
                      >
                        <span className="block truncate font-bold">
                          {assigned ? '' : '⠿ '}
                          {c.label}
                        </span>
                        <span className="block truncate text-[10px] text-text-muted">
                          {c.visitCount > 0
                            ? `${c.visitCount}件・${c.totalMinutes}分${c.timeRange ? `・${c.timeRange}` : ''}`
                            : '訪問なし'}
                        </span>
                        {assigned ? (
                          <span
                            className="block truncate text-[10px] font-medium text-text-secondary"
                            data-testid={`palette-course-${c.id}-staff`}
                          >
                            → {c.assignedStaffName ?? '（不明）'}
                          </span>
                        ) : (
                          <span className="block text-[10px] font-medium text-warning-strong">
                            未割当
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
