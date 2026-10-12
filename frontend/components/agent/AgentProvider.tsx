'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from 'react';
import { useSession } from 'next-auth/react';

import { createMockRelay } from './mockRelay';
import { agentReducer, initialStore } from './reducer';
import { HttpRelay, relayBaseFromEnv, type AgentRelay } from './relay';
import { streamWithReconnect } from './stream';
import type { AgentStore, PlanSize, SessionView } from './types';

export type AgentDevice = 'pc' | 'mobile';

export interface AgentContextValue {
  store: AgentStore;
  device: AgentDevice;
  current: SessionView | null;
  /** 「決めてほしいもの」の数(丸の点・タブの題名・バナー用。名前は持たない) */
  waitingCount: number;
  select: (id: string | null) => void;
  newSession: () => Promise<string>;
  rename: (id: string, title: string) => Promise<void>;
  send: (text: string, planRef?: string) => Promise<void>;
  stop: () => Promise<void>;
  answer: (questionId: string, answers: string[]) => Promise<void>;
  /** approvalId は画面の中の鍵(`v<版>`)。承認番号ではない */
  decide: (approvalId: string, decision: 'approve' | 'reject') => Promise<void>;
  /** 反映を元に戻す(run.result の run_id) */
  undo: (runId: string) => Promise<void>;
  planHtml: (planId: string, size: PlanSize) => Promise<string>;
  printUrl: (planId: string, size: PlanSize) => Promise<string>;
}

const Ctx = createContext<AgentContextValue | null>(null);

export function useAgent(): AgentContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAgent は AgentProvider の中で使います');
  return v;
}

/** Provider が無い所(機能が無効な時)では null。ページ側が「ホームへ戻す」判断に使う。 */
export function useAgentMaybe(): AgentContextValue | null {
  return useContext(Ctx);
}

interface Props {
  children: React.ReactNode;
  device: AgentDevice;
  /** 試験・開発用に差し替える(未指定: NEXT_PUBLIC_AGENT_RELAY_MOCK=1 なら模擬、それ以外は本物の中継) */
  relay?: AgentRelay;
}

export function AgentProvider({ children, device, relay: relayProp }: Props) {
  const { data: session } = useSession();
  const tokenRef = useRef<string | null>(null);
  tokenRef.current = session?.accessToken ?? null;

  const relay = useMemo<AgentRelay>(() => {
    if (relayProp) return relayProp;
    if (process.env.NEXT_PUBLIC_AGENT_RELAY_MOCK === '1') return createMockRelay({ seed: true });
    return new HttpRelay(() => tokenRef.current, relayBaseFromEnv(), device);
  }, [relayProp, device]);

  const [store, dispatch] = useReducer(agentReducer, initialStore);
  const storeRef = useRef(store);
  storeRef.current = store;

  const rootAbort = useRef<AbortController | null>(null);
  const streams = useRef(new Set<string>());

  const ensureStream = useCallback(
    (id: string) => {
      if (streams.current.has(id) || !rootAbort.current) return;
      streams.current.add(id);
      void streamWithReconnect({
        relay,
        sessionId: id,
        signal: rootAbort.current.signal,
        getAfter: () => storeRef.current.sessions[id]?.lastSeq ?? 0,
        onEvent: (ev) => dispatch({ type: 'event', sessionId: id, ev }),
        onConnection: (ok) => dispatch({ type: 'connection', value: ok ? 'ok' : 'lost' }),
      });
    },
    [relay],
  );

  useEffect(() => {
    const ac = new AbortController();
    rootAbort.current = ac;
    const live = streams.current;
    relay
      .listSessions()
      .then((list) => {
        if (!ac.signal.aborted) dispatch({ type: 'sessions', list });
      })
      .catch(() => dispatch({ type: 'connection', value: 'lost' }));
    return () => {
      ac.abort();
      live.clear();
    };
  }, [relay]);

  const currentId = store.currentId;
  useEffect(() => {
    if (currentId && store.sessions[currentId]) ensureStream(currentId);
  }, [currentId, store.sessions, ensureStream]);

  const select = useCallback((id: string | null) => dispatch({ type: 'select', id }), []);

  const newSession = useCallback(async () => {
    const s = await relay.createSession();
    dispatch({ type: 'upsert', session: s });
    dispatch({ type: 'select', id: s.id });
    return s.id;
  }, [relay]);

  const rename = useCallback(
    async (id: string, title: string) => {
      dispatch({ type: 'rename', id, title });
      await relay.renameSession(id, title);
    },
    [relay],
  );

  const cur = () => storeRef.current.currentId;
  const curSession = () => {
    const id = cur();
    return id ? storeRef.current.sessions[id] : undefined;
  };
  /** 画面の案の鍵(plan_id) → 中継の版番号 */
  const versionOf = (planId: string): number | undefined =>
    curSession()?.plans[planId]?.version;

  const send = useCallback(
    async (text: string, planRef?: string) => {
      const t = text.trim();
      if (!t) return;
      let id = storeRef.current.currentId;
      if (!id) id = await newSession();
      const v = planRef ? storeRef.current.sessions[id]?.plans[planRef]?.version : undefined;
      await relay.send(id, t, { planRef: v, device });
    },
    [relay, newSession, device],
  );

  const stop = useCallback(async () => {
    const id = cur();
    if (id) await relay.stop(id);
  }, [relay]);
  const answer = useCallback(
    async (questionId: string, answers: string[]) => {
      const s = curSession();
      if (!s) return;
      const q = s.items.find((i) => i.kind === 'question' && i.questionId === questionId);
      const text = q && q.kind === 'question' ? q.questions[0]?.question : undefined;
      if (!text) return;
      await relay.answer(s.id, questionId, { [text]: answers.join(', ') }, device);
    },
    [relay, device],
  );
  const decide = useCallback(
    async (approvalId: string, decision: 'approve' | 'reject') => {
      const s = curSession();
      if (!s) return;
      const it = s.items.find((i) => i.kind === 'approval' && i.approvalId === approvalId);
      if (!it || it.kind !== 'approval') return;
      await relay.decide(s.id, it.version, decision, device);
    },
    [relay, device],
  );
  const undo = useCallback(
    async (runId: string) => {
      const id = cur();
      if (id) await relay.undo(id, runId, device);
    },
    [relay, device],
  );
  const planHtml = useCallback(
    async (planId: string, size: PlanSize) => {
      const id = cur();
      const v = versionOf(planId);
      return id && v != null ? relay.planHtml(id, v, size) : '';
    },
    [relay],
  );
  const printUrl = useCallback(
    async (planId: string, size: PlanSize) => {
      const id = cur();
      const v = versionOf(planId);
      return id && v != null ? relay.printUrl(id, v, size) : '';
    },
    [relay],
  );

  const current = currentId ? (store.sessions[currentId] ?? null) : null;
  const waitingCount = useMemo(
    () => Object.values(store.sessions).filter((s) => s.needs_you).length,
    [store.sessions],
  );

  const value = useMemo<AgentContextValue>(
    () => ({
      store,
      device,
      current,
      waitingCount,
      select,
      newSession,
      rename,
      send,
      stop,
      answer,
      decide,
      undo,
      planHtml,
      printUrl,
    }),
    [store, device, current, waitingCount, select, newSession, rename, send, stop, answer, decide, undo, planHtml, printUrl],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
