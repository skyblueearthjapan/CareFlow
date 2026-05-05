'use client';

import { Loader2 } from 'lucide-react';
import { Button, type ButtonProps } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface CheckInButtonProps extends Omit<ButtonProps, 'size'> {
  loading?: boolean;
  /** Visual emphasis — primary (check-in) or outline (check-out). */
  tone?: 'primary' | 'outline';
}

/**
 * Large, thumb-friendly action button used for check-in / check-out on the
 * mobile visit detail. Stretches to full width and uses the lg button size.
 */
export function CheckInButton({
  className,
  loading,
  tone = 'primary',
  children,
  disabled,
  ...props
}: CheckInButtonProps) {
  return (
    <Button
      type="button"
      size="lg"
      variant={tone === 'primary' ? 'default' : 'outline'}
      className={cn('w-full text-base', className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </Button>
  );
}
