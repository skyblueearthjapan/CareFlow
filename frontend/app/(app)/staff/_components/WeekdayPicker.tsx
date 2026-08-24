'use client';

/**
 * 曜日の複数選択チップ + ショートカット
 * (staff-event-history-design.md §2 Phase 3 / docs/mockups/event-defaults-bulk-mock.html 変更A)。
 *
 * 固定イベントの曜日は月〜土の 0..5 (staff_event_defaults.weekday と同じ番号)。
 * 一括登録ダイアログ・スタッフ詳細の固定イベント追加/曜日編集の 3 箇所で使う。
 */
import { cn } from '@/lib/utils';

/** weekday 0=月 … 5=土 (staff_event_defaults の採番)。 */
export const WEEKDAY_LABELS_MON_SAT = ['月', '火', '水', '木', '金', '土'] as const;

/** 月〜土。 */
export const WEEKDAYS_ALL: number[] = [0, 1, 2, 3, 4, 5];
/** 月〜金。 */
export const WEEKDAYS_MON_FRI: number[] = [0, 1, 2, 3, 4];

/** 選択済み曜日を昇順の表示ラベルへ (「月火水」)。 */
export function weekdayLabels(weekdays: readonly number[]): string[] {
  return [...weekdays]
    .filter((w) => w >= 0 && w < WEEKDAY_LABELS_MON_SAT.length)
    .sort((a, b) => a - b)
    .map((w) => WEEKDAY_LABELS_MON_SAT[w]!);
}

/**
 * JS の `Date#getDay()` (0=日) → 固定イベントの weekday (0=月)。
 * 日曜は固定イベントの対象外なので null を返す。
 */
export function toEventDefaultWeekday(jsDay: number): number | null {
  if (jsDay === 0) return null;
  return jsDay - 1;
}

export interface WeekdayPickerProps {
  value: number[];
  onChange: (next: number[]) => void;
  /** チップ・ショートカットの data-testid 接頭辞 (テストと複数設置時の識別用)。 */
  idPrefix: string;
  disabled?: boolean;
}

export function WeekdayPicker({ value, onChange, idPrefix, disabled }: WeekdayPickerProps) {
  const toggle = (w: number) => {
    onChange(value.includes(w) ? value.filter((x) => x !== w) : [...value, w].sort((a, b) => a - b));
  };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="曜日 (複数選択)">
        {WEEKDAY_LABELS_MON_SAT.map((label, w) => {
          const active = value.includes(w);
          return (
            <button
              key={label}
              type="button"
              disabled={disabled}
              aria-pressed={active}
              data-testid={`${idPrefix}-weekday-${w}`}
              onClick={() => toggle(w)}
              className={cn(
                'h-8 min-w-[44px] rounded-full border px-3 text-sm transition-colors',
                active
                  ? 'border-transparent bg-brand-primary font-bold text-white'
                  : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
                disabled && 'opacity-50',
              )}
            >
              {label}
            </button>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-1.5 text-xs">
        <button
          type="button"
          disabled={disabled}
          data-testid={`${idPrefix}-shortcut-all`}
          onClick={() => onChange([...WEEKDAYS_ALL])}
          className="rounded-md border border-border-default px-2 py-1 text-text-secondary hover:bg-bg-muted"
        >
          毎日(月〜土)
        </button>
        <button
          type="button"
          disabled={disabled}
          data-testid={`${idPrefix}-shortcut-weekdays`}
          onClick={() => onChange([...WEEKDAYS_MON_FRI])}
          className="rounded-md border border-border-default px-2 py-1 text-text-secondary hover:bg-bg-muted"
        >
          月〜金
        </button>
        <button
          type="button"
          disabled={disabled}
          data-testid={`${idPrefix}-shortcut-clear`}
          onClick={() => onChange([])}
          className="rounded-md border border-border-default px-2 py-1 text-text-secondary hover:bg-bg-muted"
        >
          クリア
        </button>
      </div>
    </div>
  );
}
