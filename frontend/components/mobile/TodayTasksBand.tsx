/**
 * スマホの今日の一覧の上に出す「今日のやること N 件」の帯 (patient-tasks-design §3)。
 * やることが 1 件も無い日は出さない。
 */
import { ClipboardList } from 'lucide-react';

import type { VisitTaskCount } from '@/lib/schemas/patient-task';

export function TodayTasksBand({
  counts,
  visitIds,
}: {
  /** 訪問 id → 件数 (BE は自分の訪問だけ返す)。 */
  counts: Readonly<Record<string, VisitTaskCount>> | null | undefined;
  /** 今日の一覧に出している訪問 (取消などで消した訪問は数えない)。 */
  visitIds: readonly string[];
}) {
  if (!counts) return null;
  let open = 0;
  let total = 0;
  for (const id of visitIds) {
    const c = counts[id];
    if (!c) continue;
    open += c.open_count;
    total += c.total_count;
  }
  if (total === 0) return null;
  return (
    <div
      className="rounded-md border border-brand-primary-light bg-brand-primary-50 px-3 py-2.5 text-sm text-text-primary"
      data-testid="today-tasks-band"
    >
      <p className="m-0 flex flex-wrap items-center gap-1.5">
        <ClipboardList className="h-4 w-4 shrink-0 text-brand-primary" aria-hidden="true" />
        <b className="text-brand-primary-hover">今日のやること {open} 件</b>
        <span className="text-xs text-text-secondary">（全部で {total} 件）</span>
      </p>
      <p className="m-0 mt-0.5 text-xs text-text-secondary">
        {open > 0
          ? '訪問を開くと、事務所からのお願いが見られます。'
          : '今日の分はすべて完了しました。'}
      </p>
    </div>
  );
}
