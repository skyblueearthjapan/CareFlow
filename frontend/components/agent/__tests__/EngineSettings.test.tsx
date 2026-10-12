/**
 * ⚙ 設定(AI のつなぎ先): 様子の表示・ChatGPT のコード・Claude のコードの貼り付け・サインアウトの 3 択・既定の AI。
 */
import * as React from 'react';
import { afterEach, describe, expect, it, vi, type Mock } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('next-auth/react', () => ({ useSession: vi.fn() }));
vi.mock('../useAgentFlag', () => ({ useAgentFlag: vi.fn() }));

import { useSession } from 'next-auth/react';

import { AgentWidget } from '../AgentWidget';
import { EngineSettingsSheet } from '../EngineSettings';
import type { EngineAdmin, EnginesView, LoginFlow } from '../engines';
import { createMockRelay } from '../mockRelay';
import { useAgentFlag } from '../useAgentFlag';

function view(over: Partial<EnginesView> = {}): EnginesView {
  return {
    default_provider: 'codex',
    default_source: 'env',
    providers: ['codex', 'claude'],
    can_sign_in: true,
    engines: { codex: { state: 'ok' }, claude: { state: 'missing' } },
    login: null,
    ...over,
  };
}

function flow(over: Partial<LoginFlow>): LoginFlow {
  return {
    provider: 'codex',
    state: 'waiting',
    url: 'https://auth.openai.com/codex/device',
    code: 'T9U6-PTFBM',
    needs_input: false,
    expires_in: 900,
    error: null,
    ...over,
  };
}

