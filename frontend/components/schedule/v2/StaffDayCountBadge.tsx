'use client';

/**
 * 職員の 1 日の件数の札 (schedule-unit-switch-design-2026-10-21.md D6〜D8)。
 * 職員リストのセルの先頭と、職員タイムラインの氏名の横に出す。数え方は
 * `lib/schedule/staffDayCount.ts` が唯一の出所。
 */
import { cn } from '@/lib/utils';
import {
  dayCountTone,
  formatDayCount,
  type DayCountTone,
  type StaffDayCount,
} from '@/lib/schedule/staffDayCount';

const TONE_CLASS: Record<DayCountTone, string> = {
  plain: 'border-border-default bg-bg-muted text-text-secondary',
  normal: 'border-brand-primary-light bg-brand-primary-50 text-brand-primary-hover',
  full: 'border-border-warning bg-warning-bg text-warning-strong',
  over: 'border-border-error bg-error-bg text-error',
};

const TONE_SUFFIX: Record<DayCountTone, string> = {
  plain: '',
  normal: '',
  full: ' 満杯',
  over: ' 超過',
};

export interface StaffDayCountBadgeProps {
  count: StaffDayCount | undefined;
  /** 設定の「1コースの最大人数」。null = 読めない → 色を付けない。 */
  limit: number | null;
  /** 「（担当なし）」行。上限ではなく「担当なし N件」(N>0 でアンバー)。 */
  unassigned?: boolean;
  testId: string;
}

export function StaffDayCountBadge({ count, limit, unassigned, testId }: StaffDayCountBadgeProps) {
  const c = count ?? { visits: 0, patients: 0, companions: 0 };
  const tone: DayCountTone = unassigned
    ? c.visits > 0
      ? 'full'
      : 'plain'
    : dayCountTone(c.visits, limit);
  const text = unassigned ? `担当なし ${c.visits}件` : `${formatDayCount(c)}${TONE_SUFFIX[tone]}`;
  const title = unassigned
    ? `担当の決まっていない訪問 ${c.visits}件（取消は数えません）`
    : `この日の訪問 ${formatDayCount(c)}${limit != null ? `（上限 ${limit}件）` : ''}・取消は数えません` +
      (c.companions > 0 ? `・同行 ${c.companions}件は別` : '');
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid={testId}>
      <span
        className={cn(
          'tnum inline-flex items-center rounded-full border px-1.5 py-px text-[11px] font-bold leading-tight',
          TONE_CLASS[tone],
        )}
        title={title}
        data-tone={tone}
      >
        {text}
      </span>
      {c.companions > 0 ? (
        <span
          className="tnum text-[11px] text-text-muted"
          title="同行・2 名体制の 2 人目（件数には入れません）"
          data-testid={`${testId}-companions`}
        >
          ＋同行 {c.companions}
        </span>
      ) : null}
    </span>
  );
}
