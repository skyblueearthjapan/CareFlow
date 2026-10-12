/**
 * 開発・試験用の模擬中継。モック(rakusuke-agent-widget-mock / mobile-mock)の流れを、
 * 本物の中継(agent/relay)と同じ共通イベント(types.ts・agent/relay/events.py)で流す。
 * 登場する人・日付はすべて架空。
 *
 * 本物と同じにしてあること:
 *   - 文は message.delta(差分・id なし)→ message(確定)
 *   - 案は版番号(この相談で 1, 2, …)。新しい版が出ると前の版は反映できない
 *   - 承認は版で頼む(approval.request → approval.result)。承認番号(ap_…)は画面に出てこない
 *   - 反映は run.progress {i,total} → run.result {run_id, applied, total, …}
 *
 * 使い方: <AgentProvider relay={createMockRelay()}> か、
 *         NEXT_PUBLIC_AGENT_RELAY_MOCK=1 で AgentProvider が自動で使う。
 */
import type { AgentRelay, StreamHandlers } from './relay';
import type { AgentEvent, PlanSize, SessionSummary } from './types';

type NoSeq = Record<string, unknown> & { type: AgentEvent['type'] };

interface Hub {
  summary: SessionSummary;
  log: AgentEvent[];
  subs: Set<(ev: AgentEvent) => void>;
  running: boolean;
  aborted: boolean;
  waiters: Array<(v: string) => void>;
  /** 版番号 → A4/A3 の HTML(中継は rk の帳票で作る。模擬はここに持つ) */
  html: Map<number, Record<PlanSize, string>>;
  version: number;
  runs: number;
  turn: number;
}

export interface MockOptions {
  /** 1 = モックと同じ速さ。0 = 待たない(試験用)。 */
  speed?: number;
  /** 一覧に過去の相談を入れる */
  seed?: boolean;
}

