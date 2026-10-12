import type { AgentRelay } from './relay';
import type { AgentEvent } from './types';

export interface StreamOptions {
  relay: AgentRelay;
  sessionId: string;
  getAfter: () => number;
  onEvent: (ev: AgentEvent) => void;
  onConnection: (ok: boolean) => void;
  signal: AbortSignal;
  /** テスト用に短くできる */
  silenceMs?: number;
  minDelayMs?: number;
  maxDelayMs?: number;
}

/**
 * SSE を張り続ける。切れたら 3→30 秒で張り直し・45 秒無音で張り直し・
 * 画面に戻ったら(visibilitychange)即つなぎ直す。続きは ?after=<seq> から。
 * outer の signal が中断されたら終わる。
 */
export async function streamWithReconnect(o: StreamOptions): Promise<void> {
  const silenceMs = o.silenceMs ?? 45_000;
  const minDelay = o.minDelayMs ?? 3_000;
  const maxDelay = o.maxDelayMs ?? 30_000;
  let delay = minDelay;
  let attempt: AbortController | null = null;

  const onVisible = () => {
    if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
      attempt?.abort(); // 張り直しへ
    }
  };
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);

  try {
    while (!o.signal.aborted) {
      attempt = new AbortController();
      const ac = attempt;
      const onOuterAbort = () => ac.abort();
      o.signal.addEventListener('abort', onOuterAbort);
      let watch: ReturnType<typeof setTimeout> | undefined;
      const arm = () => {
        clearTimeout(watch);
        watch = setTimeout(() => ac.abort(), silenceMs);
      };
      arm();
      let gotAny = false;
      try {
        await o.relay.stream(o.sessionId, o.getAfter(), {
          signal: ac.signal,
          onActivity: () => {
            arm();
            if (!gotAny) {
              gotAny = true;
              delay = minDelay;
              o.onConnection(true);
            }
          },
          onEvent: (ev) => {
            arm();
            if (!gotAny) {
              gotAny = true;
              delay = minDelay;
              o.onConnection(true);
            }
            o.onEvent(ev);
          },
        });
      } catch {
        /* 切れた・中断された: 下で張り直す */
      } finally {
        clearTimeout(watch);
        o.signal.removeEventListener('abort', onOuterAbort);
      }
      if (o.signal.aborted) break;
      if (!gotAny) o.onConnection(false);
      // 画面に戻った時の中断は待たずにすぐ張り直す
      const waited = gotAny ? 0 : delay;
      if (!gotAny) delay = Math.min(maxDelay, delay * 2);
      if (waited > 0) {
        await new Promise<void>((res) => {
          const t = setTimeout(res, waited);
          o.signal.addEventListener(
            'abort',
            () => {
              clearTimeout(t);
              res();
            },
            { once: true },
          );
        });
      }
    }
  } finally {
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
  }
}
