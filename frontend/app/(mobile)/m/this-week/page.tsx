'use client';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { MobileSection } from '@/components/mobile/MobileSection';
import { MobileEventChip, MobileOverrideBadge } from '@/components/mobile/MobileEventChip';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { cn } from '@/lib/utils';
import { genderPalette } from '@/lib/scheduling/timeline';
import {
  addDays,
  currentWeekStartIso,
  useMyOverrides,
  useMyStaffEvents,
  useMyVisits,
  type MyVisit,
} from '@/lib/queries/me';
import { foldStaffEvents } from '@/lib/schedule/foldStaffEvents';
import type { EventRead } from '@/lib/schemas/staff-events';
import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import { InactiveVisitBadge } from '@/components/schedule/InactiveVisitBadge';
import { classifyVisitDisplay, VISIT_DISPLAY_CLASS } from '@/lib/schedule/visitVisibility';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/** 1 日の中身 = 訪問とイベントを開始時刻順に混ぜた行 (design §3 C-3)。 */
type DayRow =
  | { kind: 'visit'; at: string; visit: MyVisit }
  | { kind: 'event'; at: string; event: EventRead };

interface DayGroup {
  date: string;
  rows: DayRow[];
  /** 見出し右の件数は **訪問の件数** (イベントは数えない)。 */
  visitCount: number;
  override: OverrideRead | null;
}

/** "HH:MM:SS" も "HH:MM" も HH:MM に揃える (訪問とイベントで桁が違うため)。 */
function sortKey(t: string): string {
  return t.length >= 5 ? t.slice(0, 5) : t;
}

/**
 * 訪問 + イベントを日付ごとにまとめ、日付昇順 / 開始時刻昇順に並べる。
 * 休み・時間変更 (override) はその日付の見出しに添える。
 */
function groupByDate(
  visits: MyVisit[],
  events: EventRead[],
  overrides: OverrideRead[],
): DayGroup[] {
  const map = new Map<string, DayRow[]>();
  const push = (date: string, row: DayRow) => {
    const list = map.get(date) ?? [];
    list.push(row);
    map.set(date, list);
  };

  for (const v of visits) {
    // 患者ステータス連動の取消 (source='status_cancel') は一覧から消す
    // (design 2026-09-09 §7-4)。「今週だけ取消」= manual_cancel は打ち消し線で残す。
    if (classifyVisitDisplay(v) === 'hidden') continue;
    push(v.visit_date, { kind: 'visit', at: sortKey(v.start_time), visit: v });
  }
  for (const e of events) {
    push(e.date, { kind: 'event', at: sortKey(e.start_time), event: e });
  }

  const overrideByDate = new Map<string, OverrideRead>();
  for (const o of overrides) {
    if (!overrideByDate.has(o.date)) overrideByDate.set(o.date, o);
    // 休みの日は予定が 0 件でも「その日は休み」と分かるよう見出しだけ出す。
    if (!map.has(o.date)) map.set(o.date, []);
  }

  return Array.from(map.entries())
    .map(([date, rows]) => ({
      date,
      // 同時刻なら訪問を先に (現場の主役は訪問)。
      rows: rows.sort(
        (a, b) => a.at.localeCompare(b.at) || (a.kind === b.kind ? 0 : a.kind === 'visit' ? -1 : 1),
      ),
      visitCount: rows.filter((r) => r.kind === 'visit').length,
      override: overrideByDate.get(date) ?? null,
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

/**
 * 訪問 1 件のチップ (R-9b/c/d の意匠そのまま・時系列に混ぜるため関数へ切り出し)。
 *
 * 「今週だけ取消」(`status='cancelled'` で `classifyVisitDisplay` が hidden に
 * しないもの = manual_cancel) は、打消線に加えて赤「取消」バッジを出す
 * (design §3 C-3 — 薄いだけだと現場が見落とす)。
 */
function VisitChip({ visit: v }: { visit: MyVisit }) {
  const cancelled = v.status === 'cancelled';
  const pal = genderPalette(v.patient_sex ?? null);
  return (
    <div
      className={cn(
        'flex items-center gap-1.5 rounded-md border border-l-[3px] px-1.5 py-1',
        cancelled && 'opacity-50',
        // 非稼働患者の残骸 (§3-4)。
        VISIT_DISPLAY_CLASS[classifyVisitDisplay(v, { showInactive: true })],
      )}
      style={{
        background: pal.bg,
        borderColor: pal.ln,
        borderLeftColor: pal.bar,
        color: pal.ink,
      }}
    >
      <span className="tnum shrink-0 text-[11px] font-semibold">{shortTime(v.start_time)}</span>
      <span
        className={cn(
          'max-w-[55%] shrink-0 truncate text-[12px] font-bold',
          cancelled && 'line-through',
        )}
      >
        {v.patient_name ?? '—'}
      </span>
      {cancelled && (
        <span
          data-testid={`this-week-cancelled-badge-${v.id}`}
          className="shrink-0 rounded border border-error/40 bg-error/10 px-1 text-[10px] font-semibold text-error"
        >
          取消
        </span>
      )}
      {/* 非稼働患者のバッジ (「入院中」等・§3-4)。 */}
      <InactiveVisitBadge
        visit={v}
        kind={classifyVisitDisplay(v, { showInactive: true })}
        testId={`this-week-inactive-badge-${v.id}`}
      />
      {/* R-9d (PO要望): 縦1列化で空いた右側に住所 (名前より小さいフォント)。 */}
      {v.patient_address && (
        <span className="min-w-0 flex-1 truncate text-[10px] opacity-75">
          📍{v.patient_address}
        </span>
      )}
    </div>
  );
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

  // 職員イベント / 休み・時間変更も同じ窓 (月〜日) で取る。**補助情報**なので
  // 取得に失敗しても Alert は出さず、訪問だけ静かに描く (design §3 C-3)。
  const range = { from: weekStart, to: addDays(weekStart, 6) };
  const { data: events } = useMyStaffEvents(range);
  const { data: overrides } = useMyOverrides(range);

  const groups = groupByDate(visits ?? [], foldStaffEvents(events ?? []), overrides ?? []);

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
            <header className="mb-1.5 flex items-baseline justify-between gap-2">
              <h2 className="font-serif text-base font-bold text-text-primary">
                {formatDateLabel(g.date)}
              </h2>
              <div className="flex items-baseline gap-1.5">
                {/* 休み / 時間変更 (design §3 C-3)。 */}
                {g.override && (
                  <MobileOverrideBadge
                    override={g.override}
                    testId={`this-week-override-${g.date}`}
                  />
                )}
                {g.visitCount > 0 && (
                  <span className="text-xs text-text-muted">{g.visitCount}件</span>
                )}
              </div>
            </header>
            {/* R-9c (PO決定): 2列→縦1列。イベント (緑) は時系列で訪問に混ざる。 */}
            <div className="grid grid-cols-1 gap-1.5">
              {g.rows.map((row) =>
                row.kind === 'event' ? (
                  <MobileEventChip key={`ev-${row.event.id}`} event={row.event} />
                ) : (
                  <VisitChip key={row.visit.id} visit={row.visit} />
                ),
              )}
            </div>
          </section>
        ))}
      </div>
    </MobileSection>
  );
}
