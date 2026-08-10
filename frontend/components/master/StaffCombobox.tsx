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

interface StaffComboboxProps {
  value: string;
  onChange: (id: string) => void;
  /** Soft-deleted rows are filtered out automatically. Pass a staff id here
   *  to also remove that single row (e.g. mentor ≠ self). */
  excludeId?: string;
  /** 複数除外 (例: NG スタッフで既に追加済みの staff を候補から外す). */
  excludeIds?: readonly string[];
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function StaffCombobox({
  value,
  onChange,
  excludeId,
  excludeIds,
  disabled,
  placeholder = 'スタッフを選択',
  className,
}: StaffComboboxProps) {
  const { data: staff, isLoading } = useStaffList({ limit: 500 });

  const excluded = React.useMemo(
    () => new Set<string>([...(excludeIds ?? []), ...(excludeId ? [excludeId] : [])]),
    [excludeIds, excludeId],
  );

  const options = React.useMemo<ComboboxOption[]>(
    () =>
      (staff ?? [])
        .filter((s) => !s.deleted_at && !excluded.has(s.id))
        .map((s) => ({
          value: s.id,
          label: s.code ? `${s.name} (${s.code})` : s.name,
        })),
    [staff, excluded],
  );

  return (
    <Combobox
      options={options}
      value={value || undefined}
      onChange={(v) => onChange(v ?? '')}
      placeholder={isLoading ? '読み込み中…' : placeholder}
      searchPlaceholder="氏名 / コードで検索"
      emptyText="スタッフが見つかりません"
      disabled={disabled || isLoading}
      className={className}
    />
  );
}
