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
}

export function WeekCoursePalette({
  courses,
  canEdit,
  onDragChange,
  onUnassignDrop,
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
        'rounded-lg border border-border-default bg-bg-base p-2',
        dropHover && 'ring-2 ring-brand-primary/60',
      )}
      onDragOver={(e) => {
        if (!canEdit || !onUnassignDrop) return;
        if (!e.dataTransfer.types.includes(COURSE_DND_MIME)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        setDropHover(true);
      }}
      onDragLeave={() => setDropHover(false)}
      onDrop={(e) => {
        setDropHover(false);
        if (!canEdit || !onUnassignDrop) return;
        const payload = readCourseDragPayload(e.dataTransfer);
        if (!payload) return;
        e.preventDefault();
        onUnassignDrop(payload.courseId);
      }}
    >
      <div className="mb-1.5 flex items-center gap-2 px-1">
        <h3 className="text-xs font-bold text-text-primary">コースの表</h3>
        <span className="text-[11px] text-text-muted">
          {unassignedCount > 0
            ? `未割当 ${unassignedCount} 件 — カードを上のスタッフのセルへドラッグして貼り付け`
            : 'すべてのコースに担当が付いています'}
        </span>
        {canEdit && onUnassignDrop ? (
          <span className="ml-auto text-[10px] text-text-muted/80">
            ここへ戻すと担当解除（今週のみ・毎週の型には影響しません）
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
                          onDragChange?.(payload);
                        }}
                        onDragEnd={() => onDragChange?.(null)}
                        className={cn(
                          'rounded border px-1.5 py-1 text-[11px] leading-tight',
                          canEdit && 'cursor-grab active:cursor-grabbing',
                          assigned
                            ? 'border-border-subtle bg-bg-base text-text-muted'
                            : 'border-brand-primary/50 bg-brand-primary/5 text-text-primary',
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
