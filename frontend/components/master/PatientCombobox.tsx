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
import { inactiveStatusLabel, isPlaceablePatientStatus } from '@/lib/schemas/patient';

interface PatientComboboxProps {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  /**
   * 非稼働 (入院中・一時休止・解約済み・開始前) の患者様も選べるようにする。
   *
   * 既定 (false) は予定に入れられる患者様だけ (設計 §3-3 FE ピッカー除外)。
   * ステータスを問わない用途 (実績の参照・マスタの修正など) だけ true にする。
   * true のときはラベルに「入院中」などの状態を付けて見分けられるようにする。
   */
  includeInactive?: boolean;
}

export function PatientCombobox({
  value,
  onChange,
  disabled,
  placeholder = '患者を選択',
  className,
  includeInactive = false,
}: PatientComboboxProps) {
  // `limit` is the page size for the client wrapper; 500 matches the backend
  // hard cap and the wrapper's internal fetch window.
  const { data, isLoading } = usePatients({ page: 1, limit: 500 });

  const options = React.useMemo<ComboboxOption[]>(
    () =>
      (data?.items ?? [])
        .filter((p) => !p.deleted_at)
        .filter((p) => includeInactive || isPlaceablePatientStatus(p.status))
        .map((p) => {
          const base = p.code ? `${p.name} (${p.code})` : p.name;
          const statusLabel = includeInactive ? inactiveStatusLabel(p.status) : null;
          return {
            value: p.id,
            label: statusLabel ? `${base}【${statusLabel}】` : base,
          };
        }),
    [data?.items, includeInactive],
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
