import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * shadcn/ui Skeleton — minimal placeholder block.
 * TODO: design-token tuning (rounded radius, surface color) once D4 lands.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded-md bg-bg-muted', className)} {...props} />;
}
