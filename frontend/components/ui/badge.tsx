import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-brand-primary text-white hover:bg-brand-primary-hover',
        // hover:bg-bg-muted/80 → hover:bg-bg-muted:
        //   bg-muted は var() 定義なので Tailwind の /alpha 修飾子が生成されない。
        //   淡色トークン自体との差は視覚上ほぼ無いため /80 を除去。
        secondary: 'border-transparent bg-bg-muted text-text-primary hover:bg-bg-muted',
        destructive: 'border-transparent bg-error text-white hover:opacity-90',
        outline: 'border-border-default text-text-primary',
        // bg-success/15 等は var() 色に <alpha-value> が未定義なため CSS が生成されない本番バグ。
        // P0-2 で追加済みの *-bg トークン（tokens.css の --success-bg 等）に置換。
        success: 'border-transparent bg-success-bg text-success',
        warning: 'border-transparent bg-warning-bg text-warning',
        info: 'border-transparent bg-info-bg text-info',
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
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
