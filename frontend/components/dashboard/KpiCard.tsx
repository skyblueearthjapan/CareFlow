/**
 * KpiCard — single-metric tile for the dashboard.
 *
 * Renders a compact card with: small label, large numeric value, optional
 * unit (e.g. "%"), and an optional delta against "yesterday/last week".
 * Loading state shows a Skeleton block in place of the value to avoid
 * layout shift.
 */
import * as React from 'react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export interface KpiCardProps {
  /** Short label shown above the value. */
  label: string;
  /** Display value — already formatted (e.g. "92%", "12"). */
  value: React.ReactNode;
  /** Suffix (e.g. "件", "%"); rendered smaller next to the value. */
  unit?: string;
  /** Optional delta-vs-baseline string; rendered below the value. */
  delta?: string;
  /** Hint for delta sign (default: neutral). */
  deltaTone?: 'up' | 'down' | 'neutral';
  /** When true, render a skeleton in place of the value. */
  isLoading?: boolean;
  /** Optional caption shown below the value/delta. */
  caption?: string;
  className?: string;
}

const DELTA_CLASS: Record<NonNullable<KpiCardProps['deltaTone']>, string> = {
  up: 'text-emerald-600',
  down: 'text-rose-600',
  neutral: 'text-text-muted',
};

export function KpiCard({
  label,
  value,
  unit,
  delta,
  deltaTone = 'neutral',
  isLoading = false,
  caption,
  className,
}: KpiCardProps) {
  return (
    <Card className={cn('p-5', className)}>
      <p className="text-xs text-text-muted">{label}</p>
      <div className="mt-2 flex items-baseline gap-1">
        {isLoading ? (
          <Skeleton className="h-9 w-16" />
        ) : (
          <>
            <span className="font-serif text-3xl font-bold tnum text-text-primary">
              {value}
            </span>
            {unit ? <span className="text-sm text-text-secondary">{unit}</span> : null}
          </>
        )}
      </div>
      {delta && !isLoading ? (
        <p className={cn('mt-1 text-xs tnum', DELTA_CLASS[deltaTone])}>{delta}</p>
      ) : null}
      {caption && !isLoading ? (
        <p className="mt-1 text-xs text-text-muted">{caption}</p>
      ) : null}
    </Card>
  );
}
