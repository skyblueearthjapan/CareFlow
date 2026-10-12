/**
 * 中継(agent-relay)のクライアント。設計書 §1-7・§4-1。形は agent/relay/public.py と同じにする。
 * - ブラウザーの鍵は NextAuth の accessToken(backend の access JWT)をそのまま Bearer で送る。
 * - SSE は fetch + ReadableStream で読む(EventSource は Authorization を付けられない)。
 * - 承認は「この版で反映する」を中継へ送るだけ。承認番号(ap_…)は中継の中で作られ、CLI へも中継が直接渡す。
 *   画面は承認番号を受け取らず、送りもしない。
 * - 画面は AgentRelay インターフェースだけを見る。mockRelay も同じ形。
 */
import type { ScreenContext } from './screenContext';
import type { AgentEvent, Device, PlanSize, SessionSummary } from './types';

/** GET /engine の、画面が使う分だけ(disk はサーバーの空き。warning は日本語でそのまま出せる) */
export interface EngineInfo {
  default_provider?: string;
  disk?: { free_gb: number; low: boolean; blocked: boolean; warning: string | null };
}

export interface SendOptions {
  planRef?: number;
  device?: Device;
  /** 画面の文脈(AGENTS.md §11)。ID だけ。中継がそのターンの頭に 1 行付ける */
  context?: ScreenContext | null;
}

export interface StreamHandlers {
  onEvent: (ev: AgentEvent) => void;
  /** 何か届いた(ping を含む)。無音の見張りに使う。 */
  onActivity?: () => void;
  signal: AbortSignal;
}

export interface AgentRelay {
  listSessions(): Promise<SessionSummary[]>;
  createSession(): Promise<SessionSummary>;
  renameSession(id: string, title: string): Promise<void>;
  /** planRef = 「案 N について」の版番号 */
  send(id: string, text: string, opts?: SendOptions): Promise<void>;
  stop(id: string): Promise<void>;
  /** answers = {質問の文: 答え}(複数選ぶ時は「, 」でつなぐ) */
  answer(id: string, questionId: string, answers: Record<string, string>, device?: Device): Promise<void>;
  /** 案の版で反映する／やめる */
  decide(id: string, version: number, decision: 'approve' | 'reject', device: Device): Promise<void>;
  /** 反映を元に戻す(承認) */
  undo(id: string, runId: string, device: Device): Promise<void>;
  planHtml(id: string, version: number, size: PlanSize): Promise<string>;
  printUrl(id: string, version: number, size: PlanSize): Promise<string>;
  /** 閉じる(または signal が中断される)まで続く。切れたら reject/resolve のどちらでもよい。 */
  stream(id: string, after: number, h: StreamHandlers): Promise<void>;
  /** 既定の AI・サーバーの空き(無い実装は出さない) */
  engine?(): Promise<EngineInfo>;
}

export const DEFAULT_RELAY_BASE = '/agent-relay';

/**
 * 中継の置き場所。本番は同じオリジンの /agent-relay(Cloudflare の道)。
 * staging だけ NEXT_PUBLIC_AGENT_RELAY_BASE=http://127.0.0.1:18211/agent-relay(SSH の転送)で別オリジンにする
 * (中継は AGENT_PUBLIC_ORIGIN の Origin だけ CORS を通す)。
 */
export function relayBaseFromEnv(v: string | undefined = process.env.NEXT_PUBLIC_AGENT_RELAY_BASE): string {
  const t = (v ?? '').trim().replace(/\/+$/, '');
  return t || DEFAULT_RELAY_BASE;
}

export class RelayError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'RelayError';
  }
}

/** 中継の会話の行 → 一覧の 1 行 */
interface RelaySession {
  id: string;
  title?: string | null;
  last_active_at?: string | null;
  created_at?: string | null;
  status?: string | null;
  needs_you?: boolean;
  pinned?: boolean;
}

export function toSummary(s: RelaySession): SessionSummary {
  return {
    id: s.id,
    title: s.title || '新しい相談',
    last_active_at: s.last_active_at ?? s.created_at ?? new Date().toISOString(),
    needs_you:
      s.needs_you ?? (s.status === 'waiting_answer' || s.status === 'waiting_approval'),
    pinned: !!s.pinned,
  };
}

