'use client';

/**
 * 受け入れ枠マトリックス (PC版) — P2.
 *
 * 拠点 × 曜日 × 時間帯の ○△× を 1 拠点 = 1 表で縦に積んで表示する。
 * 値は effective_status (= 手動上書き ?? 自動算出) を表示し、手動上書きセルには
 * 小さな印を付ける。デザイン (claude.ai/design `careflow-avail.jsx` の AvailDesktop)
 * の構成を Tailwind + デザイントークンに移植したもの。印刷も考慮し、色だけでなく
 * ○△× の記号で判別できるようにしている。
 */
import { useMemo } from 'react';

import { cn } from '@/lib/utils';
import { normalizeTimeSlot, type AcceptanceStatus } from '@/lib/schemas/v2/acceptance';
import type {
  AcceptanceMatrixResponse,
  MatrixCell,
  OfficeMatrix,
} from '@/lib/schemas/v2/acceptance_matrix';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

interface StatusMeta {
  glyph: string;
  text: string;
  cellClass: string;
  long: string;
}

const STATUS_META: Record<AcceptanceStatus, StatusMeta> = {
  available: {
    glyph: '○',
    text: 'text-brand-primary',
    cellClass: 'bg-brand-primary/10 text-brand-primary font-bold',
    long: '受け入れ可能',
  },
  consult: {
    glyph: '△',
    text: 'text-warning',
    cellClass: 'bg-warning/10 text-warning font-bold',
    long: '相談ください',
  },
  unavailable: {
    glyph: '×',
    text: 'text-text-muted',
    cellClass: 'text-text-muted',
    long: '枠なし',
  },
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
    </div>
  );
}

function MatrixCellView({ cell, closed }: { cell: MatrixCell | undefined; closed: boolean }) {
  if (closed) {
    return (
      <div className="grid h-9 place-items-center border-l border-t border-border-default/60 bg-bg-muted text-[10px] text-text-muted">
        休
      </div>
    );
  }
  const status = cell?.effective_status ?? 'unavailable';
  const meta = STATUS_META[status];
  const isManual = cell?.source === 'manual_standing';
  const title = cell
    ? `${cell.metrics.reasons.join(' / ')}｜残 ${cell.metrics.remaining_patients_total}名・${cell.metrics.remaining_minutes_total}分${isManual ? '（手動上書き）' : ''}`
    : undefined;
  return (
    <div
      title={title}
      className={cn(
        'relative grid h-9 place-items-center border-l border-t border-border-default/60 text-base',
        meta.cellClass,
      )}
    >
      <span>{meta.glyph}</span>
      {isManual && (
        <span className="absolute right-0.5 top-0 text-[7px] leading-none text-text-muted">手</span>
      )}
    </div>
  );
}

function OfficeMatrixTable({ office, slots }: { office: OfficeMatrix; slots: string[] }) {
  // weekday -> day, day -> (HH:MM -> cell) の lookup を作る (render 毎の再生成を避ける).
  const { dayByWeekday, cellLookup, operatingSet } = useMemo(() => {
    const dayByWeekday = new Map(office.days.map((d) => [d.weekday, d]));
    const cellLookup = new Map<number, Map<string, MatrixCell>>();
    for (const d of office.days) {
      const m = new Map<string, MatrixCell>();
      for (const c of d.cells) m.set(normalizeTimeSlot(c.time_slot), c);
      cellLookup.set(d.weekday, m);
    }
    const operatingSet = new Set(office.operating_weekdays);
    return { dayByWeekday, cellLookup, operatingSet };
  }, [office.days, office.operating_weekdays]);

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
                  // day が無い場合も operating_weekdays から定休を判定 (防御).
                  const closed = day?.office_closed ?? !operatingSet.has(w);
                  return <MatrixCellView key={w} cell={cell} closed={closed} />;
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function AcceptanceMatrixBoard({ data }: { data: AcceptanceMatrixResponse }) {
  if (data.offices.length === 0) {
    return <p className="text-sm text-text-secondary">表示できる拠点がありません。</p>;
  }
  return (
    <div className="space-y-8">
      {data.offices.map((office) => (
        <OfficeMatrixTable key={office.office_id} office={office} slots={data.slots} />
      ))}
    </div>
  );
}
