'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { Mic } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { MobileSection } from '@/components/mobile/MobileSection';
import { MobileEventChip, MobileOverrideBadge } from '@/components/mobile/MobileEventChip';
import { RakusukeNote } from '@/components/brand/Rakusuke';
import { cn } from '@/lib/utils';
import { actualTimeParts } from '@/lib/format/actualTime';
import { genderPalette } from '@/lib/scheduling/timeline';
import {
  addDays,
  currentWeekStartIso,
  todayIso,
  useMyOverrides,
  useMyStaffEvents,
  useMyVisits,
  type MyVisit,
} from '@/lib/queries/me';
import { useVisitRecordings } from '@/lib/queries/visit-recordings';
import { foldStaffEvents } from '@/lib/schedule/foldStaffEvents';
import type { EventRead } from '@/lib/schemas/staff-events';
import type { OverrideRead } from '@/lib/schemas/staff-overrides';
import { InactiveVisitBadge } from '@/components/schedule/InactiveVisitBadge';
import { classifyVisitDisplay, VISIT_DISPLAY_CLASS } from '@/lib/schedule/visitVisibility';

const WEEKDAY_LABELS = ['月', '火', '水', '木', '金', '土', '日'] as const;

/**
 * 表示する週。先週は、本人が 7 日以内の訪問の時刻を合わせられるように開く入口
 * (設計 `pc-actual-time-edit-design-2026-10-06.md` Q5)。URL の `?week=last` で覚える
 * (訪問詳細から戻ったときに先週のまま)。`useSearchParams` で読む (初回描画で
 * `window.location` を読むとサーバの描画と食い違う)。
 */
type WeekChoice = 'this' | 'last';

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
 *
 * 実績のある訪問と今日の訪問は**押せる** (設計 2026-09-30 §7-8): 訪問詳細へ進み、
 * 実績の時刻を確認して合わせられる。実績の時刻は右に出す
 * (「✓ 09:33–10:08」「到着 11:08・訪問中」/ 過ぎた日は「到着 11:08・退出なし」)。それ以外 (先の日の予定など) は
 * 従来どおり見るだけ。
 */
