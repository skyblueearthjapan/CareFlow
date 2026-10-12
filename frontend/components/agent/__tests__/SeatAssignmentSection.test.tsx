/**
 * ⚙「AI の席の割り当て」(管理者だけ・PO 2026-10-13): 行 = 管理者・列 = サインイン済みの席。押した時点で保存・
 * 未サインインの席は選べない(薄く)・割り当て先が未サインインなら行に出す・全員をまとめて 1 つの席に。
 */
import * as React from 'react';
import { afterEach, describe, expect, it, vi, type Mock } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { EngineSettingsSheet, SeatAssignmentSection } from '../EngineSettings';
import type { EngineAdmin, EnginesView, SeatAssignments } from '../engines';

function data(over: Partial<SeatAssignments> = {}): SeatAssignments {
  return {
    providers: ['codex', 'claude'],
    default_seat_owner_user_id: 'u-1',
    seats: [
      {
        owner_user_id: 'u-1',
        name: 'A004',
        me: false,
        engines: { codex: { state: 'ok' }, claude: { state: 'missing' } },
        signed_in: true,
      },
      {
        owner_user_id: 'u-2',
        name: 'A002',
        me: true,
        engines: { codex: { state: 'missing' }, claude: { state: 'missing' } },
        signed_in: false,
      },
    ],
    rows: [
      {
        user_id: 'u-1',
        name: 'A004',
        me: false,
        seat_owner_user_id: 'u-1',
        source: 'default',
        seat_known: true,
        seat_signed_in: true,
      },
      {
        user_id: 'u-2',
        name: 'A002',
        me: true,
        seat_owner_user_id: 'u-2',
        source: 'assigned',
        seat_known: true,
        seat_signed_in: false,
      },
    ],
    ...over,
  };
}

function admin(d: SeatAssignments = data()): EngineAdmin & Record<string, Mock> {
  const moved = (owner: string, who?: string): SeatAssignments => ({
    ...d,
    rows: d.rows.map((r) =>
      !who || r.user_id === who
        ? { ...r, seat_owner_user_id: owner, source: 'assigned', seat_signed_in: true }
        : r,
    ),
  });
  return {
    assignments: vi.fn().mockResolvedValue(d),
    assignSeat: vi.fn().mockImplementation(async (who: string, owner: string) => moved(owner, who)),
    assignSeatAll: vi.fn().mockImplementation(async (owner: string) => moved(owner)),
  } as unknown as EngineAdmin & Record<string, Mock>;
}

afterEach(() => vi.clearAllMocks());

describe('SeatAssignmentSection', () => {
  it('列はサインインしている AI の名前つき。未サインインの席は選べない', async () => {
    render(<SeatAssignmentSection engines={admin()} />);
    expect(
      await screen.findByRole('columnheader', { name: 'A004 の ChatGPT' }),
    ).toBeInTheDocument();
    const mine = screen.getByRole('columnheader', { name: /自分の席/ });
    expect(mine).toHaveAttribute('data-disabled');
    expect(screen.getByRole('radio', { name: 'A004 は A004 の ChatGPT' })).toBeChecked();
    expect(screen.getByRole('radio', { name: /A004 は 自分の席/ })).toBeDisabled();
  });

  it('割り当て先がサインインしていない行に「席がサインインしていません」', async () => {
    render(<SeatAssignmentSection engines={admin()} />);
    const row = await screen.findByTestId('rk-es-assign-u-2');
    expect(within(row).getByText(/席がサインインしていません/)).toBeInTheDocument();
    expect(
      within(screen.getByTestId('rk-es-assign-u-1')).queryByText(/席がサインインしていません/),
    ).not.toBeInTheDocument();
  });

  it('押した時点で 1 人だけ保存する', async () => {
    const a = admin();
    render(<SeatAssignmentSection engines={a} />);
    fireEvent.click(await screen.findByRole('radio', { name: 'A002 は A004 の ChatGPT' }));
    await waitFor(() => expect(a.assignSeat).toHaveBeenCalledWith('u-2', 'u-1'));
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'A002 は A004 の ChatGPT' })).toBeChecked(),
    );
    expect(a.assignSeatAll).not.toHaveBeenCalled();
  });

  it('全員をこの席にする: 選んで念押しのボタンで全員に', async () => {
    const a = admin();
    render(<SeatAssignmentSection engines={a} />);
    const sel = await screen.findByRole('combobox', { name: '全員をこの席にする' });
    // 未サインインの席は選択肢に出ない
    expect(within(sel).queryByText(/自分の席/)).not.toBeInTheDocument();
    fireEvent.change(sel, { target: { value: 'u-1' } });
    fireEvent.click(screen.getByRole('button', { name: '全員を「A004 の ChatGPT」にする' }));
    await waitFor(() => expect(a.assignSeatAll).toHaveBeenCalledWith('u-1'));
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'A002 は A004 の ChatGPT' })).toBeChecked(),
    );
  });

  it('保存できなかったら理由を出す', async () => {
    const a = admin();
    a.assignSeat.mockRejectedValueOnce(new Error('その席はまだサインインしていません'));
    render(<SeatAssignmentSection engines={a} />);
    fireEvent.click(await screen.findByRole('radio', { name: 'A002 は A004 の ChatGPT' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('まだサインインしていません');
  });

  it('⚙: ほかの人の席で動くときは一言出す', async () => {
    const v: EnginesView = {
      default_provider: 'codex',
      default_source: 'env',
      providers: ['codex'],
      can_sign_in: true,
      engines: { codex: { state: 'missing' } },
      login: null,
      consult_seat: { owner_is_me: false, source: 'default', name: 'A004', state: 'missing' },
    };
    const a = { view: vi.fn().mockResolvedValue(v), pollLogin: vi.fn() } as unknown as EngineAdmin;
    render(<EngineSettingsSheet engines={a} onClose={() => {}} />);
    const note = await screen.findByTestId('rk-es-consult-seat');
    expect(note).toHaveTextContent('A004 の席の AI で動きます');
    expect(note).toHaveTextContent('管理者に連絡してください');
  });
});
