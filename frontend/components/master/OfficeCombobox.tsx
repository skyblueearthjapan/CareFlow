'use client';

/**
 * Office (拠点) picker — Wave 4-E.
 *
 * Thin wrapper around the shared `Combobox` primitive. Loads the office
 * master via `useOffices()` and presents `name (address)` as the searchable
 * label so callers can replace raw UUID inputs everywhere a primary_office_id
 * is collected.
 */
import * as React from 'react';

import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import { useOffices } from '@/lib/queries/offices';

interface OfficeComboboxProps {
  /** Selected office UUID, or '' when nothing is selected. */
  value: string;
  /** Fired with the new UUID (empty string preserved as ''). */
  onChange: (id: string) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function OfficeCombobox({
  value,
  onChange,
  disabled,
  placeholder = '拠点を選択',
  className,
}: OfficeComboboxProps) {
  // Generous limit covers every pilot deployment without paging.
  const { offices, isLoading } = useOffices({ limit: 500 });

  const options = React.useMemo<ComboboxOption[]>(
    () =>
      (offices ?? [])
        .filter((o) => !o.deleted_at)
        .map((o) => ({
          value: o.id,
          label: o.address ? `${o.name} (${o.address})` : o.name,
        })),
    [offices],
  );

  return (
    <Combobox
      options={options}
      value={value || undefined}
      onChange={(v) => onChange(v ?? '')}
      placeholder={isLoading ? '読み込み中…' : placeholder}
      searchPlaceholder="拠点名 / 住所で検索"
      emptyText="拠点が見つかりません"
      disabled={disabled || isLoading}
      className={className}
    />
  );
}
