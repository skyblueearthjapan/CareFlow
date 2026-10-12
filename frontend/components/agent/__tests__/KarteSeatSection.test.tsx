/**
 * ⚙「カルテの下書きに使う席」(管理者だけ・PO 2026-10-13): ChatGPT にサインインしている管理者だけを選べる・
 * 選ぶと保存する・決め方(決めた席 / 録音した本人)を切り替えられる・保存の失敗を出す。
 */
import * as React from 'react';
import { afterEach, describe, expect, it, vi, type Mock } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { EngineSettingsSheet, KarteSeatSection } from '../EngineSettings';
import type { EngineAdmin, EnginesView, KarteSettings } from '../engines';

function karte(over: Partial<KarteSettings> = {}): KarteSettings {
  return {
    mode: 'designated',
    modes: ['designated', 'presser'],
    seat_user_id: null,
    seat_known: false,
    seats: [
      { user_id: 'u-1', name: '今泉', me: true, state: 'ok' },
      { user_id: 'u-2', name: '佐藤', me: false, state: 'missing' },
    ],
    ...over,
  };
}

function admin(k: KarteSettings = karte()): EngineAdmin & Record<string, Mock> {
  return {
    karte: vi.fn().mockResolvedValue(k),
    setKarte: vi.fn().mockImplementation(async (p: Partial<KarteSettings>) => ({ ...k, ...p })),
  } as unknown as EngineAdmin & Record<string, Mock>;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('KarteSeatSection', () => {
  it('ChatGPT にサインインしている管理者だけを選べる', async () => {
    render(<KarteSeatSection engines={admin()} />);
    expect(await screen.findByRole('radio', { name: /今泉/ })).not.toBeChecked();
    expect(screen.queryByRole('radio', { name: /佐藤/ })).not.toBeInTheDocument();
    expect(screen.getByText(/まだ選んでいません/)).toBeInTheDocument();
    expect(screen.getAllByText(/Gemini で作ります/).length).toBeGreaterThan(0);
    expect(screen.getByRole('radio', { name: /ここで選んだ管理者/ })).toBeChecked();
  });

  it('選ぶと保存して、選んだ席に印が付く', async () => {
    const a = admin();
    render(<KarteSeatSection engines={a} />);
    fireEvent.click(await screen.findByRole('radio', { name: /今泉/ }));
    await waitFor(() => expect(a.setKarte).toHaveBeenCalledWith({ seat_user_id: 'u-1' }));
    await waitFor(() => expect(screen.getByRole('radio', { name: /今泉/ })).toBeChecked());
  });

  it('前に選んだ方がサインアウトしていても見せ、Gemini で作ると添える', async () => {
    render(<KarteSeatSection engines={admin(karte({ seat_user_id: 'u-2', seat_known: true }))} />);
    expect(await screen.findByRole('radio', { name: /佐藤/ })).toBeChecked();
    expect(
      screen.getByText(/サインインしていません。いまは Gemini で作ります/),
    ).toBeInTheDocument();
  });

  it('録音した本人の ChatGPT に切り替えると、席の一覧は出さない', async () => {
    const a = admin();
    render(<KarteSeatSection engines={a} />);
    fireEvent.click(await screen.findByRole('radio', { name: /録音した本人/ }));
    await waitFor(() => expect(a.setKarte).toHaveBeenCalledWith({ mode: 'presser' }));
    await waitFor(() =>
      expect(screen.queryByRole('radio', { name: /今泉/ })).not.toBeInTheDocument(),
    );
  });

  it('保存できなかったら理由を出す', async () => {
    const a = admin();
    a.setKarte.mockRejectedValueOnce(new Error('その方は ChatGPT にサインインしていません'));
    render(<KarteSeatSection engines={a} />);
    fireEvent.click(await screen.findByRole('radio', { name: /今泉/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('サインインしていません');
  });

  it('⚙ では管理者の一覧が見られるときだけ出す', async () => {
    const v: EnginesView = {
      default_provider: 'codex',
      default_source: 'env',
      providers: ['codex'],
      can_sign_in: true,
      engines: { codex: { state: 'ok' } },
      login: null,
      can_view_seats: false,
    };
    const a = {
      ...admin(),
      view: vi.fn().mockResolvedValue(v),
      pollLogin: vi.fn(),
    } as unknown as EngineAdmin;
    const { unmount } = render(<EngineSettingsSheet engines={a} onClose={() => {}} />);
    await screen.findByTestId('rk-es-card-codex');
    expect(screen.queryByText('カルテの下書きに使う席')).not.toBeInTheDocument();
    unmount();
    const b = {
      ...a,
      view: vi.fn().mockResolvedValue({ ...v, can_view_seats: true }),
    } as EngineAdmin;
    render(<EngineSettingsSheet engines={b} onClose={() => {}} />);
    expect(await screen.findByText('カルテの下書きに使う席')).toBeInTheDocument();
  });
});
