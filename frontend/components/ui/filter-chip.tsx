import * as React from 'react';

import { cn } from '@/lib/utils';

interface FilterChipProps {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  className?: string;
}

/**
 * rounded-full トグルチップ。フィルタ選択 UI で使う共通コンポーネント。
 * focus-visible はグローバル既定に任せる。
 */
export function FilterChip({ active, onClick, children, className }: FilterChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-[13px]',
        active
          ? 'border-transparent bg-brand-primary-light font-semibold text-brand-primary-hover'
          : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
        className,
      )}
    >
      {children}
    </button>
  );
}