/** SSE のテキストを 1 件ずつの data(JSON 文字列)に切り出す。コメント行(: ping)は無視する。 */
export function createSseParser(onData: (data: string) => void, onActivity?: () => void) {
  let buf = '';
  return (chunk: string) => {
    onActivity?.();
    buf += chunk.replace(/\r\n/g, '\n');
    let i: number;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = block
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).replace(/^ /, ''))
        .join('\n');
      if (data) onData(data);
    }
  };
}

async function errorOf(res: Response): Promise<RelayError> {
  let msg = `relay ${res.status}`;
  try {
    const d = (await res.json()) as { detail?: unknown };
    if (typeof d?.detail === 'string' && d.detail) msg = d.detail;
  } catch {
    /* 本文が JSON でない */
  }
  return new RelayError(msg, res.status);
}

const enc = encodeURIComponent;

export class HttpRelay implements AgentRelay {
  constructor(
    private readonly getToken: () => string | null | undefined,
    private readonly base: string = DEFAULT_RELAY_BASE,
    private readonly device: Device = 'pc',
  ) {}

  private headers(extra?: Record<string, string>): Headers {
    const h = new Headers(extra);
    const t = this.getToken();
    if (t) h.set('Authorization', `Bearer ${t}`);
    return h;
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = this.headers(
      init.body ? { 'Content-Type': 'application/json' } : undefined,
    );
    const res = await fetch(`${this.base}${path}`, { ...init, headers, cache: 'no-store' });
    if (!res.ok) throw await errorOf(res);
    const text = await res.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  private post<T = unknown>(path: string, body: unknown = {}) {
    return this.json<T>(path, { method: 'POST', body: JSON.stringify(body) });
  }

  async listSessions() {
    const list = await this.json<RelaySession[]>('/sessions');
    return (list ?? []).map(toSummary);
  }
  async createSession() {
    return toSummary(await this.post<RelaySession>('/sessions', {}));
  }
  async renameSession(id: string, title: string) {
    await this.json(`/sessions/${enc(id)}`, { method: 'PATCH', body: JSON.stringify({ title }) });
  }
  async send(id: string, text: string, opts?: SendOptions) {
    const body: Record<string, unknown> = { text, device: opts?.device ?? this.device };
    if (opts?.planRef != null) body.plan_ref = opts.planRef;
    if (opts?.context) body.context = opts.context;
    await this.post(`/sessions/${enc(id)}/messages`, body);
  }
  async engine() {
    return this.json<EngineInfo>('/engine');
  }
  async stop(id: string) {
    await this.post(`/sessions/${enc(id)}/stop`, {});
  }
  async answer(id: string, questionId: string, answers: Record<string, string>, device?: Device) {
    await this.post(`/sessions/${enc(id)}/answers`, {
      question_id: questionId,
      answers,
      device: device ?? this.device,
    });
  }
  async decide(id: string, version: number, decision: 'approve' | 'reject', device: Device) {
    await this.post(`/sessions/${enc(id)}/plans/${version}/${decision}`, { device });
  }
  async undo(id: string, runId: string, device: Device) {
    await this.post(`/sessions/${enc(id)}/runs/${enc(runId)}/undo/approve`, { device });
  }
  async planHtml(id: string, version: number, size: PlanSize) {
    const res = await fetch(`${this.base}/sessions/${enc(id)}/plans/${version}/html?size=${size}`, {
      headers: this.headers(),
      cache: 'no-store',
    });
    if (!res.ok) throw await errorOf(res);
    return res.text();
  }
  async printUrl(id: string, version: number, size: PlanSize) {
    const r = await this.post<{ url: string }>(
      `/sessions/${enc(id)}/plans/${version}/print-url`,
      { size },
    );
    // 中継が返すのは /agent-relay/print/... の道。中継が別オリジン(staging)なら、そのオリジンを付ける
    if (/^https?:\/\//.test(this.base) && r.url.startsWith('/')) return new URL(r.url, this.base).toString();
    return r.url;
  }

  async stream(id: string, after: number, h: StreamHandlers) {
    const res = await fetch(`${this.base}/sessions/${enc(id)}/events?after=${after}`, {
      headers: this.headers({ Accept: 'text/event-stream' }),
      cache: 'no-store',
      signal: h.signal,
    });
    if (!res.ok || !res.body) throw new RelayError(`relay ${res.status}`, res.status);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    const feed = createSseParser((data) => {
      try {
        h.onEvent(JSON.parse(data) as AgentEvent);
      } catch {
        /* 壊れた 1 件は捨てる */
      }
    }, h.onActivity);
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      feed(dec.decode(value, { stream: true }));
    }
  }
}
