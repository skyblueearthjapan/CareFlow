'use client';

/**
 * WeekScheduleView — 対象週の CareFlow スケジュールを「コース別週表」で表示 (K-UI)。
 *
 * 現場が見慣れた既存スケジュール画面(CourseWeekOverview)と同じ「行=コース × 列=曜日」
 * のレイアウト。行ヘッダ=コース(A/B/C/D、複数拠点なら「拠点-コース」)、各セルは
 * (コース×曜日)の患者を開始時刻順に縦並び。週セレクタを動かすと親から rows が
 * 切り替わり、この表も連動する。
 */
import type { WeekScheduleRow } from '@/lib/schemas/integration';
import { genderPalette } from '@/lib/scheduling/timeline';

const DAY_LABEL = ['月', '火', '水', '木', '金', '土']; // 月〜土 (既存画面と同じ・日曜除外)

export function WeekScheduleView({
  weekStart,
  rows,
}: {
  weekStart: Date;
  rows: WeekScheduleRow[];
}) {
  const dayDates = Array.from({ length: 6 }, (_, i) => {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    return d;
  });

  // 拠点が複数かどうか (単一なら行ヘッダを「A」だけにする)。
  const offices = new Set(rows.map((r) => r.officeName).filter(Boolean));
  const multiOffice = offices.size > 1;

  // コース一覧 (A,B,C,.. 昇順)。course が空の行は「その他」に集約。
  const courseKeys = Array.from(new Set(rows.map((r) => r.courseCode || '—'))).sort((a, b) =>
    a.localeCompare(b),
  );

  // (courseKey, weekday) → 行。
  const cell = (courseKey: string, weekday: number) =>
    rows
      .filter((r) => (r.courseCode || '—') === courseKey && r.weekday === weekday)
      .sort((a, b) => a.startTime.localeCompare(b.startTime));

  const courseLabel = (courseKey: string) => {
    if (courseKey === '—') return 'その他';
    if (!multiOffice) return `${courseKey}コース`;
    // 複数拠点: 最初に見つかった拠点名を接頭。
    const office = rows.find((r) => (r.courseCode || '—') === courseKey)?.officeName;
    return office ? `${office}-${courseKey}` : `${courseKey}コース`;
  };

  if (rows.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[860px]">
        {/* ヘッダ行 */}
        <div
          className="grid gap-px border-b border-border-default bg-border-subtle"
          style={{ gridTemplateColumns: '108px repeat(6, minmax(120px, 1fr))' }}
        >
          <div className="bg-bg-muted px-2 py-1.5 text-xs font-semibold text-text-secondary">
            コース
          </div>
          {dayDates.map((d, i) => (
            <div key={i} className="bg-bg-muted px-2 py-1.5 text-center text-xs font-medium">
              <span className="text-text-secondary">
                {d.getMonth() + 1}/{d.getDate()}
              </span>{' '}
              <span className={i >= 5 ? 'text-error' : 'text-text-secondary'}>
                （{DAY_LABEL[i]}）
              </span>
            </div>
          ))}
        </div>

        {/* コース行 */}
        {courseKeys.map((ck) => (
          <div
            key={ck}
            className="grid gap-px border-b border-border-subtle bg-border-subtle last:border-0"
            style={{ gridTemplateColumns: '108px repeat(6, minmax(120px, 1fr))' }}
          >
            <div className="flex items-center bg-bg-muted px-2 py-2 text-sm font-semibold text-text-primary">
              {courseLabel(ck)}
            </div>
            {DAY_LABEL.map((_, wd) => {
              const list = cell(ck, wd);
              return (
                <div key={wd} className="min-h-[52px] space-y-1 bg-bg-base p-1.5">
                  {list.map((r, j) => {
                    // 本体スケジュール (TimelineDayList) と同じカード視覚言語:
                    // 性別ウォッシュ地 + 性別左帯 + 角丸。Tailwind purge 回避のため
                    // 色は inline style で当てる (本体で確立済みの流儀)。sex 未設定は
                    // genderPalette(null) の中立 (砂色)。
                    const pal = genderPalette(r.patientSex);
                    return (
                      <div
                        key={`${r.patientName}-${r.startTime}-${j}`}
                        data-testid={`wsv-visit-${ck}-${wd}-${j}`}
                        className="rounded-md border border-l-[3px] px-1.5 py-1 text-[11px] leading-tight"
                        style={{
                          background: pal.bg,
                          borderColor: pal.ln,
                          borderLeftColor: pal.bar,
                          color: pal.ink,
                        }}
                      >
                        <span className="flex items-center gap-1">
                          <i
                            className="inline-block h-2 w-2 shrink-0 rounded-full"
                            style={{ background: pal.bar }}
                            aria-hidden="true"
                          />
                          <span className="font-mono tabular-nums text-text-secondary">
                            {r.startTime}
                          </span>
                          <span className="font-medium text-text-primary">{r.patientName}</span>
                        </span>
                        {r.staff1 && (
                          <div className="truncate text-text-muted" title={r.staff1}>
                            {r.staff1}
                            {r.staff2 ? `・${r.staff2}` : ''}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
