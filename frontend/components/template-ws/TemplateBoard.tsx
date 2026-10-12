'use client';

/**
 * 型の盤面 — 日付の無い仮の 1 週間。行 = コース、列 = 月〜土、セル = 患者の時間ブロック。
 * いまの型と違う枠は桃色、赤ピンの枠は赤いピン、最新の編集で変わった枠は縁取り。
 * ブロックは別のコースのセルへドラッグできる（同じ曜日の中だけ。曜日は動かせない）。
 * 時刻・コースの細かい調整はブロックを押して詳細で。決まりに合うかはバックエンドが確かめる。
 */
import {
  DndContext,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import { Pin } from 'lucide-react';

import { NO_COURSE, WEEKDAYS, buildGrid, fmtMin } from '@/lib/templateWs/helpers';
import type { DraftView, SlotCard } from '@/lib/templateWs/types';

export interface TemplateBoardProps {
  view: DraftView;
  readOnly: boolean;
  highlight: Set<string>;
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
  /** ドラッグで別のコースへ（時刻はそのまま）。 */
  onDropToCourse: (slot: SlotCard, courseId: string) => void;
  onWeekdayRefused: () => void;
}

function Block({
  slot,
  readOnly,
  highlighted,
  selected,
  onSelect,
}: {
  slot: SlotCard;
  readOnly: boolean;
  highlighted: boolean;
  selected: boolean;
  onSelect: (key: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: slot.key,
    disabled: readOnly || slot.pair,
  });
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`, zIndex: 20 }
    : undefined;
  const tone = slot.changed
    ? 'border-brand-primary bg-brand-primary-light/60'
    : 'border-border-default bg-bg-base';
  return (
    <button
      ref={setNodeRef}
      type="button"
      style={style}
      {...listeners}
      {...attributes}
      onClick={() => onSelect(slot.key)}
      data-testid={`slot-${slot.key}`}
      data-changed={slot.changed ? 'true' : 'false'}
      data-latest={highlighted ? 'true' : 'false'}
      className={`flex w-full flex-col items-start rounded border px-1.5 py-1 text-left text-[11px] leading-tight ${tone} ${
        highlighted ? 'ring-2 ring-info' : ''
      } ${selected ? 'outline outline-2 outline-brand-primary' : ''} ${isDragging ? 'opacity-70 shadow-md' : ''}`}
    >
      <span className="flex w-full items-center gap-1">
        <span className="tnum font-semibold">{fmtMin(slot.start)}</span>
        {slot.locked ? (
          <Pin className="h-3 w-3 text-error" aria-label="赤ピン（曜日と時刻は動かしません）" />
        ) : null}
        {slot.new ? <span className="rounded bg-info-bg px-1 text-[10px] text-info-strong">新</span> : null}
        {slot.held ? <span className="text-[10px] text-text-muted">手</span> : null}
      </span>
      <span className="w-full truncate">{slot.patient.replace(/\s*様$/, '')}様</span>
      {slot.changed ? (
        <span className="text-[10px] text-text-muted">
          {slot.time_changed && slot.base_start != null ? `←${fmtMin(slot.base_start)} ` : ''}
          {slot.course_changed ? '←元のコース' : ''}
        </span>
      ) : null}
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
    <td
      ref={setNodeRef}
      data-testid={`cell-${weekday}-${courseId}`}
      className={`min-w-[7.5rem] align-top border border-border-subtle p-1 ${
        open ? '' : 'bg-bg-muted/60'
      } ${isOver ? 'bg-brand-primary-50' : ''}`}
    >
      {open && staff ? <div className="mb-1 text-[10px] text-text-muted">{staff}さん</div> : null}
      {!open && courseId !== NO_COURSE ? <div className="text-[10px] text-text-muted">担当なし</div> : null}
      <div className="flex flex-col gap-1">{children}</div>
    </td>
  );
}

export function TemplateBoard({
  view,
  readOnly,
  highlight,
  selectedKey,
  onSelect,
  onDropToCourse,
  onWeekdayRefused,
}: TemplateBoardProps) {
  const rows = buildGrid(view);
  const slotsByKey = new Map(view.days.flatMap((d) => d.slots).map((s) => [s.key, s]));
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));

  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over) return;
    const slot = slotsByKey.get(String(e.active.id));
    if (!slot) return;
    const [wd, courseId = ''] = String(e.over.id).split('|');
    if (Number(wd) !== slot.weekday) {
      onWeekdayRefused();
      return;
    }
    if (courseId === NO_COURSE || courseId === slot.course_id) return;
    onDropToCourse(slot, courseId);
  };

  return (
    <DndContext sensors={sensors} onDragEnd={onDragEnd}>
      <div className="overflow-auto rounded border border-border-default bg-bg-base" data-testid="template-board">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 w-16 border border-border-subtle bg-bg-muted p-1 text-left">
                コース
              </th>
              {WEEKDAYS.map((w, i) => (
                <th key={w} className="border border-border-subtle bg-bg-muted p-1">
                  {w}
                  <span className="ml-1 tnum text-[10px] font-normal text-text-muted">
                    {view.days.find((d) => d.weekday === i)?.slots.filter((s) => s.placed).length ?? 0}件
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} data-testid={`row-${r.id}`}>
                <th className="sticky left-0 z-10 border border-border-subtle bg-bg-muted p-1 text-left">
                  {r.label}
                  {r.manager ? <span className="ml-1 text-[10px] text-text-muted">（あふれ）</span> : null}
                </th>
                {WEEKDAYS.map((_, wd) => {
                  const cell = r.cells[wd];
                  return (
                    <Cell
                      key={wd}
                      weekday={wd}
                      courseId={r.id}
                      open={r.id === NO_COURSE ? false : (cell?.open ?? false)}
                      staff={cell?.staff ?? null}
                    >
                      {(cell?.slots ?? []).map((s) => (
                        <Block
                          key={s.key}
                          slot={s}
                          readOnly={readOnly}
                          highlighted={highlight.has(s.key)}
                          selected={selectedKey === s.key}
                          onSelect={onSelect}
                        />
                      ))}
                    </Cell>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </DndContext>
  );
}
