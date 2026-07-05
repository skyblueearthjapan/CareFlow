'use client';

/**
 * WeekScheduleView — 対象週の CareFlow スケジュールを週ビューで表示 (K-2 UI)。
 *
 * diff とは独立に「その週に CareFlow が入れる予定そのもの」を月〜日の7列で表示する。
 * 週セレクタを動かすと (親が weekStart を変える) この表示も連動して切り替わる。
 * これがカイポケへ反映される内容の実体。
 */
import type { WeekScheduleRow } from '@/lib/schemas/integration';

const WEEKDAY = ['月', '火', '水', '木', '金', '土', '日'];

export function WeekScheduleView({
  weekStart,
  rows,
}: {
  weekStart: Date;
  rows: WeekScheduleRow[];
}) {
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  const byDate = new Map<string, WeekScheduleRow[]>();
  for (const r of rows) {
    const arr = byDate.get(r.visitDate);
    if (arr) arr.push(r);
    else byDate.set(r.visitDate, [r]);
  }

  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[900px] grid-cols-7 gap-2">
        {days.map((d, i) => {
          const list = (byDate.get(iso(d)) ?? []).sort((a, b) =>
            a.startTime.localeCompare(b.startTime),
          );
          return (
            <div key={i} className="rounded-lg border border-border-default bg-bg-base">
              <div className="flex items-center justify-between border-b border-border-subtle px-2 py-1.5 text-xs font-medium text-text-secondary">
                <span>
                  {d.getMonth() + 1}/{d.getDate()}{' '}
                  <span className={i >= 5 ? 'text-error' : ''}>（{WEEKDAY[i]}）</span>
                </span>
                <span className="text-[10px] text-text-muted">{list.length}</span>
              </div>
              <div className="min-h-[110px] space-y-1 p-1.5">
                {list.length === 0 ? (
                  <p className="pt-6 text-center text-[11px] text-text-muted">—</p>
                ) : (
                  list.map((r, j) => (
                    <div
                      key={`${r.patientName}-${r.startTime}-${j}`}
                      className="rounded-md border border-border-subtle bg-bg-muted/40 px-2 py-1 text-[11px]"
                    >
                      <div className="font-mono tabular-nums text-text-secondary">
                        {r.startTime}
                        <span className="text-text-muted">–{r.endTime}</span>
                      </div>
                      <div className="truncate font-medium text-text-primary" title={r.patientName}>
                        {r.patientName}
                      </div>
                      <div className="truncate text-text-muted" title={r.staff1}>
                        {r.staff1}
                        {r.staff2 ? `・${r.staff2}` : ''}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
