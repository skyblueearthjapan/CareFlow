import { describe, expect, it } from 'vitest';

import { agentReducer, initialStore, workLabel } from '../reducer';
import type { AgentEvent, AgentStore } from '../types';

function start(): AgentStore {
  return agentReducer(initialStore, {
    type: 'sessions',
    list: [{ id: 's1', title: 't', last_active_at: new Date().toISOString() }],
  });
}
function feed(store: AgentStore, evs: Array<Record<string, unknown>>, from = 1): AgentStore {
  return evs.reduce(
    (s, e, i) =>
      agentReducer(s, {
        type: 'event',
        sessionId: 's1',
        ev: { seq: from + i, ...e } as AgentEvent,
      }),
    store,
  );
}
const items = (s: AgentStore) => s.sessions['s1']!.items;

describe('reducer: 作業の畳み', () => {
  it('作業は流れている間は開き、終わると「作業 N 手順・M 秒」に畳む', () => {
    const s = feed(start(), [
      { type: 'step.start', step_id: 'a', label: '予定を見ています…' },
      { type: 'step.end', step_id: 'a', ok: true, secs: 2 },
      { type: 'step.start', step_id: 'b', label: '職員のお休みを確かめています…' },
      { type: 'step.end', step_id: 'b', ok: true, secs: 3 },
    ]);
    expect(items(s)).toHaveLength(1);
    expect(items(s)[0]).toMatchObject({ kind: 'work', collapsed: false });

    const after = feed(s, [{ type: 'message', id: 'm1', text: 'できました' }], 5);
    const w = items(after)[0];
    expect(w).toMatchObject({ kind: 'work', collapsed: true, secs: 5 });
    if (w?.kind === 'work') expect(workLabel(w)).toBe('作業 2 手順・5 秒');
    expect(items(after)[1]).toMatchObject({ kind: 'bot', text: 'できました' });
  });

  it('steps.summary でも畳む', () => {
    const s = feed(start(), [
      { type: 'step.start', step_id: 'a', label: 'x' },
      { type: 'step.end', step_id: 'a', ok: true, secs: 1 },
      { type: 'steps.summary', secs: 42 },
    ]);
    expect(items(s)[0]).toMatchObject({ kind: 'work', collapsed: true, secs: 42 });
  });

  it('失敗(⚠)を含む作業は畳まない', () => {
    const s = feed(start(), [
      { type: 'step.start', step_id: 'a', label: '予定を見ています…' },
      { type: 'step.end', step_id: 'a', ok: false, secs: 1 },
      { type: 'steps.summary' },
      { type: 'turn.end' },
    ]);
    expect(items(s)[0]).toMatchObject({ kind: 'work', collapsed: false });
  });

  it('同じ seq の二重受信は捨てる', () => {
    const s = feed(start(), [{ type: 'message', id: 'm', text: 'a' }]);
    const again = feed(s, [{ type: 'message', id: 'm2', text: 'b' }], 1);
    expect(again).toBe(s);
  });

  it('message.delta(id なし)は末尾の流れている文に足され、message で確定する', () => {
    const s = feed(start(), [
      { type: 'message.delta', text: 'こん' },
      { type: 'message.delta', text: 'にちは' },
    ]);
    expect(items(s)).toEqual([{ kind: 'bot', id: 'b1', text: 'こんにちは', streaming: true }]);
    const done = feed(s, [{ type: 'message', text: 'こんにちは。' }], 3);
    expect(items(done)).toEqual([{ kind: 'bot', id: 'b1', text: 'こんにちは。', streaming: false }]);
    // 確定の後の文は別の吹き出し
    const next = feed(done, [{ type: 'message', text: '次' }], 4);
    expect(items(next)).toHaveLength(2);
  });

  it('質問への答えは先に答えた端末の名前つきで反映される', () => {
    const s = feed(start(), [
      { type: 'question', question_id: 'q', questions: [{ question: '?', options: [] }] },
      {
        type: 'question.answered',
        question_id: 'q',
        answers: { '?': 'はい' },
        user_id: 'u',
        device: 'mobile',
      },
    ]);
    expect(items(s)[0]).toMatchObject({ kind: 'question', answers: ['はい'], by: 'スマホ' });
  });
});