function fakeAdmin(v: EnginesView = view()): EngineAdmin & Record<string, Mock> {
  return {
    view: vi.fn().mockResolvedValue(v),
    setDefault: vi.fn().mockResolvedValue(undefined),
    startLogin: vi.fn().mockResolvedValue(flow({})),
    pollLogin: vi.fn().mockResolvedValue(flow({})),
    sendCode: vi
      .fn()
      .mockResolvedValue(flow({ provider: 'claude', state: 'verifying', code: null })),
    cancelLogin: vi.fn().mockResolvedValue(flow({ state: 'cancelled' })),
    logout: vi.fn().mockResolvedValue({ provider: 'codex', ok: true, state: 'missing' }),
  } as unknown as EngineAdmin & Record<string, Mock>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe('EngineSettingsSheet', () => {
  it('2 つの AI の様子と、新しい相談で使う AI を出す(メール等は出さない)', async () => {
    const admin = fakeAdmin();
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    const codex = await screen.findByTestId('rk-es-card-codex');
    expect(within(codex).getByText('ChatGPT')).toBeInTheDocument();
    expect(within(codex).getByText('サインインしています')).toBeInTheDocument();
    expect(within(codex).getByRole('button', { name: 'サインアウト' })).toBeInTheDocument();
    const claude = screen.getByTestId('rk-es-card-claude');
    expect(within(claude).getByText('サインインしていません')).toBeInTheDocument();
    expect(within(claude).getByRole('button', { name: 'サインイン' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /ChatGPT/ })).toBeChecked();
    expect(screen.queryByText(/API/)).not.toBeInTheDocument();
  });

  it('ChatGPT: サインインで URL と 1 回限りのコード・コピー・残り時間を出す', async () => {
    const admin = fakeAdmin(
      view({ engines: { codex: { state: 'missing' }, claude: { state: 'ok' } } }),
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    const card = await screen.findByTestId('rk-es-card-codex');
    fireEvent.click(within(card).getByRole('button', { name: 'サインイン' }));
    expect(await screen.findByTestId('rk-es-code')).toHaveTextContent('T9U6-PTFBM');
    expect(admin.startLogin).toHaveBeenCalledWith('codex');
    const link = screen.getByRole('link', { name: 'https://auth.openai.com/codex/device' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(screen.getByText(/あと 15:00/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'コピー' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('T9U6-PTFBM'));
    expect(await screen.findByRole('button', { name: 'コピーしました' })).toBeInTheDocument();
    // 別の AI のサインインは、終わるまで押せない
    expect(
      within(screen.getByTestId('rk-es-card-claude')).getByRole('button', { name: 'サインアウト' }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'やめる' }));
    await waitFor(() => expect(admin.cancelLogin).toHaveBeenCalled());
    expect(await screen.findByText('サインインをやめました。')).toBeInTheDocument();
  });

  it('サインインの途中は様子を見に行き、できたら一覧を読み直す', async () => {
    vi.useFakeTimers();
    const admin = fakeAdmin(
      view({ engines: { codex: { state: 'missing' }, claude: { state: 'ok' } } }),
    );
    admin.pollLogin.mockResolvedValue(flow({ state: 'done', expires_in: 0 }));
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(
      within(screen.getByTestId('rk-es-card-codex')).getByRole('button', { name: 'サインイン' }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    admin.view.mockResolvedValue(view());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    expect(admin.pollLogin).toHaveBeenCalled();
    expect(screen.getByText(/サインインできました/)).toBeInTheDocument();
    expect(admin.view.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('Claude: ブラウザーに出たコードを貼り付けて送る', async () => {
    const admin = fakeAdmin();
    admin.startLogin.mockResolvedValue(
      flow({
        provider: 'claude',
        url: 'https://claude.com/cai/oauth/authorize?x=1',
        code: null,
        needs_input: true,
      }),
    );
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    const card = await screen.findByTestId('rk-es-card-claude');
    fireEvent.click(within(card).getByRole('button', { name: 'サインイン' }));
    const input = await screen.findByLabelText('ブラウザーに出たコード');
    expect(screen.getByRole('link', { name: 'Claude のサインインのページを開く' })).toHaveAttribute(
      'href',
      'https://claude.com/cai/oauth/authorize?x=1',
    );
    expect(screen.getByRole('button', { name: '送る' })).toBeDisabled();
    fireEvent.change(input, { target: { value: 'abc123#state' } });
    fireEvent.click(screen.getByRole('button', { name: '送る' }));
    await waitFor(() => expect(admin.sendCode).toHaveBeenCalledWith('abc123#state'));
    expect(await screen.findByText('確かめています…')).toBeInTheDocument();
  });

  it('形の違うコードは、やさしく知らせる', async () => {
    const admin = fakeAdmin(
      view({
        login: flow({
          provider: 'claude',
          url: 'https://claude.ai/x',
          code: null,
          needs_input: true,
          error: 'invalid_code',
        }),
      }),
    );
    admin.pollLogin.mockResolvedValue(
      flow({
        provider: 'claude',
        url: 'https://claude.ai/x',
        code: null,
        needs_input: true,
        error: 'invalid_code',
      }),
    );
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('コードの形が違うようです');
  });

  it('サインアウトは 3 択で確かめ、止まることを伝える', async () => {
    const admin = fakeAdmin();
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    const card = await screen.findByTestId('rk-es-card-codex');
    fireEvent.click(within(card).getByRole('button', { name: 'サインアウト' }));
    const group = screen.getByRole('group', { name: /サインアウトしますか/ });
    expect(group).toHaveTextContent('だれかがもう一度サインインするまで止まります');
    const choices = within(group)
      .getAllByRole('button')
      .map((b) => b.textContent);
    expect(choices).toEqual([
      'サインアウトする',
      'サインアウトして、別のアカウントでサインインし直す',
      'やめる',
    ]);
    fireEvent.click(within(group).getByRole('button', { name: 'やめる' }));
    expect(admin.logout).not.toHaveBeenCalled();
    fireEvent.click(within(card).getByRole('button', { name: 'サインアウト' }));
    fireEvent.click(screen.getByRole('button', { name: 'サインアウトする' }));
    await waitFor(() => expect(admin.logout).toHaveBeenCalledWith('codex'));
    expect(await screen.findByText('ChatGPT からサインアウトしました。')).toBeInTheDocument();
    expect(admin.startLogin).not.toHaveBeenCalled();
  });

  it('既定の AI を選ぶと保存する', async () => {
    const admin = fakeAdmin();
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole('radio', { name: /Claude/ }));
    await waitFor(() => expect(admin.setDefault).toHaveBeenCalledWith('claude'));
    expect(await screen.findByText('新しい相談は Claude で始めます。')).toBeInTheDocument();
  });

  it('画面からサインインできない環境では、ボタンを押せない', async () => {
    const admin = fakeAdmin(view({ can_sign_in: false }));
    render(<EngineSettingsSheet engines={admin} onClose={() => {}} />);
    expect(
      await screen.findByText('この環境では、画面からのサインインはできません。'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId('rk-es-card-claude')).getByRole('button', { name: 'サインイン' }),
    ).toBeDisabled();
  });

  it('Esc・✕・閉じるで閉じる', async () => {
    const onClose = vi.fn();
    render(<EngineSettingsSheet engines={fakeAdmin()} onClose={onClose} />);
    await screen.findByTestId('rk-es-card-codex');
    fireEvent.keyDown(window, { key: 'Escape' });
    for (const b of screen.getAllByRole('button', { name: '閉じる' })) fireEvent.click(b);
    expect(onClose).toHaveBeenCalledTimes(3);
  });
});

describe('小窓の見出しの ⚙ 設定', () => {
  it('開いた小窓の見出しから設定を開ける', async () => {
    vi.stubEnv('NEXT_PUBLIC_AGENT_PILOT_USERS', 'u-1');
    (useAgentFlag as Mock).mockReturnValue(true);
    (useSession as Mock).mockReturnValue({
      data: { user: { id: 'u-1', role: 'admin' }, accessToken: 't' },
      status: 'authenticated',
    });
    render(<AgentWidget relay={createMockRelay({ speed: 0 })} />);
    fireEvent.click(screen.getByRole('button', { name: 'らく助を開く' }));
    fireEvent.click(screen.getByRole('button', { name: 'AI のつなぎ先の設定' }));
    expect(await screen.findByRole('dialog', { name: 'AI のつなぎ先' })).toBeInTheDocument();
    expect(await screen.findByTestId('rk-es-card-claude')).toBeInTheDocument();
  });
});
