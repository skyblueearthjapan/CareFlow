'use client';

/**
 * Compact visit pill rendered inside a `WeekGrid` cell. Shows time +
 * patient name and exposes the underlying record on click for editing.
 *
 * `tone="warning"` is used by the parent grid to flag overlap / over-cap
 * cells; we stay visually quiet otherwise so the grid doesn't drown in
 * color.
 *
 * `canEdit=false` (staff role) downgrades the chip to a non-interactive
 * `<div>` so click-through to the edit dialog is impossible — the dialog
 * itself would 403 on save anyway, so swallowing the click avoids the
 * round-trip and the broken UX.
 */
import { cn } from '@/lib/utils';
import { trimSeconds, type VisitRead } from '@/lib/schemas/visit';

export type VisitChipTone = 'default' | 'warning';

interface VisitChipProps {
  visit: VisitRead;
  tone?: VisitChipTone;
  /** When false, the chip renders as a static element (no click handler). */
  canEdit?: boolean;
  onClick?: (visit: VisitRead) => void;
  /** Optional office label rendered as a tiny suffix badge. */
  officeLabel?: string | null;
  /** Optional role badge (e.g. "同行" for secondary, "指導" for mentor). */
  roleBadge?: string | null;
}

export function VisitChip({
  visit,
  tone = 'default',
  canEdit = true,
  onClick,
  officeLabel,
  roleBadge,
}: VisitChipProps) {
  const start = trimSeconds(visit.start_time);
  const end = trimSeconds(visit.end_time);
  const patient = visit.patient_name ?? '(未設定)';

  const className = cn(
    'group block w-full rounded-md border px-2 py-1 text-left text-xs transition-colors',
    canEdit && 'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
    tone === 'warning'
      ? 'border-error/50 bg-error/10 text-error'
      : 'border-border-default bg-bg-base text-text-primary',
    canEdit && (tone === 'warning' ? 'hover:bg-error/15' : 'hover:bg-bg-muted'),
  );

  const inner = (
    <>
      <div className="tnum font-medium leading-tight">
        {start}–{end}
      </div>
      <div className="truncate leading-tight">{patient}</div>
      {roleBadge ? (
        <div className="truncate text-[10px] font-medium leading-tight text-text-secondary">
          {roleBadge}
        </div>
      ) : null}
      {officeLabel ? (
        <div className="truncate text-[10px] leading-tight text-text-muted">{officeLabel}</div>
      ) : null}
    </>
  );

  if (!canEdit) {
    return (
      <div className={className} aria-label={`${start}〜${end} ${patient}`}>
        {inner}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onClick?.(visit)}
      className={className}
      aria-label={`${start}〜${end} ${patient} を編集`}
    >
      {inner}
    </button>
  );
}
