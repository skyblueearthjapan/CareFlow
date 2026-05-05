'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { MobileSection } from '@/components/mobile/MobileSection';
import {
  currentWeekStartIso,
  useMyVisits,
  type MyVisit,
} from '@/lib/queries/me';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** Group visits by `visit_date`. Ordered by ISO date asc, then start_time. */
function groupByDate(visits: MyVisit[]): Array<{ date: string; items: MyVisit[] }> {
  const map = new Map<string, MyVisit[]>();
  for (const v of visits) {
    const list = map.get(v.visit_date) ?? [];
    list.push(v);
    map.set(v.visit_date, list);
  }
  return Array.from(map.entries())
    .map(([date, items]) => ({
      date,
      items: items.sort((a, b) => a.start_time.localeCompare(b.start_time)),
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function formatDateLabel(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  const dow = d.getDay(); // 0 = Sun
  const idx = dow === 0 ? 6 : dow - 1; // shift to Mon-first
  const md = `${d.getMonth() + 1}/${d.getDate()}`;
  return `${md} (${WEEKDAY_LABELS[idx]})`;
}

function shortTime(t: string): string {
  return t.length >= 5 ? t.slice(0, 5) : t;
}

export default function MobileThisWeekPage() {
  const weekStart = currentWeekStartIso();
  const { data: visits, isLoading, isError, error } = useMyVisits({
    weekStart,
  });

  const groups = groupByDate(visits ?? []);

  return (
    <MobileSection title="今週の予定" subtitle={`${weekStart} 週`}>
      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>取得に失敗しました</AlertTitle>
          <AlertDescription>
            {error instanceof Error ? error.message : '不明なエラー'}
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && groups.length === 0 && (
        <Card className="p-6 text-center">
          <p className="text-sm text-text-muted">今週の訪問はありません。</p>
        </Card>
      )}

      <div className="space-y-4">
        {groups.map((g) => (
          <Card key={g.date} className="p-4">
            <header className="mb-2 flex items-baseline justify-between">
              <h2 className="font-serif text-base font-bold text-text-primary">
                {formatDateLabel(g.date)}
              </h2>
              <span className="text-xs text-text-muted">{g.items.length}件</span>
            </header>
            <ul className="divide-y divide-border-default">
              {g.items.map((v) => (
                <li
                  key={v.id}
                  className="flex items-center gap-3 py-2 text-sm"
                >
                  <span className="font-mono tnum text-text-primary w-12 shrink-0">
                    {shortTime(v.start_time)}
                  </span>
                  <span className="flex-1 truncate text-text-primary">
                    {v.patient_name ?? '(患者名未設定)'}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </MobileSection>
  );
}
