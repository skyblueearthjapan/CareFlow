'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import { usePathname } from 'next/navigation';
import { useSession } from 'next-auth/react';

import type { EngineAdmin } from './engines';
import { createMockRelay } from './mockRelay';
import { agentReducer, initialStore } from './reducer';
import { HttpRelay, relayBaseFromEnv, type AgentRelay } from './relay';
import { currentScreenContext } from './screenContext';
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
  /** サーバーの空きが少ない時の一言(管理者向け・ウィジェット自体が管理者だけ)。無ければ null */
  diskWarning: string | null;
  /** AI のつなぎ先の設定(⚙ 設定)。中継が持たない時は null */
  engines: EngineAdmin | null;
}

/** 空きが少ない(10 GB 未満)時の、落ち着いた言い方。新しい相談を断る段(5 GB 未満)は中継の文をそのまま出す */
export const DISK_LOW_JA = 'サーバーの空きが少なくなっています。管理者にお知らせください。';
/** 空きの見直しの間隔(ミリ秒) */
const ENGINE_POLL_MS = 10 * 60 * 1000;

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
  const [diskWarning, setDiskWarning] = useState<string | null>(null);

  // 話しかけた画面の道。携帯は会話が /m/agent にあるので、その前に見ていた画面を覚えておく
  const pathname = usePathname();
  const lastScreen = useRef<{ path: string; search: string } | null>(null);
  useEffect(() => {
    if (!pathname || pathname.startsWith('/m/agent')) return;
    lastScreen.current = { path: pathname, search: window.location.search };
  }, [pathname]);
  const screenContext = useCallback(() => {
    if (device === 'mobile') {
      const l = lastScreen.current;
      return l ? currentScreenContext(l.path, l.search) : null;
    }
    return currentScreenContext(window.location.pathname, window.location.search);
  }, [device]);

  useEffect(() => {
    if (!relay.engine) return;
    let alive = true;
    const load = () =>
      relay
        .engine?.()
        .then((info) => {
          if (!alive) return;
          const d = info?.disk;
          setDiskWarning(d?.blocked ? (d.warning ?? DISK_LOW_JA) : d?.low ? DISK_LOW_JA : null);
        })
        .catch(() => {
          /* 見られなくても会話はできる */
        });
    void load();
    const t = setInterval(() => void load(), ENGINE_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [relay]);
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
      await relay.send(id, t, { planRef: v, device, context: screenContext() });
    },
    [relay, newSession, device, screenContext],
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
      diskWarning,
      engines: relay.engines ?? null,
    }),
    [store, device, current, waitingCount, select, newSession, rename, send, stop, answer, decide, undo, planHtml, printUrl, diskWarning, relay],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
