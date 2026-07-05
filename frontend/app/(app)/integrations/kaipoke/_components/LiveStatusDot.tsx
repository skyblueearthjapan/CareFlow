'use client';

/**
 * LiveStatusDot — 稼働状態を表す小さな脈動インジケータ。
 *
 * running: teal で脈動 / idle: 落ち着いたグレー / unreachable: 赤。
 * 連携センターの各所で状態を一目で伝えるための最小単位。
 */
import { cn } from '@/lib/utils';

type Tone = 'running' | 'idle' | 'error';

const TONE: Record<Tone, { dot: string; ring: string; label: string }> = {
  running: { dot: 'bg-brand-primary', ring: 'bg-brand-primary', label: 'text-brand-primary' },
  idle: { dot: 'bg-border-strong', ring: 'bg-border-strong', label: 'text-text-muted' },
  error: { dot: 'bg-error', ring: 'bg-error', label: 'text-error' },
};

export function LiveStatusDot({
  tone,
  label,
  className,
}: {
  tone: Tone;
  label?: string;
  className?: string;
}) {
  const c = TONE[tone];
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <span className="relative inline-flex h-2.5 w-2.5">
        {tone === 'running' && (
          <span
            className={cn(
              'absolute inline-flex h-full w-full animate-ping rounded-full opacity-60',
              c.ring,
            )}
          />
        )}
        <span className={cn('relative inline-flex h-2.5 w-2.5 rounded-full', c.dot)} />
      </span>
      {label && <span className={cn('text-xs font-medium', c.label)}>{label}</span>}
    </span>
  );
}
