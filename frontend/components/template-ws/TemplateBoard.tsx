'use client';

/**
 * 型の盤面 — 日付の無い仮の 1 週間。行 = コース、列 = 月〜土、セル = 患者の枠（時刻＋お名前）。
 * 見た目は週の予定の「コース × 曜日」の一覧（CourseWeekOverview）にそろえる: 曜日の見出しに件数、
 * セルの頭に「担当: 名前」、枠は左に色の帯のある 1 行のカード。
 * いまの型と違う枠は桃色の帯、赤ピンの枠は赤いピン、最新の編集で変わった枠は青い縁取り。
 * 全拠点の下書きは拠点ごとに見出しの行を入れて並べる（コースなしの行も拠点ごと）。
 * 列は「残りの幅を 6 等分（いちばん狭くて 7.5rem）」なので、右の欄の幅が変わっても月〜土が収まり、
 * 収まらないときだけ横にスクロールする。
 * ブロックは別のコースのセルへドラッグできる（同じ曜日の中だけ。曜日は動かせない）。
 */
import { Fragment, useEffect, useRef } from 'react';
import {
  DndContext,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';

import { PushPin } from '@/components/ui/push-pin';
import { WEEKDAYS, buildGrid, dayCounts, fmtMin, isNoCourse } from '@/lib/templateWs/helpers';
import type { DraftView, SlotCard } from '@/lib/templateWs/types';

/** 左の見出しの列の幅と、曜日の列のいちばん狭い幅（盤面の幅の計算: 76 + 6 × 120 = 796px）。 */
export const LABEL_COL_PX = 76;
export const DAY_COL_MIN_PX = 120;
export const GRID_COLS = `${LABEL_COL_PX}px repeat(${WEEKDAYS.length}, minmax(${DAY_COL_MIN_PX}px, 1fr))`;

export interface TemplateBoardProps {
  view: DraftView;
  readOnly: boolean;
  highlight: Set<string>;
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
  /** 報告の一覧から押した枠（盤面で光らせて、見える所までスクロールする）。 */
  flashKey?: string | null;
  /** ドラッグで別のコースへ（時刻はそのまま）。 */
  onDropToCourse: (slot: SlotCard, courseId: string) => void;
  onWeekdayRefused: () => void;
}

function Block({
  slot,
  readOnly,
  highlighted,
  selected,
  flashing,
  baseLabel,
  onSelect,
}: {
  slot: SlotCard;
  readOnly: boolean;
  highlighted: boolean;
  selected: boolean;
  flashing: boolean;
  baseLabel: string | null;
  onSelect: (key: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: slot.key,
    disabled: readOnly || slot.pair,
  });
  const ref = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (flashing) ref.current?.scrollIntoView?.({ block: 'center', inline: 'center', behavior: 'smooth' });
  }, [flashing]);
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`, zIndex: 20 }
    : undefined;
  // 週の予定のカードと同じ形: 左に 3px の帯。変わった枠は桃色の帯と薄い桃色の地
  const tone = slot.changed
    ? 'border-brand-primary/50 border-l-brand-primary bg-brand-primary-50'
    : 'border-border-default border-l-border-strong bg-bg-base';
  const name = slot.patient.replace(/\s*様$/, '');
  const hint = slot.changed
    ? [
        slot.course_changed ? `←${baseLabel ?? '元のコース'}` : null,
        slot.time_changed && slot.base_start != null ? `←${fmtMin(slot.base_start)}` : null,
      ]
        .filter(Boolean)
        .join(' ')
    : '';
  return (
    <button
      ref={(el) => {
        setNodeRef(el);
        ref.current = el;
      }}
      type="button"
      style={style}
      {...listeners}
      {...attributes}
      onClick={() => onSelect(slot.key)}
      data-testid={`slot-${slot.key}`}
      data-changed={slot.changed ? 'true' : 'false'}
      data-latest={highlighted ? 'true' : 'false'}
      data-flash={flashing ? 'true' : 'false'}
      title={`${fmtMin(slot.start)} ${name}様${hint ? `（${slot.course_changed ? '元のコース' : '元の時刻'} ${hint.replace(/←/g, '')}）` : ''}`}
      className={`block w-full rounded border border-l-[3px] px-1 py-0.5 text-left text-xs leading-snug text-text-primary ${tone} ${
        highlighted ? 'ring-2 ring-info' : ''
      } ${flashing ? 'ring-2 ring-warning animate-pulse' : ''} ${
        selected ? 'outline outline-2 outline-offset-1 outline-brand-primary' : ''
      } ${isDragging ? 'opacity-70 shadow-md' : ''}`}
    >
      <span className="flex w-full min-w-0 items-center gap-1">
        <span className="tnum shrink-0 text-text-secondary">{fmtMin(slot.start)}</span>
        <span className="min-w-0 flex-1 truncate font-medium">{name}様</span>
        {slot.locked ? (
          <PushPin
            className="h-3 w-3 shrink-0"
            role="img"
            aria-hidden={false}
            aria-label="赤ピン（曜日と時刻は動かしません）"
          />
        ) : null}
        {slot.new ? (
          <span className="shrink-0 rounded bg-info-bg px-1 text-[11px] font-semibold text-info-strong">新</span>
        ) : null}
        {slot.held ? (
          <span className="shrink-0 text-[11px] text-text-muted" title="手で動かした枠（最適化で動かしません）">
            手
          </span>
        ) : null}
      </span>
      {hint ? <span className="block truncate text-[11px] text-text-muted">{hint}</span> : null}
    </button>
  );
}

function Cell({
  weekday,
  courseId,
  open,
  staff,
  children,
}: {
  weekday: number;
  courseId: string;
  open: boolean;
  staff: string | null;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${weekday}|${courseId}`, disabled: !open });
  return (
    <div
      ref={setNodeRef}
      role="gridcell"
      data-testid={`cell-${weekday}-${courseId}`}
      className={`min-w-0 border-b border-r border-border-default px-1.5 py-1 ${
        open || isNoCourse(courseId) ? 'bg-bg-base' : 'bg-bg-muted/40'
      } ${isOver ? 'bg-brand-primary-50' : ''}`}
    >
      {open && staff ? (
        <div className="mb-1 truncate border-b border-border-default/40 pb-0.5 text-[11px]">
          <span className="font-semibold text-text-secondary">担当: </span>
          {staff}
        </div>
      ) : null}
      {!open && !isNoCourse(courseId) ? <div className="text-[11px] text-text-muted">担当なし</div> : null}
      <div className="flex flex-col gap-0.5">{children}</div>
    </div>
  );
}

