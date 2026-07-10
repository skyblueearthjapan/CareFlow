'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { MobileSection } from '@/components/mobile/MobileSection';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { cn } from '@/lib/utils';
import { genderPalette } from '@/lib/scheduling/timeline';
import { currentWeekStartIso, useMyVisits, type MyVisit } from '@/lib/queries/me';

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
  const {
    data: visits,
    isLoading,
    isError,
    error,
  } = useMyVisits({
    weekStart,
  });

  const groups = groupByDate(visits ?? []);

  return (
    <MobileSection pose="calendar" title="今週の予定" subtitle={`${weekStart} 週`}>
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
        <Card className="p-6">
          <RakusukeNote
            pose="calendar"
            title="今週の訪問はありません"
            comment="新しい予定が入ったら、ここでお知らせしますね"
          />
        </Card>
      )}

      <div className="space-y-4">
        {/* R-9b (PO要望): 今週は「見渡す」画面 — タップ不可の高密度チップを2列で敷き詰める。
            時刻+性別ドット相当の左帯+患者名のみ (住所/詳細は今日の訪問側の役割)。 */}
        {groups.map((g) => (
          <section key={g.date}>
            <header className="mb-1.5 flex items-baseline justify-between">
              <h2 className="font-serif text-base font-bold text-text-primary">
                {formatDateLabel(g.date)}
              </h2>
              <span className="text-xs text-text-muted">{g.items.length}件</span>
            </header>
            {/* R-9c (PO決定): 2列→縦1列。 */}
            <div className="grid grid-cols-1 gap-1.5">
              {g.items.map((v) => (
                <div
                  key={v.id}
                  className={cn(
                    'flex items-center gap-1.5 rounded-md border border-l-[3px] px-1.5 py-1',
                    v.status === 'cancelled' && 'opacity-50',
                  )}
                  style={(() => {
                    const pal = genderPalette(v.patient_sex ?? null);
                    return {
                      background: pal.bg,
                      borderColor: pal.ln,
                      borderLeftColor: pal.bar,
                      color: pal.ink,
                    };
                  })()}
                >
                  <span className="tnum shrink-0 text-[11px] font-semibold">
                    {shortTime(v.start_time)}
                  </span>
                  <span
                    className={cn(
                      'max-w-[55%] shrink-0 truncate text-[12px] font-bold',
                      v.status === 'cancelled' && 'line-through',
                    )}
                  >
                    {v.patient_name ?? '—'}
                  </span>
                  {/* R-9d (PO要望): 縦1列化で空いた右側に住所 (名前より小さいフォント)。 */}
                  {v.patient_address && (
                    <span className="min-w-0 flex-1 truncate text-[10px] opacity-75">
                      📍{v.patient_address}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </MobileSection>
  );
}
