/**
 * EmptyState — neutral "no data" placeholder used by dashboard widgets.
 */
import * as React from 'react';

import { cn } from '@/lib/utils';

export interface EmptyStateProps {
  /** Headline. Default: "データがありません". */
  title?: string;
  /** Optional sub-text. */
  description?: string;
  className?: string;
}

export function EmptyState({
  title = 'データがありません',
  description,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border-default bg-bg-muted/40 px-6 py-10 text-center',
        className,
      )}
    >
      <p className="font-serif text-sm font-bold text-text-secondary">{title}</p>
      {description ? (
        <p className="text-xs text-text-muted">{description}</p>
      ) : null}
    </div>
  );
}
