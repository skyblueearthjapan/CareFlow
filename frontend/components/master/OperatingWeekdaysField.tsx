'use client';

/**
 * Phase G-45: 拠点稼働曜日 (operating_weekdays) フィールド.
 *
 * 7 個の toggle (月-日) + 「全選択 / 全解除」 ボタン.
 * 0 個チェック禁止 (= len >= 1 必須).
 */
import { useCallback, useMemo } from 'react';

import { Button } from '@/components/ui/button';

export const WEEKDAY_OPTIONS = [
  { value: 0, label: '月' },
  { value: 1, label: '火' },
  { value: 2, label: '水' },
  { value: 3, label: '木' },
  { value: 4, label: '金' },
  { value: 5, label: '土' },
  { value: 6, label: '日' },
] as const;

export const ALL_WEEKDAYS: number[] = WEEKDAY_OPTIONS.map((w) => w.value);

export interface OperatingWeekdaysFieldProps {
  value: number[];
  onChange: (next: number[]) => void;
  disabled?: boolean;
  /** 0 個チェック時の警告メッセージ表示用 (= form 全体の validation と独立). */
  error?: string | null;
  /** test 識別子の prefix (= 複数フォームで衝突回避). */
  testIdPrefix?: string;
}

export function OperatingWeekdaysField({
  value,
  onChange,
  disabled,
  error,
  testIdPrefix = 'operating-weekdays',
}: OperatingWeekdaysFieldProps) {
  const selectedSet = useMemo(() => new Set(value), [value]);

  const toggleWeekday = useCallback(
    (wd: number) => {
      if (disabled) return;
      const next = new Set(selectedSet);
      if (next.has(wd)) {
        next.delete(wd);
      } else {
        next.add(wd);
      }
      // 0..6 昇順で安定化.
      onChange(
        Array.from(next)
          .filter((v) => v >= 0 && v <= 6)
          .sort((a, b) => a - b),
      );
    },
    [disabled, onChange, selectedSet],
  );

  const handleSelectAll = useCallback(() => {
    if (disabled) return;
    onChange([...ALL_WEEKDAYS]);
  }, [disabled, onChange]);

  const handleClearAll = useCallback(() => {
    if (disabled) return;
    onChange([]);
  }, [disabled, onChange]);

  const isAllSelected = value.length === ALL_WEEKDAYS.length;
  const showEmptyWarning = value.length === 0;

  return (
    <div className="space-y-2" data-testid={testIdPrefix}>
      <div
        role="group"
        aria-label="稼働曜日"
        className="flex flex-wrap gap-1.5"
        data-testid={`${testIdPrefix}-toggles`}
      >
        {WEEKDAY_OPTIONS.map((opt) => {
          const checked = selectedSet.has(opt.value);
          return (
            <button
              key={opt.value}
              type="button"
              role="checkbox"
              aria-checked={checked}
              aria-label={`${opt.label}曜`}
              data-testid={`${testIdPrefix}-toggle-${opt.value}`}
              data-state={checked ? 'checked' : 'unchecked'}
              onClick={() => toggleWeekday(opt.value)}
              disabled={disabled}
              className={
                'min-w-[40px] rounded border px-2.5 py-1.5 text-sm font-semibold transition-colors ' +
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-primary focus-visible:ring-offset-1 ' +
                'disabled:cursor-not-allowed disabled:opacity-50 ' +
                (checked
                  ? 'border-brand-primary bg-brand-primary text-white hover:bg-brand-primary/90'
                  : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted')
              }
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleSelectAll}
          disabled={disabled || isAllSelected}
          data-testid={`${testIdPrefix}-select-all`}
        >
          全選択
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleClearAll}
          disabled={disabled || value.length === 0}
          data-testid={`${testIdPrefix}-clear-all`}
        >
          全解除
        </Button>
      </div>

      {(error || showEmptyWarning) && (
        <p className="text-xs text-status-error" role="alert" data-testid={`${testIdPrefix}-error`}>
          {error ?? '稼働曜日は最低 1 日選択してください'}
        </p>
      )}
    </div>
  );
}
