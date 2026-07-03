'use client';

/**
 * ChangeScopeChoice — 反映先の2択共通部品（設計書 §2.4）。
 *
 * **全変更操作の出口を統一する共通部品（設計書 §2.4）。操作ごとに独自の選択UIを作らないこと。**
 *
 * A「固定訪問週間に登録」: 毎週の型が変わります。今週のスケジュールにもすぐ反映されます
 * B「この週だけ反映」: 今週のみ変更します。毎週の型は変わりません（来週以降は従来どおり）
 *
 * 使用方法:
 *   <ChangeScopeChoice value={scopeChoice} onChange={setScopeChoice} />
 *
 * コンパクト設計: ダイアログ内への埋め込みを前提とし、縦幅を最小限に抑える。
 */
import * as React from 'react';

export type ChangeScopeValue = 'pattern' | 'week';

export interface ChangeScopeChoiceProps {
  /** 現在の選択 ('pattern' = A: 固定訪問週間に登録 / 'week' = B: この週だけ). */
  value: ChangeScopeValue;
  onChange: (v: ChangeScopeValue) => void;
  disabled?: boolean;
  /** 既定値についての補足注記 (省略可). コンパクト表示のため 1 行以内を想定. */
  defaultNote?: string;
}

export function ChangeScopeChoice({
  value,
  onChange,
  disabled,
  defaultNote,
}: ChangeScopeChoiceProps) {
  return (
    <div data-testid="change-scope-choice" className="space-y-1.5">
      {/* A: 固定訪問週間に登録 */}
      <button
        type="button"
        role="radio"
        aria-checked={value === 'pattern'}
        data-testid="change-scope-pattern"
        disabled={disabled}
        onClick={() => onChange('pattern')}
        className={[
          'w-full rounded border px-3 py-2 text-left transition-colors',
          value === 'pattern'
            ? 'border-brand-primary bg-brand-primary/5'
            : 'border-border-default bg-bg-base hover:border-brand-primary/50',
          disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
        ].join(' ')}
      >
        <div className="flex items-start gap-2">
          <span
            className={[
              'mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border-2',
              value === 'pattern' ? 'border-brand-primary' : 'border-border-default',
            ].join(' ')}
          >
            {value === 'pattern' ? (
              <span className="h-1.5 w-1.5 rounded-full bg-brand-primary" />
            ) : null}
          </span>
          <div>
            <div className="text-xs font-semibold text-text-primary">固定訪問週間に登録</div>
            <div className="text-[11px] text-text-muted">
              毎週の型が変わります。今週のスケジュールにもすぐ反映されます
            </div>
          </div>
        </div>
      </button>

      {/* B: この週だけ反映 */}
      <button
        type="button"
        role="radio"
        aria-checked={value === 'week'}
        data-testid="change-scope-week"
        disabled={disabled}
        onClick={() => onChange('week')}
        className={[
          'w-full rounded border px-3 py-2 text-left transition-colors',
          value === 'week'
            ? 'border-brand-primary bg-brand-primary/5'
            : 'border-border-default bg-bg-base hover:border-brand-primary/50',
          disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
        ].join(' ')}
      >
        <div className="flex items-start gap-2">
          <span
            className={[
              'mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border-2',
              value === 'week' ? 'border-brand-primary' : 'border-border-default',
            ].join(' ')}
          >
            {value === 'week' ? (
              <span className="h-1.5 w-1.5 rounded-full bg-brand-primary" />
            ) : null}
          </span>
          <div>
            <div className="text-xs font-semibold text-text-primary">この週だけ反映</div>
            <div className="text-[11px] text-text-muted">
              今週のみ変更します。毎週の型は変わりません（来週以降は従来どおり）
            </div>
          </div>
        </div>
      </button>

      {defaultNote ? (
        <div className="text-[11px] text-text-muted">{defaultNote}</div>
      ) : null}
    </div>
  );
}
