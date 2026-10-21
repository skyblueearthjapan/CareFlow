/**
 * /acceptance — 拠点の切り替え (「全て」が先頭・既定、選んだ拠点はこのブラウザに残す)。
 */
import * as React from 'react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('@/lib/queries/offices', () => ({ useOffices: vi.fn() }));
vi.mock('@/lib/queries/acceptance_strict', () => ({
  useStrictAcceptance: vi.fn(),
  useStrictRefreshJob: vi.fn(() => ({ data: null })),
  useStartStrictRefresh: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
}));
vi.mock('@/lib/queries/acceptance_matrix', () => ({
  useSetStandingOverride: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
  useClearStandingOverride: vi.fn(() => ({ isPending: false, mutate: vi.fn() })),
}));
vi.mock('@/components/acceptance/StrictAcceptanceBoard', () => ({
  StrictAcceptanceBoard: () => <div data-testid="board" />,
}));

import { useSession } from 'next-auth/react';
import { useStrictAcceptance } from '@/lib/queries/acceptance_strict';
import { useOffices } from '@/lib/queries/offices';

import AcceptanceMatrixPage from '../page';

const OFFICES = [
  { id: 'office-1', name: '拠点イ' },
  { id: 'office-2', name: '拠点ロ' },
];

function lastCall(): [string | null, { allOffices?: boolean }] {
  const calls = (useStrictAcceptance as Mock).mock.calls;
  return calls[calls.length - 1] as [string | null, { allOffices?: boolean }];
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  (useSession as Mock).mockReturnValue({ data: { user: { role: 'admin' } } });
  (useOffices as Mock).mockReturnValue({ allOffices: OFFICES, isSuccess: true });
  (useStrictAcceptance as Mock).mockReturnValue({ data: undefined, isLoading: false });
});

describe('AcceptanceMatrixPage の拠点', () => {
  it('「全て」が先頭で既定・office_id を付けない', () => {
    render(<AcceptanceMatrixPage />);
    const select = screen.getByLabelText('拠点') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['全て', '拠点イ', '拠点ロ']);
    expect(select.value).toBe('');
    expect(lastCall()[0]).toBeNull();
    expect(lastCall()[1].allOffices).toBe(true);
  });

  it('拠点を選ぶと office_id を付け、残す。「全て」に戻すと外す', () => {
    render(<AcceptanceMatrixPage />);
    const select = screen.getByLabelText('拠点');
    fireEvent.change(select, { target: { value: 'office-2' } });
    expect(lastCall()[0]).toBe('office-2');
    expect(lastCall()[1].allOffices).toBe(false);
    expect(window.localStorage.getItem('acceptance.strict.officeId')).toBe('office-2');
    fireEvent.change(select, { target: { value: '' } });
    expect(lastCall()[1].allOffices).toBe(true);
    expect(window.localStorage.getItem('acceptance.strict.officeId')).toBeNull();
  });

  it('残した拠点で開く・一覧に無い拠点なら「全て」', () => {
    window.localStorage.setItem('acceptance.strict.officeId', 'office-1');
    const { unmount } = render(<AcceptanceMatrixPage />);
    expect((screen.getByLabelText('拠点') as HTMLSelectElement).value).toBe('office-1');
    expect(lastCall()[0]).toBe('office-1');
    unmount();
    window.localStorage.setItem('acceptance.strict.officeId', 'office-gone');
    render(<AcceptanceMatrixPage />);
    expect((screen.getByLabelText('拠点') as HTMLSelectElement).value).toBe('');
    expect(lastCall()[1].allOffices).toBe(true);
  });
});
