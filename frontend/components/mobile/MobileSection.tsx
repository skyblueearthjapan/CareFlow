import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Section wrapper used by every screen inside the bottom-nav layout.
 * Adds consistent vertical rhythm and a serif title block when provided.
 */
interface MobileSectionProps extends Omit<React.HTMLAttributes<HTMLElement>, 'title'> {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  /** Right-side slot for actions (e.g. notification bell). */
  action?: React.ReactNode;
}

export function MobileSection({
  className,
  title,
  subtitle,
  action,
  children,
  ...props
}: MobileSectionProps) {
  return (
    <section className={cn('space-y-4', className)} {...props}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            {title && <h1 className="font-serif text-xl font-bold text-text-primary">{title}</h1>}
            {subtitle && <p className="text-sm text-text-secondary">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
