import type {
  AgentEvent,
  AgentStore,
  PlanInfo,
  RunKind,
  SessionSummary,
  SessionView,
  StepItem,
  TimelineItem,
} from './types';

type Work = Extract<TimelineItem, { kind: 'work' }>;
type Bot = Extract<TimelineItem, { kind: 'bot' }>;
type RunItem = Extract<TimelineItem, { kind: 'run' }>;

export const initialStore: AgentStore = {
  sessions: {},
  order: [],
  currentId: null,
  connection: 'ok',
};

export type AgentAction =
  | { type: 'sessions'; list: SessionSummary[] }
  | { type: 'upsert'; session: SessionSummary }
  | { type: 'select'; id: string | null }
  | { type: 'rename'; id: string; title: string }
  | { type: 'connection'; value: 'ok' | 'lost' }
  | { type: 'event'; sessionId: string; ev: AgentEvent };

export function emptySession(s: SessionSummary): SessionView {
  return {
    ...s,
    state: 'idle',
    pose: 'wave',
    line: 'ご用件をどうぞ',
    items: [],
    plans: {},
    planOrder: [],
    lastSeq: 0,
    loaded: false,
    thinking: false,
  };
}

/** 直前の作業まとまりを畳む。失敗(⚠)を含むとき・まだ動いているときは畳まない。 */
function closeWork(items: TimelineItem[]): TimelineItem[] {
  const last = items[items.length - 1];
  if (!last || last.kind !== 'work' || last.collapsed) return items;
  if (last.steps.some((s) => !s.done)) return items;
  if (last.steps.some((s) => s.ok === false)) return items;
  const secs = last.steps.reduce((a, s) => a + (s.secs ?? 0), 0);
  return [...items.slice(0, -1), { ...last, collapsed: true, secs: Math.round(secs) }];
}

function pushItem(items: TimelineItem[], it: TimelineItem): TimelineItem[] {
  return [...closeWork(items), it];
}

export function sessionList(store: AgentStore): SessionView[] {
  return store.order.flatMap((id) => {
    const s = store.sessions[id];
    return s ? [s] : [];
  });
}

export function plansOf(s: SessionView): PlanInfo[] {
  return s.planOrder.flatMap((id) => {
    const p = s.plans[id];
    return p ? [p] : [];
  });
}

export function workLabel(w: Work): string {
  return `作業 ${w.steps.length} 手順・${w.secs} 秒`;
}

function replaceAt(items: TimelineItem[], idx: number, it: TimelineItem): TimelineItem[] {
  const next = items.slice();
  next[idx] = it;
  return next;
}

/** 末尾の流れている途中の文(message.delta が足し込む先・message が確定させる先)。無ければ -1。 */
function trailingStreamingBot(items: TimelineItem[]): number {
  for (let i = items.length - 1; i >= 0; i--) {
    const it = items[i]!;
    if (it.kind === 'bot') return it.streaming ? i : -1;
    if (it.kind === 'user') return -1;
  }
  return -1;
}

export function deviceLabel(d?: string): string | undefined {
  if (!d) return undefined;
  return d === 'mobile' ? 'スマホ' : 'PC';
}

/** run.result の 1 行(中継は数だけ送る。言葉は画面が決める)。 */
export function runLine(
  kind: RunKind,
  ok: boolean,
  applied: number,
  total: number,
  error?: string | null,
): string {
  if (ok) return kind === 'undo' ? `元に戻しました（${applied} 件）` : `${applied} 件を反映しました`;
  const head =
    applied > 0
      ? `${applied} 件目までは反映済み・そこで止まりました`
      : `${total} 件のうち、反映できたものはありません`;
  return error ? `${head}: ${error}` : head;
}

const DECISION = { approve: 'approved', reject: 'rejected', expired: 'expired' } as const;

