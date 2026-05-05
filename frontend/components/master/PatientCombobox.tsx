'use client';

/**
 * Patient picker — Wave 4-E.
 *
 * Wraps the shared `Combobox` primitive over `usePatients()`. The patients
 * list endpoint paginates client-side, so we ask for the first hard-cap
 * window (500 rows) — sufficient for the pilot footprint.
 */
import * as React from 'react';

import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import { usePatients } from '@/lib/queries/patients';

interface PatientComboboxProps {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

export function PatientCombobox({
  value,
  onChange,
  disabled,
  placeholder = '患者を選択',
  className,
}: PatientComboboxProps) {
  // `limit` is the page size for the client wrapper; 500 matches the backend
  // hard cap and the wrapper's internal fetch window.
  const { data, isLoading } = usePatients({ page: 1, limit: 500 });

  const options = React.useMemo<ComboboxOption[]>(
    () =>
      (data?.items ?? [])
        .filter((p) => !p.deleted_at)
        .map((p) => ({
          value: p.id,
          label: p.code ? `${p.name} (${p.code})` : p.name,
        })),
    [data?.items],
  );

  return (
    <Combobox
      options={options}
      value={value || undefined}
      onChange={(v) => onChange(v ?? '')}
      placeholder={isLoading ? '読み込み中…' : placeholder}
      searchPlaceholder="氏名 / コードで検索"
      emptyText="患者が見つかりません"
      disabled={disabled || isLoading}
      className={className}
    />
  );
}
