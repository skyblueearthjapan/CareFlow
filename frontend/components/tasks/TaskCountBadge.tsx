/**
 * 訪問モニターのカードに出す、やることの印 (設計 §3・patient-tasks-due-design §3)。
 * 未完了あり = 📋N (赤 = 期日を過ぎたものあり・橙 = 2 回目以降の訪問待ちあり)・全部完了 = ✓。
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
  const over = !allDone && count.has_overdue;
  const carry = !allDone && count.has_carried_over;
  const notes = [over ? '期日を過ぎたものあり' : null, carry ? '2 回目以降の訪問待ちあり' : null]
    .filter(Boolean)
    .join('・');
  const title = allDone
    ? '事務所からのやること：すべて完了'
    : `事務所からのやること：未完了 ${count.open_count} 件${notes ? `（${notes}）` : ''}`;
  return (
    <span
      data-testid={testId}
      data-tone={allDone ? 'done' : over ? 'over' : carry ? 'carry' : 'open'}
      title={title}
      role="img"
      aria-label={title}
      className={cn(
        'shrink-0 rounded-full px-1 py-px text-[9px] font-bold leading-tight',
        allDone
          ? 'bg-success-bg text-success'
          : over
            ? 'bg-error text-white'
            : carry
              ? 'bg-[#e07800] text-white'
              : 'bg-brand-primary-light text-brand-primary-hover',
        className,
      )}
    >
      {allDone ? '✓' : `📋${count.open_count}`}
    </span>
  );
}
