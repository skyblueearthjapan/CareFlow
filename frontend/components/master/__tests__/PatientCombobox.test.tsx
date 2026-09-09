/**
 * PatientCombobox — 非稼働患者の除外 (Phase 2 / 設計 §3-3「FE ピッカー除外」)。
 *
 * 既定 (`includeInactive` 省略) では稼働中の患者様しか選べない。
 * ステータスを問わない用途 (実績の参照・マスタの修正など) だけ
 * `includeInactive` を true にし、そのときはラベルに状態を添えて見分けられるようにする。
 */
import * as React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const { mocks } = vi.hoisted(() => ({
  mocks: { items: [] as Record<string, unknown>[] },
}));

// Combobox 本体 (cmdk / popover) は描画せず、渡された options を露出するだけの stub。
vi.mock('@/components/ui/combobox', () => ({
  Combobox: ({ options }: { options: { value: string; label: string }[] }) => (
    <ul data-testid="combobox-options">
      {options.map((o) => (
        <li key={o.value} data-value={o.value}>
          {o.label}
        </li>
      ))}
    </ul>
  ),
}));

vi.mock('@/lib/queries/patients', () => ({
  usePatients: () => ({ data: { items: mocks.items }, isLoading: false }),
}));

import { PatientCombobox } from '../PatientCombobox';

const PATIENTS = [
  { id: 'p1', name: '稼働 太郎', code: 'A-1', status: 'active', deleted_at: null },
  { id: 'p2', name: '入院 花子', code: 'A-2', status: 'admitted', deleted_at: null },
  { id: 'p3', name: '休止 次郎', code: null, status: 'suspended', deleted_at: null },
  { id: 'p4', name: '開始前 三郎', code: 'A-4', status: 'pending', deleted_at: null },
];

function renderCombobox(includeInactive?: boolean) {
  mocks.items = PATIENTS;
  return render(
    <PatientCombobox value="" onChange={() => undefined} includeInactive={includeInactive} />,
  );
}

function labels(): string[] {
  return Array.from(screen.getByTestId('combobox-options').querySelectorAll('li')).map(
    (li) => li.textContent ?? '',
  );
}

describe('PatientCombobox', () => {
  it('既定では非稼働 (入院中・一時休止・開始前) を候補から外す', () => {
    renderCombobox();
    expect(labels()).toEqual(['稼働 太郎 (A-1)']);
  });

  it('includeInactive=true なら全員出し、ラベルに状態を添える', () => {
    renderCombobox(true);
    expect(labels()).toEqual([
      '稼働 太郎 (A-1)',
      '入院 花子 (A-2)【入院中】',
      '休止 次郎【一時休止】',
      '開始前 三郎 (A-4)【開始前】',
    ]);
  });
});
