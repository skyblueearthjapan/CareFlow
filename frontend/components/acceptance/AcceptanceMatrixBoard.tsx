'use client';

/**
 * 受け入れ枠マトリックス (PC版) — P2 / P4。
 *
 * 拠点 × 曜日 × 時間帯の ○△× を 1 拠点 = 1 表で縦に積んで表示する。値は
 * effective_status (= 週別上書き ?? 常設上書き ?? 自動算出) を表示し、上書きセルには
 * 由来印 (週=週別 / 常=常設) を付ける。デザイン (claude.ai/design `careflow-avail.jsx`
 * の AvailDesktop) の構成を Tailwind + デザイントークンに移植したもの。印刷も考慮し、
 * 色だけでなく ○△× の記号で判別できるようにしている。
 *
 * P4: ``onEditCell`` を渡すと各セルがクリックで「週別上書き」を編集できる
 * (admin/manager のみ。page 側で権限を制御)。
 */
import { useState } from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { normalizeTimeSlot, type AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import type {
  AcceptanceMatrixResponse,
  MatrixCell,
  OfficeMatrix,
} from '@/lib/schemas/v2/acceptance_matrix';
import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** セル編集ハンドラ。status=null は上書き解除 (常設/自動へ戻す)。 */
export type OnEditCell = (args: {
  officeId: string;
  weekday: number;
  timeSlot: string; // "HH:MM:SS"
  status: AcceptanceStatus | null;
}) => void;

interface StatusMeta {
  glyph: string;
  cellClass: string;
  long: string;
}

const STATUS_META: Record<AcceptanceStatus, StatusMeta> = {
  available: {
    glyph: '○',
    cellClass: 'bg-brand-primary/10 text-brand-primary font-bold',
    long: '受け入れ可能',
  },
  consult: { glyph: '△', cellClass: 'bg-warning/10 text-warning font-bold', long: '相談ください' },
  unavailable: { glyph: '×', cellClass: 'text-text-muted', long: '枠なし' },
};

function weekdayClass(weekday: number): string {
  if (weekday === 5) return 'text-blue-600'; // 土
  if (weekday === 6) return 'text-rose-500'; // 日
  return 'text-text-primary';
}

export function AcceptanceMatrixLegend({ className }: { className?: string }) {
  return (
    <div className={cn('flex flex-wrap items-center gap-4 text-xs text-text-secondary', className)}>
      {(['available', 'consult', 'unavailable'] as const).map((s) => {
        const m = STATUS_META[s];
        return (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span
              className={cn(
                'grid h-5 w-5 place-items-center rounded border border-border-default text-xs',
                m.cellClass,
              )}
            >
              {m.glyph}
            </span>
            {m.long}
          </span>
        );
      })}
      <span className="text-text-muted">数字 = その時間帯に入れる空きコース数</span>
    </div>
  );
}

function MatrixCellView({
  cell,
  closed,
  hhmm,
  onPick,
  busy,
}: {
  cell: MatrixCell | undefined;
  closed: boolean;
  hhmm: string;
  onPick?: (status: AcceptanceStatus | null) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);

  if (closed) {
    return (
      <div className="grid h-9 place-items-center border-l border-t border-border-default/60 bg-bg-muted text-[10px] text-text-muted">
        休
      </div>
    );
  }

  const status = cell?.effective_status ?? 'unavailable';
  const meta = STATUS_META[status];
  const src = cell?.source ?? 'auto';
  const mark = src === 'week_override' ? '週' : src === 'manual_standing' ? '常' : null;
  // 空きコース数 (自動算出セルのみ。手動上書きは人の判断なので数字は出さない)。
  const count =
    src === 'auto' && cell
      ? status === 'available'
        ? cell.metrics.available_course_count
        : status === 'consult'
          ? cell.metrics.consult_course_count
          : 0
      : 0;
  const title = cell
    ? `${cell.metrics.reasons.join(' / ')}｜空き ${cell.metrics.available_course_count}コース・残 ${cell.metrics.remaining_patients_total}名・${cell.metrics.remaining_minutes_total}分`
    : undefined;

  const body = (
    <div
      className={cn(
        'relative grid h-9 place-items-center border-l border-t border-border-default/60 text-base',
        meta.cellClass,
        src === 'week_override' && 'ring-1 ring-inset ring-brand-primary/50',
      )}
    >
      <span className="inline-flex items-baseline gap-px leading-none">
        <span>{meta.glyph}</span>
        {count > 0 && <span className="text-[10px] font-bold">{count}</span>}
      </span>
      {mark && (
        <span className="absolute right-0.5 top-0 text-[7px] leading-none text-text-muted">
          {mark}
        </span>
      )}
    </div>
  );

  if (!onPick) {
    return <div title={title}>{body}</div>;
  }

  const pick = (s: AcceptanceStatus | null) => {
    onPick(s);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          title={title}
          aria-label={`${hhmm} の受け入れ枠を編集`}
          className="block w-full cursor-pointer"
        >
          {body}
        </button>
      </PopoverTrigger>
      <PopoverContent align="center" className="w-48 p-2">
        <div className="mb-1.5 text-[11px] text-text-secondary">{hhmm}・この週だけ上書き</div>
        <div className="flex flex-col gap-1">
          {(['available', 'consult', 'unavailable'] as const).map((s) => {
            const mm = STATUS_META[s];
            const active = cell?.week_status === s;
            return (
              <button
                key={s}
                type="button"
                disabled={busy}
                onClick={() => pick(s)}
                className={cn(
                  'flex items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-bg-muted disabled:opacity-50',
                  active && 'bg-bg-muted ring-1 ring-inset ring-brand-primary/40',
                )}
              >
                <span
                  className={cn('grid h-5 w-5 place-items-center rounded text-xs', mm.cellClass)}
                >
                  {mm.glyph}
                </span>
                {mm.long}
              </button>
            );
          })}
          <button
            type="button"
            disabled={busy}
            onClick={() => pick(null)}
            className="mt-0.5 rounded border-t border-border-default px-2 py-1.5 text-left text-xs text-text-secondary hover:bg-bg-muted disabled:opacity-50"
          >
            上書きを解除（常設/自動に戻す）
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function OfficeMatrixTable({
  office,
  slots,
  onEditCell,
  editBusy,
}: {
  office: OfficeMatrix;
  slots: string[];
  onEditCell?: OnEditCell;
  editBusy?: boolean;
}) {
  const dayByWeekday = new Map(office.days.map((d) => [d.weekday, d]));
  const cellLookup = new Map<number, Map<string, MatrixCell>>();
  for (const d of office.days) {
    const m = new Map<string, MatrixCell>();
    for (const c of d.cells) m.set(normalizeTimeSlot(c.time_slot), c);
    cellLookup.set(d.weekday, m);
  }
  const operatingSet = new Set(office.operating_weekdays);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <span className="h-5 w-2 rounded bg-brand-primary" aria-hidden="true" />
        <h3 className="font-serif text-lg font-bold text-text-primary">{office.office_name}</h3>
        {office.city_names.length > 0 && (
          <span className="text-xs text-text-secondary">{office.city_names.join('・')}</span>
        )}
        {!office.week_generated && (
          <span className="rounded bg-bg-muted px-1.5 py-0.5 text-[10px] text-text-muted">
            週未生成
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <div
          className="min-w-[420px] overflow-hidden rounded-lg border border-border-default bg-bg-base"
          style={{ display: 'grid', gridTemplateColumns: '56px repeat(7, minmax(38px, 1fr))' }}
        >
          {/* ヘッダ行 */}
          <div className="border-b border-border-default bg-bg-muted" />
          {WEEKDAY_LABELS.map((label, w) => (
            <div
              key={w}
              className={cn(
                'grid h-9 place-items-center border-b border-l border-border-default bg-bg-muted font-serif text-sm font-bold',
                weekdayClass(w),
              )}
            >
              {label}
            </div>
          ))}

          {/* 本体行 (時間帯ごと) */}
          {slots.map((slot) => {
            const hhmm = normalizeTimeSlot(slot);
            return (
              <div key={slot} style={{ display: 'contents' }}>
                <div className="tnum grid h-9 place-items-center bg-bg-muted text-[11px] text-text-secondary">
                  {hhmm}
                </div>
                {WEEKDAY_LABELS.map((_, w) => {
                  const day = dayByWeekday.get(w);
                  const cell = cellLookup.get(w)?.get(hhmm);
                  const closed = day?.office_closed ?? !operatingSet.has(w);
                  const onPick =
                    onEditCell && !closed
                      ? (status: AcceptanceStatus | null) =>
                          onEditCell({
                            officeId: office.office_id,
                            weekday: w,
                            timeSlot: `${hhmm}:00`,
                            status,
                          })
                      : undefined;
                  return (
                    <MatrixCellView
                      key={w}
                      cell={cell}
                      closed={closed}
                      hhmm={hhmm}
                      onPick={onPick}
                      busy={editBusy}
                    />
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function AcceptanceMatrixBoard({
  data,
  onEditCell,
  editBusy,
}: {
  data: AcceptanceMatrixResponse;
  onEditCell?: OnEditCell;
  editBusy?: boolean;
}) {
  if (data.offices.length === 0) {
    return <p className="text-sm text-text-secondary">表示できる拠点がありません。</p>;
  }
  return (
    <div className="space-y-8">
      {data.offices.map((office) => (
        <OfficeMatrixTable
          key={office.office_id}
          office={office}
          slots={data.slots}
          onEditCell={onEditCell}
          editBusy={editBusy}
        />
      ))}
    </div>
  );
}