const sleepMs = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function paper(title: string, rows: Array<[string, string, string]>, size: PlanSize): string {
  const w = size === 'a4' ? 794 : 1587;
  const body = rows
    .map(
      ([who, from, to]) =>
        `<tr><td>${who}</td><td class="f">${from}</td><td class="t">${to}</td></tr>`,
    )
    .join('');
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>
body{margin:0;width:${w}px;height:1123px;box-sizing:border-box;padding:44px 52px;background:#fff;color:#2b2226;font:13px/1.6 "Noto Sans JP",sans-serif}
h1{margin:0 0 16px;font-size:22px;color:#c94a6e;border-bottom:3px solid #e15a7f;padding-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:#7a646b;border-bottom:2px solid #f0dde3;padding:5px 6px}
td{border-bottom:1px solid #f3e7eb;padding:6px}.f{color:#9a8790;text-decoration:line-through}.t{font-weight:700;color:#3f8f50}
p{color:#7a646b;margin-top:24px}</style></head><body><h1>${title}</h1>
<table><tr><th>職員</th><th>いま</th><th>案</th></tr>${body}</table>
<p>らく助のサーバーが決まった様式で作った案です(試作・データはすべて架空)。反映するまで予定は変わりません。</p></body></html>`;
}

interface MockPlan {
  plan_id: string;
  version: number;
  title: string;
  rows: Array<[string, string, string]>;
}

function planEvent(p: MockPlan, sessionId: string): NoSeq {
  const base = `/agent-relay/sessions/${sessionId}/plans/${p.version}/html`;
  return {
    type: 'plan',
    plan_id: p.plan_id,
    version: p.version,
    title: p.title,
    summary: [`${p.rows.length} 件の訪問を入れ替えます`, 'カイポケへはまだ送りません'],
    counts: { ops: p.rows.length, not_undoable: 0 },
    undoable: true,
    ready: true,
    blockers: [],
    html_urls: { a4: `${base}?size=a4`, a3: `${base}?size=a3` },
    status: 'draft',
  };
}

const IDLE = { type: 'status', state: 'idle', pose: 'bow', line: 'ほかにも何かあれば、どうぞ' } as const;

export function createMockRelay(opts: MockOptions = {}): AgentRelay & { __hubs: Map<string, Hub> } {
  const speed = opts.speed ?? 1;
  const hubs = new Map<string, Hub>();
  let n = 0;
  const wait = (ms: number) => (speed > 0 ? sleepMs(ms * speed) : Promise.resolve());

  const hub = (id: string): Hub => {
    const h = hubs.get(id);
    if (!h) throw new Error(`no session ${id}`);
    return h;
  };
  const emit = (h: Hub, e: NoSeq) => {
    const ev = {
      ...e,
      seq: h.log.length + 1,
      ts: new Date().toISOString(),
      session_id: h.summary.id,
      turn_id: h.turn,
    } as AgentEvent;
    h.log.push(ev);
    h.summary = { ...h.summary, last_active_at: ev.ts ?? h.summary.last_active_at };
    h.subs.forEach((f) => f(ev));
  };
  const waitUser = (h: Hub) => new Promise<string>((res) => h.waiters.push(res));

  const newHub = (title: string, ageMs = 0, needsYou = false): Hub => {
    const id = `mock-${++n}`;
    const h: Hub = {
      summary: {
        id,
        title,
        last_active_at: new Date(Date.now() - ageMs).toISOString(),
        needs_you: needsYou,
      },
      log: [],
      subs: new Set(),
      running: false,
      aborted: false,
      waiters: [],
      html: new Map(),
      version: 0,
      runs: 0,
      turn: 0,
    };
    hubs.set(id, h);
    return h;
  };

  async function step(h: Hub, label: string, ms: number, pose?: string) {
    const id = `s_${h.log.length}`;
    if (pose) emit(h, { type: 'status', state: 'working', pose, line: label });
    emit(h, { type: 'step.start', step_id: id, label });
    await wait(ms);
    if (h.aborted) throw new Error('stop');
    emit(h, {
      type: 'step.end',
      step_id: id,
      label,
      ok: true,
      secs: Math.max(1, Math.round(ms / 1000)),
    });
  }
  async function say(h: Hub, text: string) {
    const half = Math.ceil(text.length / 2);
    emit(h, { type: 'message.delta', text: text.slice(0, half) });
    emit(h, { type: 'message.delta', text: text.slice(half) });
    emit(h, { type: 'message', text });
    await wait(200);
  }
  function summary(h: Hub, count: number, secs: number) {
    emit(h, {
      type: 'steps.summary',
      count,
      failed: 0,
      secs,
      label: `✓ 調べました（${count} 件・${secs} 秒）`,
    });
  }
  function turnEnd(h: Hub, secs: number, ok = true) {
    emit(h, { type: 'turn.end', secs, ok, reason: ok ? 'completed' : 'aborted_tools' });
  }

  async function runRun(h: Hub, kind: 'apply' | 'undo', total: number): Promise<string> {
    const runId = `run-mock-${++h.runs}`;
    emit(h, {
      type: 'status',
      state: 'applying',
      pose: 'stamp',
      line: kind === 'undo' ? '元に戻しています' : '反映しています',
    });
    for (let i = 1; i <= total; i++) {
      await wait(500);
      emit(h, { type: 'run.progress', run_kind: kind, i, total, label: `${i} 件目`, ok: true });
    }
    emit(h, {
      type: 'run.result',
      run_kind: kind,
      run_id: runId,
      ok: true,
      applied: total,
      total,
      error: null,
      undoable: kind === 'apply',
      backup: 'agent-mock-20261012-000000.sql.gz',
    });
    return runId;
  }

  async function runApproval(h: Hub, latest: MockPlan) {
    const v = await waitUser(h); // "approve:<版>" | "reject:<版>"
    if (h.aborted) return;
    const [decision = '', ver = ''] = v.split(':');
    if (Number(ver) !== latest.version) return;
    h.turn += 1;
    emit(h, {
      type: 'approval.result',
      version: latest.version,
      kind: 'apply',
      decision: decision === 'approve' ? 'approve' : 'reject',
      user_id: 'mock-user',
      device: 'pc',
    });
    if (decision !== 'approve') {
      await say(h, '承知しました。反映せずに止めておきますね。ご希望があれば、いつでもおっしゃってください。');
      turnEnd(h, 1);
      emit(h, IDLE);
      return;
    }
    await runRun(h, 'apply', latest.rows.length);
    await say(
      h,
      `反映しました（${latest.rows.length} 件）。照合も一致しました。カイポケへはまだ送っていません。`,
    );
    turnEnd(h, 4);
    emit(h, IDLE);
  }

  async function scenario(h: Hub, text: string) {
    const kind = /休/.test(text)
      ? 'off'
      : /足り/.test(text)
        ? 'short'
        : /特別/.test(text)
          ? 'special'
          : 'other';
    emit(h, { type: 'status', state: 'working', pose: 'search', line: '調べています' });
    emit(h, { type: 'thinking', phase: 'start' });
    await wait(700);
    emit(h, { type: 'thinking', phase: 'end' });
    if (kind === 'other') {
      await say(
        h,
        'ありがとうございます。お休みの反映や、人が足りているかの確認なら、すぐにお手伝いできます。どちらから始めましょうか？',
      );
      turnEnd(h, 1);
      emit(h, IDLE);
      return;
    }
    await step(h, '予定を読んでいます…', 900, 'search');
    await step(h, '職員のお休みを確かめています…', 900);
    if (kind === 'short') {
      await step(h, '来週の人数を数えています…', 1000, 'laptop');
      summary(h, 3, 3);
      await say(
        h,
        '確かめました。来週は、水曜日の午前だけ 1 名分たりません。ほかの日は足りています。ご希望があれば、水曜日の組み方の案を作りますね。',
      );
      turnEnd(h, 3);
      emit(h, IDLE);
      return;
    }
    // 聞き返し(AskUserQuestion)
    const qid = `q-${h.log.length}`;
    summary(h, 2, 2);
    const question =
      kind === 'off'
        ? 'お休みは 22 日(木)の一日中でしょうか？'
        : '特別訪問週間は、どの週に入れましょう？';
    emit(h, {
      type: 'question',
      question_id: qid,
      questions: [
        {
          header: '確認',
          question,
          options:
            kind === 'off'
              ? [{ label: '一日中' }, { label: '午前だけ' }, { label: '午後だけ' }]
              : [{ label: '来週' }, { label: '再来週' }],
          multiSelect: false,
        },
      ],
    });
    emit(h, { type: 'status', state: 'waiting_answer', pose: 'waiting', line: '教えていただけますか' });
    const ans = await waitUser(h);
    if (h.aborted) throw new Error('stop');
    emit(h, {
      type: 'question.answered',
      question_id: qid,
      answers: { [question]: ans },
      user_id: 'mock-user',
      device: 'pc',
    });
    emit(h, { type: 'status', state: 'working', pose: 'search', line: '調べています' });
    await step(h, '振り分け方を考えています…', 1400, 'laptop');
    await step(h, '案をまとめています…', 1000);
    const mk = (title: string, rows: Array<[string, string, string]>): MockPlan => {
      const version = ++h.version;
      h.html.set(version, { a4: paper(title, rows, 'a4'), a3: paper(title, rows, 'a3') });
      return { plan_id: `pl-${h.summary.id}-${version}`, version, title, rows };
    };
    const p1 = mk('22日(木) 田中さんお休みの組み替え', [
      ['鈴木', '午前 2 件', '午前 3 件'],
      ['高橋', '午後 1 件', '午後 2 件'],
      ['佐藤', '午前 1 件', '午前 2 件'],
    ]);
    emit(h, planEvent(p1, h.summary.id));
    emit(h, { type: 'approval.request', kind: 'apply', version: p1.version, plan_id: p1.plan_id });
    // 作り直し(移動を少なく)。新しい版が出ると前の版は反映できない(中継と同じ)
    const p2 = mk('22日(木) 田中さんお休みの組み替え・移動を少なく', [
      ['鈴木', '午前 2 件', '午前 4 件'],
      ['佐藤', '午前 1 件', '午前 2 件'],
    ]);
    emit(h, planEvent(p2, h.summary.id));
    emit(h, { type: 'approval.request', kind: 'apply', version: p2.version, plan_id: p2.plan_id });
    summary(h, 4, 5);
    await say(h, '案ができました。広げてお見せしますね。気になるところがあれば、遠慮なくおっしゃってください。');
    turnEnd(h, 8);
    emit(h, { type: 'status', state: 'waiting_approval', pose: 'propose', line: '案を見て、決めてください' });
    await runApproval(h, p2);
  }

  const relay: AgentRelay & { __hubs: Map<string, Hub> } = {
    __hubs: hubs,
    async listSessions() {
      return [...hubs.values()].map((h) => h.summary);
    },
    async createSession() {
      const h = newHub('新しい相談');
      return h.summary;
    },
    async renameSession(id, title) {
      const h = hub(id);
      h.summary = { ...h.summary, title };
    },
    async send(id, text, o) {
      const h = hub(id);
      if (h.summary.title === '新しい相談') h.summary = { ...h.summary, title: text.slice(0, 20) };
      emit(h, {
        type: 'user.message',
        text,
        user_id: 'mock-user',
        device: o?.device ?? 'pc',
        ...(o?.planRef != null ? { plan_ref: o.planRef } : {}),
      });
      if (h.running) return; // 作業中の追加の文は同じターンに入る(本物と同じ)
      h.running = true;
      h.aborted = false;
      h.turn += 1;
      void scenario(h, text)
        .catch(() => {
          turnEnd(h, 0, false);
          emit(h, { ...IDLE, line: '止めました' });
        })
        .finally(() => {
          h.running = false;
        });
    },
    async stop(id) {
      const h = hub(id);
      h.aborted = true;
      h.waiters.splice(0).forEach((w) => w('stop'));
    },
    async answer(id, _q, answers) {
      hub(id)
        .waiters.splice(0)
        .forEach((w) => w(Object.values(answers).join(', ')));
    },
    async decide(id, version, decision) {
      hub(id)
        .waiters.splice(0)
        .forEach((w) => w(`${decision}:${version}`));
    },
    async undo(id, runId, device) {
      const h = hub(id);
      h.turn += 1;
      emit(h, {
        type: 'approval.result',
        version: 0,
        kind: 'undo',
        decision: 'approve',
        user_id: 'mock-user',
        device,
        run_id: runId,
      });
      void runRun(h, 'undo', 1).then(() => {
        turnEnd(h, 1);
        emit(h, IDLE);
      });
    },
    async planHtml(id, version, size) {
      return hub(id).html.get(version)?.[size] ?? '';
    },
    async printUrl() {
      return 'about:blank';
    },
    async stream(id, after, s: StreamHandlers) {
      const h = hub(id);
      s.onActivity?.();
      h.log.filter((e) => e.seq > after).forEach((e) => s.onEvent(e));
      await new Promise<void>((resolve) => {
        const f = (ev: AgentEvent) => {
          s.onActivity?.();
          s.onEvent(ev);
        };
        h.subs.add(f);
        const done = () => {
          h.subs.delete(f);
          resolve();
        };
        if (s.signal.aborted) done();
        else s.signal.addEventListener('abort', done, { once: true });
      });
    },
  };

  if (opts.seed) {
    const day = 86_400_000;
    const seeds: Array<[string, number, boolean]> = [
      ['10/22 田中さんのお休み', 0, true],
      ['来週の人数の確認', 2 * 3_600_000, false],
      ['山本様の特別訪問週間', day + 3_600_000, false],
      ['週 43 のならし', 3 * day, false],
      ['9月の振り返り', 20 * day, false],
    ];
    for (const [title, age, needs] of seeds) {
      const h = newHub(title, age, needs);
      emit(h, { type: 'user.message', text: title, user_id: 'mock-user', device: 'pc' });
      emit(h, { type: 'message', text: '承知しました。確かめてお伝えしますね。' });
      h.summary = { ...h.summary, last_active_at: new Date(Date.now() - age).toISOString() };
      h.log = h.log.map((e) => ({ ...e, ts: h.summary.last_active_at }));
    }
  }
  return relay;
}
