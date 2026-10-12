/**
 * 人ごとの席: 相談の持ち主がその AI にサインインしていないとき、会話に落ち着いた 1 行と ⚙(AI のつなぎ先)を開くボタンが出る。
 * 利用上限に近いお知らせ(level warn)は「お知らせ」として出す。
 */
import * as React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import type { EngineAdmin } from '../engines';
import type { SessionView, TimelineItem } from '../types';

const admin: EngineAdmin = {
  view: vi.fn().mockResolvedValue({
    default_provider: 'codex',
    default_source: 'env',
    providers: ['codex'],
    can_sign_in: true,
    seat: 'own',
    engines: { codex: { state: 'missing' } },
    login: null,
  }),
  setDefault: vi.fn(),
  startLogin: vi.fn(),
  pollLogin: vi.fn(),
  sendCode: vi.fn(),
  cancelLogin: vi.fn(),
  logout: vi.fn(),
};

vi.mock('../AgentProvider', () => {
  const value = { answer: vi.fn(), decide: vi.fn(), undo: vi.fn(), engines: null as unknown };
  return {
    useAgent: () => value,
    useAgentMaybe: () => ({ ...value, engines: admin }),
  };
});

import { Conversation } from '../Conversation';

function session(items: TimelineItem[]): SessionView {
  return {
    id: 's1',
    title: 't',
    last_active_at: '2026-10-13T00:00:00Z',
    needs_you: false,
    pinned: false,
    state: 'idle',
    pose: 'bow',
    line: '',
    items,
    plans: {},
    planOrder: [],
    lastSeq: 1,
    loaded: true,
    thinking: false,
  } as SessionView;
}

describe('サインインが要るときの会話のカード', () => {
  it('signin: 落ち着いた 1 行と、AI のつなぎ先を開くボタン', async () => {
    render(
      <Conversation
        session={session([
          {
            kind: 'error',
            id: 'e1',
            line: 'ChatGPT にサインインすると、相談を始められます',
            retry: false,
            code: 'signin',
          },
        ])}
        onOpenPlan={() => {}}
      />,
    );
    expect(screen.getByText('サインインをお願いします')).toBeInTheDocument();
    expect(screen.getByText('ChatGPT にサインインすると、相談を始められます')).toBeInTheDocument();
    expect(screen.queryByText('申し訳ありません')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'AI のつなぎ先の設定' }));
    expect(await screen.findByRole('dialog', { name: 'AI のつなぎ先' })).toBeInTheDocument();
    expect(await screen.findByText('あなたの ChatGPT')).toBeInTheDocument();
  });

  it('お知らせ(利用上限に近い)はボタンなし・「お知らせ」', () => {
    render(
      <Conversation
        session={session([
          {
            kind: 'error',
            id: 'e2',
            line: 'いまはこのアカウントの利用上限に近づいています（あなたの ChatGPT の契約の分です）',
            retry: false,
            code: 'usage_limit',
            level: 'warn',
          },
        ])}
        onOpenPlan={() => {}}
      />,
    );
    expect(screen.getByText('お知らせ')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      'いまはこのアカウントの利用上限に近づいています',
    );
    expect(screen.queryByRole('button', { name: 'AI のつなぎ先の設定' })).not.toBeInTheDocument();
  });

  it('ほかの失敗はいままでどおり', () => {
    render(
      <Conversation
        session={session([{ kind: 'error', id: 'e3', line: '係が止まりました', retry: true }])}
        onOpenPlan={() => {}}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('申し訳ありません');
  });
});
