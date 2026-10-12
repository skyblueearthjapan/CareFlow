/**
 * 模擬中継(mockRelay)で、質問 -> 案 -> 承認 -> 反映 の一連が画面の状態に届くこと。
 */
import * as React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { role: 'admin' }, accessToken: 't' }, status: 'authenticated' }),
}));

import { AgentProvider, useAgent, type AgentContextValue } from '../AgentProvider';
import { createMockRelay } from '../mockRelay';
import { createSseParser } from '../relay';

describe('mockRelay の流れ', () => {
  it('お休みの相談 -> 聞き返し -> 案 2 つ -> 承認 -> 反映', async () => {
    let ctx: AgentContextValue | null = null;
    function Probe() {
      ctx = useAgent();
      return null;
    }
    render(
      <AgentProvider device="pc" relay={createMockRelay({ speed: 0 })}>
        <Probe />
      </AgentProvider>,
    );
    await act(async () => {
      await ctx!.newSession();
    });
    await act(async () => {
      await ctx!.send('田中さんが急にお休みです');
    });
    await waitFor(() => expect(ctx!.current?.state).toBe('waiting_answer'));
    const q = ctx!.current!.items.find((i) => i.kind === 'question');
    expect(q?.kind === 'question' && q.questions[0]?.question).toContain('お休み');

    await act(async () => {
      await ctx!.answer(q!.kind === 'question' ? q.questionId : '', ['一日中']);
    });
    await waitFor(() => expect(ctx!.current?.state).toBe('waiting_approval'));
    expect(ctx!.current!.planOrder).toHaveLength(2);
    // 作業はターンの途中で畳まれている
    expect(ctx!.current!.items.find((i) => i.kind === 'work')).toMatchObject({ collapsed: true });

    // 前の版(案 1)の確認は新しい版(案 2)が出た時点で終わっている。押せるのは最新の版だけ
    const ap = ctx!.current!.items.find((i) => i.kind === 'approval' && !i.decision);
    expect(ap).toMatchObject({ approvalId: 'v2', version: 2 });
    await act(async () => {
      await ctx!.decide(ap!.kind === 'approval' ? ap.approvalId : '', 'approve');
    });
    await waitFor(() =>
      expect(ctx!.current!.items.find((i) => i.kind === 'run')).toMatchObject({ finished: true, ok: true }),
    );
    const decisions = ctx!.current!.items.filter((i) => i.kind === 'approval').map((i) => (i.kind === 'approval' ? i.decision : ''));
    expect(decisions.sort()).toEqual(['approved', 'expired']);
    expect(ctx!.current!.plans[ctx!.current!.planOrder[0]!]?.status).toBe('superseded');

    // 元に戻す(run_id で頼む)
    const run = ctx!.current!.items.find((i) => i.kind === 'run');
    expect(run).toMatchObject({ runKind: 'apply', undoable: true });
    await act(async () => {
      await ctx!.undo(run!.kind === 'run' ? run.runId! : '');
    });
    await waitFor(() =>
      expect(ctx!.current!.items.filter((i) => i.kind === 'run')).toMatchObject([
        { runKind: 'apply', undoRequested: true, undoable: false },
        { runKind: 'undo', finished: true, ok: true },
      ]),
    );
  });
});

describe('SSE パーサー', () => {
  it('チャンクが途中で切れても 1 件ずつ取り出し、ping は無視する', () => {
    const got: string[] = [];
    const feed = createSseParser((d) => got.push(d));
    feed(': ping\n\nid: 1\ndata: {"a"');
    feed(':1}\n\nid: 2\ndata: {"b":2}\n\n');
    expect(got).toEqual(['{"a":1}', '{"b":2}']);
  });
});
