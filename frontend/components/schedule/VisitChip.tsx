'use client';

/**
 * Compact visit pill rendered inside a `WeekGrid` cell. Shows time +
 * patient name and exposes the underlying record on click for editing.
 *
 * `tone="warning"` is used by the parent grid to flag overlap / over-cap
 * cells; we stay visually quiet otherwise so the grid doesn't drown in
 * color.
 */
import { cn } from '@/lib/utils';
import { trimSeconds, type VisitRead } from '@/lib/schemas/visit';

export type VisitChipTone = 'default' | 'warning';

interface VisitChipProps {
  visit: VisitRead;
  tone?: VisitChipTone;
  onClick?: (visit: VisitRead) => void;
  /** Optional office label rendered as a tiny suffix badge. */
  officeLabel?: string | null;
}

export function VisitChip({
  visit,
  tone = 'default',
  onClick,
  officeLabel,
}: VisitChipProps) {
  const start = trimSeconds(visit.start_time);
  const end = trimSeconds(visit.end_time);
  const patient = visit.patient_name ?? '(未設定)';

  return (
    <button
      type="button"
      onClick={() => onClick?.(visit)}
      className={cn(
        'group block w-full rounded-md border px-2 py-1 text-left text-xs transition-colors',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
        tone === 'warning'
          ? 'border-error/50 bg-error/10 text-error hover:bg-error/15'
          : 'border-border-default bg-bg-base text-text-primary hover:bg-bg-muted',
      )}
      aria-label={`${start}〜${end} ${patient} を編集`}
    >
      <div className="tnum font-medium leading-tight">
        {start}–{end}
      </div>
      <div className="truncate leading-tight">{patient}</div>
      {officeLabel ? (
        <div className="truncate text-[10px] leading-tight text-text-muted">
          {officeLabel}
        </div>
      ) : null}
    </button>
  );
}
