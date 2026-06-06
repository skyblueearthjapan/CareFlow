'use client';

/**
 * スライダー + 数値入力の複合フィールド — Phase G-88 Step4.
 *
 * 範囲もの (バッファー / 速度 / 昼休み長 / 定員) の共通操作部品。
 * ネイティブ `<input type="range">` を Tailwind で整え、横に数値入力を併置する。
 * デザインはメインアプリの言語 (text-text-primary 等) に合わせる。
 */

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

export interface SliderFieldProps {
  id: string;
  label: string;
  /** 平易な一言説明。 */
  description?: string;
  /** ライブ表示 (例: 速度→「1kmあたり約3分」)。 */
  liveHint?: React.ReactNode;
  value: number;
  min: number;
  max: number;
  step: number;
  /** 数値末尾の単位 (分 / km/h / 人 等)。 */
  unit?: string;
  /** is_default を使った「既定」バッジ。 */
  isDefault?: boolean;
  onChange: (next: number) => void;
  disabled?: boolean;
}

export function SliderField({
  id,
  label,
  description,
  liveHint,
  value,
  min,
  max,
  step,
  unit,
  isDefault,
  onChange,
  disabled,
}: SliderFieldProps) {
  const clamp = (n: number) => Math.min(max, Math.max(min, n));

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id} className="text-sm font-medium text-text-primary">
          {label}
        </Label>
        {isDefault && (
          <span className="shrink-0 rounded-full bg-bg-muted px-2 py-0.5 text-[10px] font-medium text-text-muted">
            既定
          </span>
        )}
      </div>
      {description && <p className="text-xs text-text-secondary">{description}</p>}

      <div className="flex items-center gap-3">
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(clamp(Number(e.target.value)))}
          aria-label={label}
          className={cn(
            'h-2 flex-1 cursor-pointer appearance-none rounded-full bg-bg-muted accent-brand-primary',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        />
        <div className="flex items-center gap-1">
          <Input
            type="number"
            inputMode="numeric"
            min={min}
            max={max}
            step={step}
            value={value}
            disabled={disabled}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n)) onChange(clamp(n));
            }}
            aria-label={`${label} 数値入力`}
            data-testid={`${id}-number`}
            className="h-9 w-20 text-right tabular-nums"
          />
          {unit && <span className="text-sm text-text-muted">{unit}</span>}
        </div>
      </div>

      {liveHint && <p className="text-xs text-brand-primary">{liveHint}</p>}
    </div>
  );
}
