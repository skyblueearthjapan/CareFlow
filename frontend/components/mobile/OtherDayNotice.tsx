/**
 * スマホで「今日以外の日」を見ているときの注意 (mobile-future-days・PO 2026-11-01)。
 *
 * 先の日の訪問は見るだけ (QR・打刻・音声記録などは出さない)。取り違えを防ぐため、
 * 今日以外を見ている間は色の付いた帯で「M/D（曜）の予定です。今日ではありません」と出す。
 *
 * 見られる範囲は **先週の月曜 〜 来週の日曜** (backend も staff は来週の日曜までしか返さない)。
 */
import { AlertTriangle, CalendarClock } from 'lucide-react';

import { cn } from '@/lib/utils';

const WEEKDAY_LABELS = ['日', '月', '火', '水', '木', '金', '土'] as const;

function parseLocal(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function toIso(dt: Date): string {
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(
    dt.getDate(),
  ).padStart(2, '0')}`;
}

/** ローカル日付だけで日を足す (toISOString を使わない・UTC のずれを避ける)。 */
export function shiftIsoDate(iso: string, days: number): string {
  const dt = parseLocal(iso);
  dt.setDate(dt.getDate() + days);
  return toIso(dt);
}

/** "2026-11-03" → "11/3（火）"。 */
export function mobileDayLabel(iso: string): string {
  const dt = parseLocal(iso);
  return `${dt.getMonth() + 1}/${dt.getDate()}（${WEEKDAY_LABELS[dt.getDay()]}）`;
}

/** スマホで見られる日の範囲 = 先週の月曜 〜 来週の日曜 (どちらも含む)。 */
export function mobileViewBounds(today: string): { min: string; max: string } {
  const dow = parseLocal(today).getDay(); // 0 = 日
  const monday = shiftIsoDate(today, dow === 0 ? -6 : 1 - dow);
  return { min: shiftIsoDate(monday, -7), max: shiftIsoDate(monday, 13) };
}

/** 範囲の外・形の崩れた日付は今日に寄せる (URL を手で書き換えられても範囲を超えない)。 */
export function clampMobileDate(raw: string | null | undefined, today: string): string {
  if (!raw || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return today;
  const { min, max } = mobileViewBounds(today);
  if (raw < min || raw > max) return today;
  return raw;
}

/** 今日以外を見ているときの帯。先の日は注意の色、過ぎた日は落ち着いた色。 */
export function OtherDayNotice({
  date,
  today,
  className,
  testId = 'other-day-notice',
}: {
  date: string;
  today: string;
  className?: string;
  testId?: string;
}) {
  if (date === today) return null;
  const future = date > today;
  const Icon = future ? AlertTriangle : CalendarClock;
  return (
    <div
      role="status"
      className={cn(
        'flex items-center gap-2 rounded-md border-2 px-3 py-2.5 text-[15px] font-bold',
        future
          ? 'border-warning-strong bg-warning-bg text-warning-strong'
          : 'border-info-strong bg-info-bg text-info-strong',
        className,
      )}
      data-testid={testId}
      data-tone={future ? 'future' : 'past'}
    >
      <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
      <span>{mobileDayLabel(date)}の予定です。今日ではありません</span>
    </div>
  );
}
