'use client';

/**
 * Staff picker — Wave 4-E.
 *
 * Wraps the shared `Combobox` primitive and feeds it from `useStaffList()`.
 * `excludeId` lets the mentor field exclude the staff member being edited so
 * a person cannot be picked as their own mentor.
 */
import * as React from 'react';

import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import { useStaffList } from '@/lib/queries/staff';

/** 「選択解除」擬似オプションの内部値 (実在の staff id と衝突しない)。 */
const CLEAR_VALUE = '__clear__';

interface StaffComboboxProps {
  value: string;
  onChange: (id: string) => void;
  /** Soft-deleted rows are filtered out automatically. Pass a staff id here
   *  to also remove that single row (e.g. mentor ≠ self). */
  excludeId?: string;
  /** 複数除外 (例: NG スタッフで既に追加済みの staff を候補から外す). */
  excludeIds?: readonly string[];
  /**
   * 指定すると先頭に「選択解除」オプションを出す (選ぶと onChange('') が飛ぶ)。
   * 例: '― 全員（絞り込みなし）'。絞り込み用途で「全員に戻す」導線が要るとき用。
   */
  clearLabel?: string;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function StaffCombobox({
  value,
  onChange,
  excludeId,
  excludeIds,
  clearLabel,
  disabled,
  placeholder = 'スタッフを選択',
  className,
}: StaffComboboxProps) {
  const { data: staff, isLoading } = useStaffList({ limit: 500 });

  const excluded = React.useMemo(
    () => new Set<string>([...(excludeIds ?? []), ...(excludeId ? [excludeId] : [])]),
    [excludeIds, excludeId],
  );

  const options = React.useMemo<ComboboxOption[]>(() => {
    const list = (staff ?? [])
      .filter((s) => !s.deleted_at && !excluded.has(s.id))
      .map((s) => ({
        value: s.id,
        label: s.code ? `${s.name} (${s.code})` : s.name,
      }));
    return clearLabel ? [{ value: CLEAR_VALUE, label: clearLabel }, ...list] : list;
  }, [staff, excluded, clearLabel]);

  return (
    <Combobox
      options={options}
      value={value || undefined}
      onChange={(v) => onChange(!v || v === CLEAR_VALUE ? '' : v)}
      placeholder={isLoading ? '読み込み中…' : placeholder}
      searchPlaceholder="氏名 / コードで検索"
      emptyText="スタッフが見つかりません"
      disabled={disabled || isLoading}
      className={className}
    />
  );
}
