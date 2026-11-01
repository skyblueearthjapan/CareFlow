'use client';

/**
 * やることの項目を選ぶチップ (いくつでも選べる)。訪問モニターの詳細と、
 * やることのページの「＋ やることを付ける」で使う。項目は事務所が設定で足す
 * (事業所の項目名はコードに持たない・設計 §4)。
 */
import { Check } from 'lucide-react';

import { cn } from '@/lib/utils';
import type { TaskItemTypeRead } from '@/lib/schemas/patient-task';

interface TaskItemChipsProps {
  items: readonly TaskItemTypeRead[];
  selected: readonly string[];
  onToggle: (id: string) => void;
  disabled?: boolean;
}

export function TaskItemChips({ items, selected, onToggle, disabled }: TaskItemChipsProps) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-text-muted" data-testid="task-item-chips-empty">
        項目がまだありません。やることのページの「項目の設定」で足せます。伝言だけでも付けられます。
      </p>
    );
  }
  return (
    <div className="flex flex-wrap gap-2" data-testid="task-item-chips">
      {items.map((it) => {
        const on = selected.includes(it.id);
        return (
          <button
            key={it.id}
            type="button"
            aria-pressed={on}
            disabled={disabled}
            onClick={() => onToggle(it.id)}
            className={cn(
              'inline-flex min-h-9 items-center gap-1 rounded-full border px-3 py-1.5 text-sm transition-colors disabled:opacity-50',
              on
                ? 'border-brand-primary bg-brand-primary-light font-semibold text-brand-primary-hover'
                : 'border-border-default bg-bg-base text-text-secondary hover:bg-bg-muted',
            )}
          >
            {on && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
            {it.name}
          </button>
        );
      })}
    </div>
  );
}

/** 付けられるか (項目か伝言のどちらかが要る・BE の検証と同じ)。 */
export function canSubmitTask(itemTypeIds: readonly string[], memo: string): boolean {
  return itemTypeIds.length > 0 || memo.trim().length > 0;
}

/** チップの選択を切り替えた配列を返す。 */
export function toggleId(list: readonly string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}