function applyEvent(s: SessionView, ev: AgentEvent): SessionView {
  switch (ev.type) {
    case 'status':
      return {
        ...s,
        state: ev.state,
        pose: ev.pose ?? s.pose,
        line: ev.line ?? s.line,
        needs_you: ev.state === 'waiting_answer' || ev.state === 'waiting_approval',
      };
    case 'user.message': {
      const ref =
        ev.plan_ref != null
          ? s.planOrder.find((k) => s.plans[k]?.version === ev.plan_ref)
          : undefined;
      return {
        ...s,
        items: pushItem(s.items, { kind: 'user', id: `u${ev.seq}`, text: ev.text, planRef: ref }),
      };
    }
    case 'message.delta': {
      const idx = trailingStreamingBot(s.items);
      if (idx >= 0) {
        const cur = s.items[idx] as Bot;
        return { ...s, items: replaceAt(s.items, idx, { ...cur, text: cur.text + ev.text }) };
      }
      return {
        ...s,
        items: pushItem(s.items, { kind: 'bot', id: `b${ev.seq}`, text: ev.text, streaming: true }),
      };
    }
    case 'message': {
      const idx = trailingStreamingBot(s.items);
      if (idx >= 0) {
        const cur = s.items[idx] as Bot;
        return {
          ...s,
          items: replaceAt(s.items, idx, { ...cur, text: ev.text, streaming: false }),
        };
      }
      return {
        ...s,
        items: pushItem(s.items, { kind: 'bot', id: `b${ev.seq}`, text: ev.text, streaming: false }),
      };
    }
    case 'thinking':
      return { ...s, thinking: ev.phase === 'start' };
    case 'step.start': {
      const last = s.items[s.items.length - 1];
      const step: StepItem = { id: ev.step_id, label: ev.label, done: false };
      if (last && last.kind === 'work' && !last.collapsed) {
        return {
          ...s,
          items: [...s.items.slice(0, -1), { ...last, steps: [...last.steps, step] }],
        };
      }
      return {
        ...s,
        items: [
          ...s.items,
          { kind: 'work', id: `w${ev.seq}`, steps: [step], collapsed: false, secs: 0 },
        ],
      };
    }
    case 'step.end':
      return {
        ...s,
        items: s.items.map((i) =>
          i.kind === 'work'
            ? {
                ...i,
                steps: i.steps.map((st) =>
                  st.id === ev.step_id ? { ...st, done: true, ok: ev.ok, secs: ev.secs } : st,
                ),
              }
            : i,
        ),
      };
    case 'steps.summary': {
      const last = s.items[s.items.length - 1];
      if (last && last.kind === 'work') {
        const failed = last.steps.some((st) => st.ok === false) || (ev.failed ?? 0) > 0;
        const secs = Math.round(ev.secs ?? last.steps.reduce((a, st) => a + (st.secs ?? 0), 0));
        return {
          ...s,
          items: [...s.items.slice(0, -1), { ...last, collapsed: !failed, secs }],
        };
      }
      return s;
    }
    case 'progress':
      return { ...s, line: ev.text };
    case 'question':
      return {
        ...s,
        needs_you: true,
        items: pushItem(s.items, {
          kind: 'question',
          id: `q${ev.question_id}`,
          questionId: ev.question_id,
          questions: ev.questions,
        }),
      };
    case 'question.answered':
      return {
        ...s,
        items: s.items.map((i) =>
          i.kind === 'question' && i.questionId === ev.question_id
            ? { ...i, answers: Object.values(ev.answers ?? {}), by: deviceLabel(ev.device) }
            : i,
        ),
      };
    case 'plan': {
      const key = ev.plan_id;
      const plan: PlanInfo = {
        ...s.plans[key],
        plan_id: ev.plan_id,
        version: ev.version,
        title: ev.title,
        summary: ev.summary ?? [],
        counts: ev.counts,
        undoable: ev.undoable,
        ready: ev.ready,
        blockers: ev.blockers,
        status: ev.status,
        html_urls: ev.html_urls,
        label: `案 ${ev.version}`,
      };
      const plans: Record<string, PlanInfo> = { ...s.plans, [key]: plan };
      // 新しい版が来たら、前の版(まだ決まっていないもの)は「前の案」にする
      for (const k of s.planOrder) {
        const p = plans[k];
        if (
          p &&
          k !== key &&
          p.version < ev.version &&
          (p.status === 'draft' || p.status === 'approved')
        ) {
          plans[k] = { ...p, status: 'superseded' };
        }
      }
      const dead = ev.status === 'stale' || ev.status === 'superseded' || ev.status === 'rejected';
      let items = s.items.map((i) =>
        i.kind === 'approval' &&
        !i.decision &&
        (i.version < ev.version || (dead && i.version === ev.version))
          ? { ...i, decision: 'expired' as const }
          : i,
      );
      if (!items.some((i) => i.kind === 'plan' && i.planId === key)) {
        items = pushItem(items, { kind: 'plan', id: `p${key}`, planId: key });
      }
      const planOrder = s.planOrder.includes(key) ? s.planOrder : [...s.planOrder, key];
      return { ...s, plans, planOrder, items };
    }
    case 'approval.request': {
      if (ev.kind !== 'apply') return s;
      const approvalId = `v${ev.version}`;
      if (s.items.some((i) => i.kind === 'approval' && i.approvalId === approvalId)) return s;
      return {
        ...s,
        needs_you: true,
        items: pushItem(s.items, {
          kind: 'approval',
          id: `a${approvalId}`,
          approvalId,
          planId: ev.plan_id,
          version: ev.version,
        }),
      };
    }
    case 'approval.result': {
      if (ev.kind === 'undo') {
        return {
          ...s,
          items: s.items.map((i) =>
            i.kind === 'run' && i.runKind === 'apply' && (!ev.run_id || i.runId === ev.run_id)
              ? { ...i, undoRequested: true }
              : i,
          ),
        };
      }
      return {
        ...s,
        items: s.items.map((i) =>
          i.kind === 'approval' && i.version === ev.version
            ? { ...i, decision: DECISION[ev.decision], by: deviceLabel(ev.device) }
            : i,
        ),
      };
    }
    case 'run.progress': {
      const idx = s.items.findIndex((i) => i.kind === 'run' && !i.finished);
      const prev = idx >= 0 ? (s.items[idx] as RunItem) : undefined;
      const run: TimelineItem = {
        kind: 'run',
        id: prev?.id ?? `r${ev.seq}`,
        runKind: ev.run_kind,
        done: ev.i,
        total: ev.total,
        line: `${ev.i} / ${ev.total} 件目${ev.label ? `: ${ev.label}` : ''}`,
        finished: false,
      };
      return { ...s, items: idx >= 0 ? replaceAt(s.items, idx, run) : pushItem(s.items, run) };
    }
    case 'run.result': {
      const idx = s.items.findIndex((i) => i.kind === 'run' && !i.finished);
      const prev = idx >= 0 ? (s.items[idx] as RunItem) : undefined;
      const run: TimelineItem = {
        kind: 'run',
        id: prev?.id ?? `r${ev.seq}`,
        runKind: ev.run_kind,
        runId: ev.run_id,
        ok: ev.ok,
        done: ev.applied,
        total: ev.total,
        line: runLine(ev.run_kind, ev.ok, ev.applied, ev.total, ev.error),
        undoable: ev.undoable,
        finished: true,
      };
      let items = idx >= 0 ? replaceAt(s.items, idx, run) : pushItem(s.items, run);
      if (ev.run_kind === 'undo' && ev.ok) {
        // 元に戻した反映は、もう一度は戻せない
        items = items.map((i) =>
          i.kind === 'run' && i.runKind === 'apply' && i.undoRequested
            ? { ...i, undoable: false }
            : i,
        );
      }
      return { ...s, items };
    }
    case 'error':
      return {
        ...s,
        state: 'error',
        pose: 'waiting',
        items: pushItem(s.items, {
          kind: 'error',
          id: `e${ev.seq}`,
          line: ev.message,
          retry: !!ev.retry,
        }),
      };
    case 'turn.end':
      return {
        ...s,
        items: closeWork(s.items).map((i) =>
          i.kind === 'bot' && i.streaming ? { ...i, streaming: false } : i,
        ),
        thinking: false,
      };
    default:
      return s;
  }
}

