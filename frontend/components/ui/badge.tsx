import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2',
  {
    variants: {
      variant: {
        default:
          'border-transparent bg-brand-primary text-white hover:bg-brand-primary-hover',
        secondary:
          'border-transparent bg-bg-muted text-text-primary hover:bg-bg-muted/80',
        destructive:
          'border-transparent bg-error text-white hover:opacity-90',
        outline: 'border-border-default text-text-primary',
        success:
          'border-transparent bg-success/15 text-success',
        warning:
          'border-transparent bg-warning/15 text-warning',
        info: 'border-transparent bg-info/15 text-info',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { badgeVariants };
