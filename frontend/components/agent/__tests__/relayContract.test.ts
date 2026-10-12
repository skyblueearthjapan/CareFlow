/**
 * 中継(agent/relay)との約束の試験。
 * 1. 中継の金型試験の期待イベント(agent/relay/tests/fixtures/*.expected.json = 本物の claude の
 *    stream-json を中継が正規化した結果)を、そのまま画面の reducer に流して画面の状態になること。
 * 2. HttpRelay が中継の public.py と同じ道・形で頼むこと。承認番号(ap_…)を送らないこと。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import { agentReducer, initialStore } from '../reducer';
import { HttpRelay, RelayError, relayBaseFromEnv, toSummary } from '../relay';
import type { AgentEvent, AgentStore } from '../types';

const FIXTURES = resolve(process.cwd(), '../agent/relay/tests/fixtures');

function relayEvents(name: string): AgentEvent[] {
  const raw = JSON.parse(readFileSync(resolve(FIXTURES, `${name}.expected.json`), 'utf-8')) as Array<
    Record<string, unknown>
  >;
  // _meta / _abort は中継の内部用(保存も配信もしない)。seq は中継の EventBus が付ける
  return raw
    .filter((e) => !String(e.type).startsWith('_'))
    .map((e, i) => ({ ...e, seq: i + 1, session_id: 's1', turn_id: 1 }) as AgentEvent);
}

function run(evs: AgentEvent[]): AgentStore {
  const s0 = agentReducer(initialStore, {
    type: 'sessions',
    list: [{ id: 's1', title: 't', last_active_at: new Date().toISOString() }],
  });
  return evs.reduce((s, ev) => agentReducer(s, { type: 'event', sessionId: 's1', ev }), s0);
}

describe('中継の金型イベント → 画面の状態', () => {
  it('質問 → 答え → 文 → 作業 1 手順 → 文(claude_ask_then_tool)', () => {
    const s = run(relayEvents('claude_ask_then_tool')).sessions['s1']!;
    expect(s.state).toBe('idle');
    expect(s.pose).toBe('bow');
    expect(s.items.map((i) => i.kind)).toEqual(['question', 'bot', 'work', 'bot']);
    expect(s.items[0]).toMatchObject({ kind: 'question' });
    if (s.items[0]?.kind === 'question') {
      expect(s.items[0].questions[0]?.options.map((o) => o.label)).toEqual(['赤', '青']);
    }
    expect(s.items[1]).toMatchObject({ kind: 'bot', text: '青がお好きなんですね。', streaming: false });
    expect(s.items[2]).toMatchObject({ kind: 'work', collapsed: true });
    expect(s.items[3]).toMatchObject({ kind: 'bot', text: '30', streaming: false });
  });

  it('差分(message.delta)と確定(message)が 1 つの吹き出しになる(claude_sql_list_partial)', () => {
    const s = run(relayEvents('claude_sql_list_partial')).sessions['s1']!;
    const bots = s.items.filter((i) => i.kind === 'bot');
    expect(bots).toHaveLength(1);
    expect(bots[0]).toMatchObject({ streaming: false });
    expect(s.items[0]).toMatchObject({ kind: 'work', collapsed: true });
    expect(s.state).toBe('idle');
  });
});

describe('HttpRelay の頼み方(agent/relay/public.py と同じ)', () => {
  const calls: Array<{ url: string; method: string; body?: string; auth?: string | null }> = [];
  function mockFetch(body: unknown = { ok: true }, status = 200) {
    calls.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit = {}) => {
        const h = new Headers(init.headers);
        calls.push({
          url,
          method: init.method ?? 'GET',
          body: typeof init.body === 'string' ? init.body : undefined,
          auth: h.get('Authorization'),
        });
        const text = typeof body === 'string' ? body : JSON.stringify(body);
        return new Response(text, { status });
      }),
    );
  }
  afterEach(() => vi.unstubAllGlobals());

  const relay = () => new HttpRelay(() => 'jwt-token', '/agent-relay', 'mobile');

  it('承認・やめる・元に戻すは版と run_id で頼み、承認番号は送らない', async () => {
    mockFetch({ ok: true, version: 2, expires_at: '2026-10-12T00:15:00Z' });
    const r = relay();
    await r.decide('s1', 2, 'approve', 'pc');
    await r.decide('s1', 2, 'reject', 'mobile');
    await r.undo('s1', 'run-20261012-abc', 'pc');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /agent-relay/sessions/s1/plans/2/approve',
      'POST /agent-relay/sessions/s1/plans/2/reject',
      'POST /agent-relay/sessions/s1/runs/run-20261012-abc/undo/approve',
    ]);
    expect(calls.map((c) => JSON.parse(c.body!))).toEqual([
      { device: 'pc' },
      { device: 'mobile' },
      { device: 'pc' },
    ]);
    expect(calls.every((c) => c.auth === 'Bearer jwt-token')).toBe(true);
    expect(JSON.stringify(calls)).not.toMatch(/ap_/);
  });

  it('送る・答える・止める', async () => {
    mockFetch({ ok: true, seq: 3 });
    const r = relay();
    await r.send('s1', '田中さんが休みです');
    await r.send('s1', 'ここを合わせて', { planRef: 2, device: 'pc' });
    await r.answer('s1', 'q-1', { '一日中でしょうか？': '一日中' });
    await r.stop('s1');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'POST /agent-relay/sessions/s1/messages',
      'POST /agent-relay/sessions/s1/messages',
      'POST /agent-relay/sessions/s1/answers',
      'POST /agent-relay/sessions/s1/stop',
    ]);
    expect(JSON.parse(calls[0]!.body!)).toEqual({ text: '田中さんが休みです', device: 'mobile' });
    expect(JSON.parse(calls[1]!.body!)).toEqual({ text: 'ここを合わせて', device: 'pc', plan_ref: 2 });
    expect(JSON.parse(calls[2]!.body!)).toEqual({
      question_id: 'q-1',
      answers: { '一日中でしょうか？': '一日中' },
      device: 'mobile',
    });
    expect(JSON.stringify(calls)).not.toMatch(/ap_/);
  });

  it('案の HTML と印刷用 URL は版で取る', async () => {
    mockFetch('<html>案</html>');
    expect(await relay().planHtml('s1', 3, 'a3')).toBe('<html>案</html>');
    expect(calls[0]).toMatchObject({ method: 'GET', url: '/agent-relay/sessions/s1/plans/3/html?size=a3' });
    mockFetch({ url: '/agent-relay/print/s1/3/a4?exp=1&sig=x' });
    expect(await relay().printUrl('s1', 3, 'a4')).toBe('/agent-relay/print/s1/3/a4?exp=1&sig=x');
    expect(calls[0]).toMatchObject({ method: 'POST', url: '/agent-relay/sessions/s1/plans/3/print-url' });
    expect(JSON.parse(calls[0]!.body!)).toEqual({ size: 'a4' });
  });

  it('staging: 中継が別オリジンなら道にそのオリジンを付ける', async () => {
    expect(relayBaseFromEnv(undefined)).toBe('/agent-relay');
    expect(relayBaseFromEnv(' http://127.0.0.1:18211/agent-relay/ ')).toBe('http://127.0.0.1:18211/agent-relay');
    const stg = new HttpRelay(() => 't', relayBaseFromEnv('http://127.0.0.1:18211/agent-relay'), 'pc');
    mockFetch({ url: '/agent-relay/print/s1/3?size=a4&sig=x' });
    expect(await stg.printUrl('s1', 3, 'a4')).toBe('http://127.0.0.1:18211/agent-relay/print/s1/3?size=a4&sig=x');
    expect(calls[0]!.url).toBe('http://127.0.0.1:18211/agent-relay/sessions/s1/plans/3/print-url');
  });

  it('一覧・作る・名前を変える', async () => {
    mockFetch([
      { id: 'a', title: 'x', status: 'waiting_approval', last_active_at: '2026-10-12T01:00:00Z', pinned: true },
    ]);
    const list = await relay().listSessions();
    expect(list).toEqual([
      { id: 'a', title: 'x', last_active_at: '2026-10-12T01:00:00Z', needs_you: true, pinned: true },
    ]);
    mockFetch({ id: 'b', title: '', status: 'idle', last_active_at: '2026-10-12T01:00:00Z', needs_you: false });
    expect(await relay().createSession()).toMatchObject({ id: 'b', title: '新しい相談', needs_you: false });
    expect(calls[0]).toMatchObject({ method: 'POST', url: '/agent-relay/sessions' });
    mockFetch({ id: 'b', title: 'y' });
    await relay().renameSession('b', 'y');
    expect(calls[0]).toMatchObject({ method: 'PATCH', url: '/agent-relay/sessions/b', body: '{"title":"y"}' });
  });

  it('断られたら中継の detail を言葉にする', async () => {
    mockFetch({ detail: 'この案より新しい案があります。新しい案を見てください' }, 409);
    const err = await relay()
      .decide('s1', 1, 'approve', 'pc')
      .catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RelayError);
    expect((err as RelayError).status).toBe(409);
    expect((err as RelayError).message).toBe('この案より新しい案があります。新しい案を見てください');
  });

  it('toSummary: needs_you は中継の値を優先', () => {
    expect(toSummary({ id: 'a', status: 'idle', needs_you: true }).needs_you).toBe(true);
    expect(toSummary({ id: 'a', status: 'waiting_answer' }).needs_you).toBe(true);
  });
});