function VisitChip({
  visit: v,
  hasRecording,
  today,
  fromParam = 'week',
}: {
  visit: MyVisit;
  hasRecording?: boolean;
  today: string;
  /** 訪問詳細の戻り先 (`week` = 今週 / `lastweek` = 先週)。 */
  fromParam?: 'week' | 'lastweek';
}) {
  const cancelled = v.status === 'cancelled';
  const pal = genderPalette(v.patient_sex ?? null);
  // 未訪問 (no_show) は実績を出さない (一覧カード・訪問詳細と同じ扱い)。
  const actual =
    v.status === 'no_show' ? null : actualTimeParts(v.actual_arrival_at, v.actual_departure_at);
  const adjusted =
    !!v.actual_arrival_adjusted || !!v.actual_departure_adjusted || !!v.actual_departure_manual;
  const tappable = actual != null || v.visit_date === today;
  // 到着だけの訪問: 今日なら「訪問中」。「退出なし」は、退出の読み取りが無いまま
  // 過ぎた日だけ (今日の訪問中に警告色で出すと、読み忘れに見えてしまう)。
  const inProgressToday = actual != null && !actual.done && v.visit_date === today;
  const className = cn(
    // 高さは 44px 以上 (親指で押せる大きさ)。押せないチップも同じ高さで揃える。
    'flex min-h-11 items-center gap-1.5 rounded-md border border-l-[3px] px-2 py-1',
    tappable && 'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary',
    cancelled && 'opacity-50',
    // 非稼働患者の残骸 (§3-4)。
    VISIT_DISPLAY_CLASS[classifyVisitDisplay(v, { showInactive: true })],
  );
  const style = {
    background: pal.bg,
    borderColor: pal.ln,
    borderLeftColor: pal.bar,
    color: pal.ink,
  };
  const body = (
    <>
      <span className="tnum shrink-0 text-[11px] font-semibold">{shortTime(v.start_time)}</span>
      <span
        className={cn(
          'truncate text-[12px] font-bold',
          // 実績を右に出すときは、氏名の方を縮めて収める。
          actual ? 'min-w-0' : 'max-w-[55%] shrink-0',
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
      {/* 音声記録あり (訪問の音声記録 §2-3)。 */}
      {hasRecording && (
        <Mic
          className="h-3.5 w-3.5 shrink-0 opacity-70"
          aria-label="音声記録あり"
          data-testid={`this-week-recording-mark-${v.id}`}
        />
      )}
      {/* R-9d (PO要望): 縦1列化で空いた右側に住所 (名前より小さいフォント)。
          実績があるときは、その場所を実績の時刻に譲る。 */}
      {!actual && v.patient_address && (
        <span className="min-w-0 flex-1 truncate text-[10px] opacity-75">
          📍{v.patient_address}
        </span>
      )}
      {actual && (
        <span
          className={cn(
            'tnum ml-auto shrink-0 whitespace-nowrap text-[12px] font-semibold',
            // 今日の訪問中は注意ではない (これから退出する)。色はチップの文字色のまま。
            actual.done ? 'text-success' : !inProgressToday && 'text-warning-strong',
          )}
          data-testid={`this-week-actual-${v.id}`}
        >
          {actual.done
            ? `✓ ${actual.compactRange}${adjusted ? ' ✎' : ''}`
            : `到着 ${actual.arrival}・${inProgressToday ? '訪問中' : '退出なし'}`}
        </span>
      )}
    </>
  );

  if (!tappable) {
    return (
      <div className={className} style={style}>
        {body}
      </div>
    );
  }
  return (
    <Link
      href={`/m/today/${v.id}?from=${fromParam}`}
      className={className}
      style={style}
      aria-label={`${v.patient_name ?? '患者'} の訪問詳細`}
      data-testid={`this-week-visit-link-${v.id}`}
    >
      {body}
    </Link>
  );
}

/**
 * `useSearchParams` を使うので Suspense で包む (静的に描くページで、検索条件を読む部分だけ
 * クライアントで描く・Next.js の規則)。
 */
export default function MobileThisWeekPage() {
  return (
    <Suspense fallback={<Skeleton className="h-32 w-full" />}>
      <ThisWeekContent />
    </Suspense>
  );
}

function ThisWeekContent() {
  const searchParams = useSearchParams();
  const [week, setWeek] = useState<WeekChoice>(() =>
    searchParams?.get('week') === 'last' ? 'last' : 'this',
  );
  const isLast = week === 'last';
  const weekStart = isLast ? addDays(currentWeekStartIso(), -7) : currentWeekStartIso();
  const today = todayIso();
  const chooseWeek = (next: WeekChoice) => {
    setWeek(next);
    // 戻ったときに同じ週を開けるよう URL に残す (画面は遷移させない・押したときだけ)。
    const url = next === 'last' ? '/m/this-week?week=last' : '/m/this-week';
    window.history.replaceState(window.history.state, '', url);
  };
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

  // 🎙 マーク用。今週ぶんの音声記録を 1 回だけ引き、visit_id の集合で判定する
  // (`recordings_count` は API に無い・設計 §10-5)。1 週間ぶんを取り切れるよう
  // 200 件で引き (レビュー M-1)、足りなければ警告だけ出す (`fields=` は BE 未対応)。
  const { data: session } = useSession();
  const staffId = session?.user?.staffId ?? null;
  const { data: recordingList } = useVisitRecordings({
    staffId,
    from: range.from,
    to: range.to,
    limit: 200,
  });
  const recordedVisitIds = new Set(
    (recordingList?.items ?? []).map((r) => r.visit_id).filter((id): id is string => !!id),
  );
  const recordingTotal = recordingList?.total ?? 0;
  const recordingLoaded = recordingList?.items.length ?? 0;
  useEffect(() => {
    if (recordingTotal > recordingLoaded) {
      console.warn('[visit-recordings] 🎙 マークが一部欠けます (取得上限)', {
        total: recordingTotal,
        loaded: recordingLoaded,
      });
    }
  }, [recordingTotal, recordingLoaded]);

  return (
    <MobileSection
      pose="calendar"
      title={isLast ? '先週の予定' : '今週の予定'}
      subtitle={`${weekStart} 週`}
    >
      {/* 先週への切り替え (Q5)。終わった訪問の時刻を 7 日以内なら合わせられる。 */}
      <div
        role="group"
        aria-label="表示する週"
        className="inline-flex overflow-hidden rounded-lg border border-border-default"
        data-testid="this-week-switch"
      >
        {(
          [
            ['last', '先週'],
            ['this', '今週'],
          ] as const
        ).map(([value, label], i) => (
          <button
            key={value}
            type="button"
            aria-pressed={week === value}
            onClick={() => chooseWeek(value)}
            data-testid={`this-week-switch-${value}`}
            className={cn(
              'min-h-11 px-5 text-sm',
              i > 0 && 'border-l border-border-default',
              week === value
                ? 'bg-brand-primary font-bold text-white'
                : 'bg-bg-base text-text-secondary',
            )}
          >
            {label}
          </button>
        ))}
      </div>

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
            title={isLast ? '先週の訪問はありません' : '今週の訪問はありません'}
            comment="新しい予定が入ったら、ここでお知らせしますね"
          />
        </Card>
      )}

      {groups.length > 0 && (
        <p className="rounded-md border border-brand-primary-light bg-brand-primary-50 px-3 py-2 text-xs text-brand-primary-hover">
          終わった訪問を押すと、実績の時刻を確認して合わせられます
          {isLast ? '（合わせられるのは 7 日前までの訪問です）' : ''}。
        </p>
      )}

      <div className="space-y-4">
        {/* R-9b (PO要望): 今週は「見渡す」画面 — 高密度チップを敷き詰める。
            実績のある訪問と今日の訪問だけ押せる (設計 2026-09-30 §7-8)。 */}
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
                  <VisitChip
                    key={row.visit.id}
                    visit={row.visit}
                    hasRecording={recordedVisitIds.has(row.visit.id)}
                    today={today}
                    fromParam={isLast ? 'lastweek' : 'week'}
                  />
                ),
              )}
            </div>
          </section>
        ))}
      </div>
    </MobileSection>
  );
}