export function TemplateBoard({
  view,
  readOnly,
  highlight,
  selectedKey,
  onSelect,
  flashKey = null,
  onDropToCourse,
  onWeekdayRefused,
}: TemplateBoardProps) {
  const rows = buildGrid(view);
  const slotsByKey = new Map(view.days.flatMap((d) => d.slots).map((s) => [s.key, s]));
  const courseLabel = new Map(view.courses.map((c) => [c.id, c.label]));
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const multi = rows.some((r) => r.office);

  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over) return;
    const slot = slotsByKey.get(String(e.active.id));
    if (!slot) return;
    const [wd, courseId = ''] = String(e.over.id).split('|');
    if (Number(wd) !== slot.weekday) {
      onWeekdayRefused();
      return;
    }
    if (isNoCourse(courseId) || courseId === slot.course_id) return;
    onDropToCourse(slot, courseId);
  };

  return (
    <DndContext sensors={sensors} onDragEnd={onDragEnd}>
      <div
        className="min-h-0 overflow-auto rounded-lg border border-border-default bg-bg-base"
        data-testid="template-board"
      >
        <div role="grid" className="grid text-xs" style={{ gridTemplateColumns: GRID_COLS }}>
          <div className="sticky left-0 top-0 z-20 border-b border-r border-border-default bg-bg-muted px-2 py-1.5 text-[11px] font-semibold text-text-muted">
            コース
          </div>
          {WEEKDAYS.map((w, i) => {
            const c = dayCounts(view, i);
            return (
              <div
                key={w}
                role="columnheader"
                className="sticky top-0 z-10 flex flex-col items-center border-b border-r border-border-default bg-bg-muted px-1 py-1 text-center"
                data-testid={`day-head-${i}`}
              >
                <span className="text-sm font-semibold text-text-primary">{w}</span>
                <span className="tnum text-[11px] text-text-muted">
                  {c.total} 件
                  {c.byOffice.length > 1 ? `（${c.byOffice.map((o) => `${o.label} ${o.n}`).join('・')}）` : ''}
                </span>
              </div>
            );
          })}

          {rows.map((r, idx) => (
            <Fragment key={r.id}>
              {multi && r.office && rows[idx - 1]?.office !== r.office ? (
                <div
                  className="col-span-full border-b border-border-default bg-brand-primary-50 px-2 py-1 text-xs font-semibold text-text-primary"
                  data-testid={`office-head-${r.officeId}`}
                >
                  {r.office}
                  <span className="ml-2 font-normal text-text-muted">
                    コース {rows.filter((x) => x.office === r.office && !isNoCourse(x.id)).length} 本
                  </span>
                </div>
              ) : null}
              <div
                role="rowheader"
                data-testid={`row-${r.id}`}
                className="sticky left-0 z-10 border-b border-r border-border-default bg-bg-base px-2 py-1 text-xs font-semibold text-text-primary"
              >
                {r.label}
                {r.manager ? <span className="block text-[11px] font-normal text-text-muted">あふれ</span> : null}
              </div>
              {WEEKDAYS.map((_, wd) => {
                const cell = r.cells[wd];
                return (
                  <Cell
                    key={wd}
                    weekday={wd}
                    courseId={r.id}
                    open={isNoCourse(r.id) ? false : (cell?.open ?? false)}
                    staff={cell?.staff ?? null}
                  >
                    {(cell?.slots ?? []).map((s) => (
                      <Block
                        key={s.key}
                        slot={s}
                        readOnly={readOnly}
                        highlighted={highlight.has(s.key)}
                        selected={selectedKey === s.key}
                        flashing={flashKey === s.key}
                        baseLabel={s.base_course_id ? (courseLabel.get(s.base_course_id) ?? null) : null}
                        onSelect={onSelect}
                      />
                    ))}
                  </Cell>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>
    </DndContext>
  );
}
