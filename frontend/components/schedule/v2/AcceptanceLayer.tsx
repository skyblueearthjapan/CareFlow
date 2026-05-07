'use client';

/**
 * AcceptanceLayer — 受入目安レイヤー (W15-FE D-2).
 *
 * セル背景色 + 右肩バッジで受入目安 (○ / △ / ×) を表示する。
 *
 * - データソース: useAcceptanceCalendar (office_id ごとにキャッシュ)
 * - 表示単位: weekday × HH:00 (1 時間刻み)
 *
 * 本ファイルでは:
 *   1. AcceptanceBadge: セル右肩に出すバッジ
 *   2. acceptanceCellClass: セル背景の Tailwind class を返す helper
 *   3. AcceptanceLegend: フッター凡例
 */
import { Card } from '@/components/ui/card';
import {
  ACCEPTANCE_STATUS_LABELS,
  ACCEPTANCE_STATUS_LONG_LABELS,
  type AcceptanceStatus,
} from '@/lib/schemas/v2/acceptance';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Cell background helper
// ---------------------------------------------------------------------------

/**
 * セル背景に乗せる Tailwind class.
 * available: 無着色 / consult: 薄黄 / unavailable: 薄赤
 */
export function acceptanceCellClass(status: AcceptanceStatus | undefined | null): string {
  switch (status) {
    case 'consult':
      return 'bg-warning/10';
    case 'unavailable':
      return 'bg-error/10';
    case 'available':
    default:
      return '';
  }
}

// ---------------------------------------------------------------------------
// Right-shoulder badge
// ---------------------------------------------------------------------------

export interface AcceptanceBadgeProps {
  status: AcceptanceStatus | undefined | null;
}

export function AcceptanceBadge({ status }: AcceptanceBadgeProps) {
  if (!status) return null;
  const tone =
    status === 'available'
      ? 'bg-success/15 text-success border-success/40'
      : status === 'consult'
        ? 'bg-warning/15 text-warning border-warning/40'
        : 'bg-error/15 text-error border-error/40';
  return (
    <span
      className={cn(
        'pointer-events-none absolute right-0.5 top-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full border text-[10px] font-bold leading-none',
        tone,
      )}
      title={ACCEPTANCE_STATUS_LONG_LABELS[status]}
      aria-label={`受入目安: ${ACCEPTANCE_STATUS_LONG_LABELS[status]}`}
      data-acceptance-status={status}
    >
      {ACCEPTANCE_STATUS_LABELS[status]}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Legend (footer)
// ---------------------------------------------------------------------------

export function AcceptanceLegend() {
  const items: AcceptanceStatus[] = ['available', 'consult', 'unavailable'];
  return (
    <Card className="flex flex-wrap items-center gap-3 p-2 text-[11px] text-text-secondary">
      <span className="font-semibold">受入目安:</span>
      {items.map((s) => (
        <span key={s} className="flex items-center gap-1">
          <span
            className={cn(
              'inline-flex h-4 w-4 items-center justify-center rounded-full border text-[10px] font-bold',
              s === 'available'
                ? 'bg-success/15 text-success border-success/40'
                : s === 'consult'
                  ? 'bg-warning/15 text-warning border-warning/40'
                  : 'bg-error/15 text-error border-error/40',
            )}
          >
            {ACCEPTANCE_STATUS_LABELS[s]}
          </span>
          <span>{ACCEPTANCE_STATUS_LONG_LABELS[s]}</span>
        </span>
      ))}
    </Card>
  );
}