describe('reducer: 案・承認・反映(中継の形)', () => {
  const plan = (version: number, status = 'draft') => ({
    type: 'plan',
    plan_id: `p${version}`,
    version,
    title: `案の題 ${version}`,
    summary: ['3 件'],
    counts: { ops: 3, not_undoable: 0 },
    undoable: true,
    ready: true,
    blockers: [],
    html_urls: { a4: `/agent-relay/sessions/s1/plans/${version}/html?size=a4` },
    status,
  });

  it('新しい版が来ると前の版は「前の案」・前の承認は終わり', () => {
    const s = feed(start(), [
      plan(1),
      { type: 'approval.request', kind: 'apply', version: 1, plan_id: 'p1' },
      plan(2),
      { type: 'approval.request', kind: 'apply', version: 2, plan_id: 'p2' },
    ]);
    const v = s.sessions['s1']!;
    expect(v.plans['p1']).toMatchObject({ status: 'superseded', label: '案 1' });
    expect(v.plans['p2']).toMatchObject({ status: 'draft', label: '案 2' });
    const aps = items(s).filter((i) => i.kind === 'approval');
    expect(aps).toMatchObject([
      { approvalId: 'v1', decision: 'expired' },
      { approvalId: 'v2', version: 2 },
    ]);
    expect(aps[1]).not.toHaveProperty('decision');
  });

  it('approval.result(版)→ 承認済み、stale の案は承認が終わる', () => {
    const s = feed(start(), [
      plan(1),
      { type: 'approval.request', kind: 'apply', version: 1, plan_id: 'p1' },
      {
        type: 'approval.result',
        version: 1,
        kind: 'apply',
        decision: 'approve',
        user_id: 'u',
        device: 'pc',
      },
    ]);
    expect(items(s).find((i) => i.kind === 'approval')).toMatchObject({
      decision: 'approved',
      by: 'PC',
    });
    const st = feed(start(), [
      plan(1),
      { type: 'approval.request', kind: 'apply', version: 1, plan_id: 'p1' },
      plan(1, 'stale'),
    ]);
    expect(items(st).find((i) => i.kind === 'approval')).toMatchObject({ decision: 'expired' });
    expect(items(st).filter((i) => i.kind === 'plan')).toHaveLength(1);
  });

  it('run.progress → run.result で 1 枚のカード・元に戻すの依頼で印が付く', () => {
    const s = feed(start(), [
      { type: 'run.progress', run_kind: 'apply', i: 1, total: 2, label: '1 件目', ok: true },
      { type: 'run.progress', run_kind: 'apply', i: 2, total: 2, label: '2 件目', ok: true },
      {
        type: 'run.result',
        run_kind: 'apply',
        run_id: 'r-1',
        ok: true,
        applied: 2,
        total: 2,
        error: null,
        undoable: true,
        backup: 'x.sql.gz',
      },
      { type: 'approval.result', version: 0, kind: 'undo', decision: 'approve', user_id: 'u', device: 'pc' },
    ]);
    const runs = items(s).filter((i) => i.kind === 'run');
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      runKind: 'apply',
      runId: 'r-1',
      finished: true,
      ok: true,
      done: 2,
      line: '2 件を反映しました',
      undoRequested: true,
    });
  });

  it('止まった反映は「何件目まで」を言う・error は message を出す', () => {
    const s = feed(start(), [
      {
        type: 'run.result',
        run_kind: 'apply',
        run_id: 'r-2',
        ok: false,
        applied: 3,
        total: 5,
        error: '予定が変わっていました',
        undoable: true,
        backup: null,
      },
      { type: 'error', code: 'cli_exit', message: '係が止まりました', retry: true },
    ]);
    expect(items(s)[0]).toMatchObject({
      line: '3 件目までは反映済み・そこで止まりました: 予定が変わっていました',
    });
    expect(items(s)[1]).toMatchObject({ kind: 'error', line: '係が止まりました', retry: true });
    expect(s.sessions['s1']!.state).toBe('error');
  });

  it('progress は吹き出しの 1 行・queued も受ける', () => {
    const s = feed(start(), [
      { type: 'progress', text: '組み合わせを比べています（3/6 日）' },
      { type: 'status', state: 'queued', pose: 'waiting', line: '順番を待っています' },
    ]);
    expect(s.sessions['s1']!.state).toBe('queued');
    const p = feed(start(), [{ type: 'progress', text: '組み合わせを比べています' }]);
    expect(p.sessions['s1']!.line).toBe('組み合わせを比べています');
  });
});