export function agentReducer(store: AgentStore, action: AgentAction): AgentStore {
  switch (action.type) {
    case 'sessions': {
      const sessions = { ...store.sessions };
      for (const sm of action.list) {
        const prev = sessions[sm.id];
        sessions[sm.id] = prev ? { ...prev, ...sm } : emptySession(sm);
      }
      return { ...store, sessions, order: action.list.map((x) => x.id) };
    }
    case 'upsert': {
      const prev = store.sessions[action.session.id];
      const sessions = {
        ...store.sessions,
        [action.session.id]: prev ? { ...prev, ...action.session } : emptySession(action.session),
      };
      const order = store.order.includes(action.session.id)
        ? store.order
        : [action.session.id, ...store.order];
      return { ...store, sessions, order };
    }
    case 'select':
      return { ...store, currentId: action.id };
    case 'rename': {
      const s = store.sessions[action.id];
      if (!s) return store;
      return {
        ...store,
        sessions: { ...store.sessions, [action.id]: { ...s, title: action.title } },
      };
    }
    case 'connection':
      return store.connection === action.value ? store : { ...store, connection: action.value };
    case 'event': {
      const s = store.sessions[action.sessionId];
      if (!s) return store;
      if (action.ev.seq <= s.lastSeq) return store; // つなぎ直しの二重受信は捨てる
      const next = applyEvent(s, action.ev);
      return {
        ...store,
        sessions: {
          ...store.sessions,
          [action.sessionId]: {
            ...next,
            lastSeq: action.ev.seq,
            loaded: true,
            last_active_at: action.ev.ts ?? next.last_active_at,
          },
        },
      };
    }
    default:
      return store;
  }
}
