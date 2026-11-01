/**
 * 訪問モニターのカードに出す、やることの印 (設計 §3)。
 * 未完了あり = 📋N (持ち越し = 期日を過ぎたものがあれば赤)・全部完了 = ✓。
 */
import { cn } from '@/lib/utils';
import type { VisitTaskCount } from '@/lib/schemas/patient-task';

export function TaskCountBadge({
  count,
  testId,
  className,
}: {
  count: VisitTaskCount | null | undefined;
  testId?: string;
  className?: string;
}) {
  if (!count || count.total_count === 0) return null;
  const allDone = count.all_done || count.open_count === 0;
  const over = !allDone && count.has_carried_over;
  const title = allDone
    ? '事務所からのやること：すべて完了'
    : `事務所からのやること：未完了 ${count.open_count} 件${over ? '（期日を過ぎたものあり）' : ''}`;
  return (
    <span
      data-testid={testId}
      data-tone={allDone ? 'done' : over ? 'over' : 'open'}
      title={title}
      aria-label={title}
      className={cn(
        'shrink-0 rounded-full px-1 py-px text-[9px] font-bold leading-tight',
        allDone
          ? 'bg-success-bg text-success'
          : over
            ? 'bg-error text-white'
            : 'bg-brand-primary-light text-brand-primary-hover',
        className,
      )}
    >
      {allDone ? '✓' : `📋${count.open_count}`}
    </span>
  );
}
