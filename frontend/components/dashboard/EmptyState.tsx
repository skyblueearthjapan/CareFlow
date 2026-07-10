/**
 * EmptyState — neutral "no data" placeholder used by dashboard widgets.
 */
import * as React from 'react';

import { cn } from '@/lib/utils';
import { Rakusuke, type RakusukePose } from '@/components/brand/Rakusuke';

export interface EmptyStateProps {
  /** Headline. Default: "データがありません". */
  title?: string;
  /** Optional sub-text. */
  description?: string;
  /** らく助のポーズ (R-4)。 */
  pose?: RakusukePose;
  className?: string;
}

export function EmptyState({
  title = 'データがありません',
  description,
  pose = 'think',
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border-default bg-bg-muted/40 px-6 py-8 text-center',
        className,
      )}
    >
      <Rakusuke pose={pose} className="mb-1 h-16" />
      <p className="font-serif text-sm font-bold text-text-secondary">{title}</p>
      {description ? <p className="text-xs text-text-muted">{description}</p> : null}
    </div>
  );
}
