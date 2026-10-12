/**
 * AI のつなぎ先(ChatGPT = Codex / Claude)の設定: ログインの様子・画面からのサインイン/サインアウト・既定の AI。
 * 形は agent/relay/public.py の /engines* と同じにする。管理者だけ(中継が /auth/me で確かめる)。
 * 中継はトークン・メールを返さない。画面に来るのは状態の語と、サインイン用の URL・1 回限りのコードだけ。
 * 人ごとの席(PO 2026-10-13: ChatGPT Business/Team は 1 席 1 人): どれも「開いている本人」の AI のアカウント。
 */

export type EngineProvider = 'codex' | 'claude';
/** ok = サインイン済み / missing = していない / api_key = 使えないつなぎ方 / unknown = 分からない */
export type EngineState = 'ok' | 'missing' | 'api_key' | 'unknown';
export type LoginState =
  | 'idle'
  | 'starting'
  | 'waiting'
  | 'verifying'
  | 'done'
  | 'failed'
  | 'timeout'
  | 'cancelled';

export interface LoginFlow {
  provider: EngineProvider | null;
  state: LoginState;
  /** ブラウザーで開くところ(決まったホストだけ) */
  url: string | null;
  /** ChatGPT の 1 回限りのコード(XXXX-XXXXX) */
  code: string | null;
  /** Claude: ブラウザーに出たコードを貼る欄を出す */
  needs_input: boolean;
  /** 残りの秒(最長 900) */
  expires_in: number;
  error: 'invalid_code' | 'no_url' | null;
}

export interface EnginesView {
  default_provider: EngineProvider;
  /** user = この人が選んだもの / setting = 全体の既定 / env = サーバーの初期の設定 */
  default_source: 'user' | 'setting' | 'env';
  providers: EngineProvider[];
  /** 画面からサインインできる環境か(手元の開発では false) */
  can_sign_in: boolean;
  /** own = ここに出る様子・サインインは開いている本人の席(アカウント) */
  seat?: 'own';
  /** 管理者のサインインの様子(一覧)を見られるか */
  can_view_seats?: boolean;
  engines: Partial<Record<EngineProvider, { state: EngineState }>>;
  login: LoginFlow | null;
}

/** 管理者だけ: どの管理者がどの AI にサインインしているか(名前と状態の語だけ。AI のメールは来ない) */
export interface SeatsView {
  providers: EngineProvider[];
  seats: {
    name: string;
    me: boolean;
    engines: Partial<Record<EngineProvider, { state: EngineState }>>;
  }[];
}

/** カルテの下書きに使う席(管理者だけ・agent/relay/karte.py の /karte/settings と同じ形) */
export type KarteMode = 'designated' | 'presser';
export interface KarteSettings {
  /** designated = 決めた管理者の席 / presser = 録音した本人の席 */
  mode: KarteMode;
  modes: KarteMode[];
  seat_user_id: string | null;
  /** 決めた席が、いまの管理者の一覧にいるか */
  seat_known: boolean;
  /** state は ChatGPT のサインインの様子 */
  seats: { user_id: string; name: string; me: boolean; state: EngineState }[];
}

export interface EngineAdmin {
  view(): Promise<EnginesView>;
  setDefault(provider: EngineProvider): Promise<void>;
  startLogin(provider: EngineProvider): Promise<LoginFlow>;
  pollLogin(): Promise<LoginFlow>;
  sendCode(code: string): Promise<LoginFlow>;
  cancelLogin(): Promise<LoginFlow>;
  logout(
    provider: EngineProvider,
  ): Promise<{ provider: EngineProvider; ok: boolean; state: EngineState }>;
  /** 管理者のサインインの様子(無い実装では出さない) */
  seats?(): Promise<SeatsView>;
  /** カルテの下書きに使う席(無い実装では出さない) */
  karte?(): Promise<KarteSettings>;
  setKarte?(patch: { mode?: KarteMode; seat_user_id?: string | null }): Promise<KarteSettings>;
}

export const ENGINE_NAME: Record<EngineProvider, string> = { codex: 'ChatGPT', claude: 'Claude' };

export const ACTIVE_LOGIN: LoginState[] = ['starting', 'waiting', 'verifying'];

export class EngineError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = 'EngineError';
  }
}

export class HttpEngineAdmin implements EngineAdmin {
  constructor(
    private readonly getToken: () => string | null | undefined,
    private readonly base: string,
  ) {}

  private call<T>(path: string, method: 'GET' | 'POST' = 'GET', body?: unknown): Promise<T> {
    return this.send<T>(`/engines${path}`, method, body);
  }

  private async send<T>(
    path: string,
    method: 'GET' | 'POST' | 'PUT' = 'GET',
    body?: unknown,
  ): Promise<T> {
    const headers = new Headers();
    const t = this.getToken();
    if (t) headers.set('Authorization', `Bearer ${t}`);
    if (method !== 'GET') headers.set('Content-Type', 'application/json');
    const res = await fetch(`${this.base}${path}`, {
      method,
      headers,
      cache: 'no-store',
      body: method !== 'GET' ? JSON.stringify(body ?? {}) : undefined,
    });
    if (!res.ok) {
      let msg = 'うまくいきませんでした。少し時間をおいて、もう一度どうぞ';
      try {
        const d = (await res.json()) as { detail?: unknown };
        if (typeof d?.detail === 'string' && d.detail) msg = d.detail;
      } catch {
        /* 本文が JSON でない */
      }
      throw new EngineError(msg, res.status);
    }
    return (await res.json()) as T;
  }

  view() {
    return this.call<EnginesView>('');
  }
  async setDefault(provider: EngineProvider) {
    await this.call('/default', 'POST', { provider });
  }
  startLogin(provider: EngineProvider) {
    return this.call<LoginFlow>(`/${provider}/login`, 'POST');
  }
  pollLogin() {
    return this.call<LoginFlow>('/login');
  }
  sendCode(code: string) {
    return this.call<LoginFlow>('/login/code', 'POST', { code });
  }
  cancelLogin() {
    return this.call<LoginFlow>('/login/cancel', 'POST');
  }
  logout(provider: EngineProvider) {
    return this.call<{ provider: EngineProvider; ok: boolean; state: EngineState }>(
      `/${provider}/logout`,
      'POST',
    );
  }
  seats() {
    return this.call<SeatsView>('/seats');
  }
  karte() {
    return this.send<KarteSettings>('/karte/settings');
  }
  setKarte(patch: { mode?: KarteMode; seat_user_id?: string | null }) {
    return this.send<KarteSettings>('/karte/settings', 'PUT', patch);
  }
}

/** 模擬(NEXT_PUBLIC_AGENT_RELAY_MOCK=1・試験)。ChatGPT はサインイン済み、Claude はしていない状態から始まる */
export function createMockEngineAdmin(): EngineAdmin {
  const engines: Record<EngineProvider, EngineState> = { codex: 'ok', claude: 'missing' };
  let def: EngineProvider = 'codex';
  let source: EnginesView['default_source'] = 'env';
  let flow: LoginFlow | null = null;
  let karteMode: KarteMode = 'designated';
  let karteSeat: string | null = null;
  const karteView = (): KarteSettings => ({
    mode: karteMode,
    modes: ['designated', 'presser'],
    seat_user_id: karteSeat,
    seat_known: karteSeat === null || karteSeat === 'mock-me' || karteSeat === 'mock-b',
    seats: [
      { user_id: 'mock-me', name: 'あなた', me: true, state: engines.codex },
      { user_id: 'mock-b', name: '管理者 B', me: false, state: 'missing' },
    ],
  });
  const idle = (): LoginFlow => ({
    provider: null,
    state: 'idle',
    url: null,
    code: null,
    needs_input: false,
    expires_in: 0,
    error: null,
  });
  return {
    async view() {
      return {
        default_provider: def,
        default_source: source,
        providers: ['codex', 'claude'],
        can_sign_in: true,
        seat: 'own',
        can_view_seats: true,
        engines: { codex: { state: engines.codex }, claude: { state: engines.claude } },
        login: flow && flow.state !== 'idle' ? { ...flow } : null,
      };
    },
    async setDefault(p) {
      def = p;
      source = 'user';
    },
    async seats() {
      return {
        providers: ['codex', 'claude'] as EngineProvider[],
        seats: [
          {
            name: 'あなた',
            me: true,
            engines: { codex: { state: engines.codex }, claude: { state: engines.claude } },
          },
          {
            name: '管理者 B',
            me: false,
            engines: { codex: { state: 'missing' }, claude: { state: 'missing' } },
          },
        ],
      };
    },
    async karte() {
      return karteView();
    },
    async setKarte(patch) {
      if (patch.mode) karteMode = patch.mode;
      if (patch.seat_user_id !== undefined) karteSeat = patch.seat_user_id;
      return karteView();
    },
    async startLogin(p) {
      if (flow && ACTIVE_LOGIN.includes(flow.state)) {
        throw new EngineError(
          'いま別のサインインの途中です。終わるか「やめる」を押してから、もう一度どうぞ',
          409,
        );
      }
      flow = {
        provider: p,
        state: 'waiting',
        url:
          p === 'codex'
            ? 'https://auth.openai.com/codex/device'
            : 'https://claude.ai/oauth/authorize?mock=1',
        code: p === 'codex' ? 'MOCK-12345' : null,
        needs_input: p === 'claude',
        expires_in: 900,
        error: null,
      };
      // 模擬: ChatGPT は少しして「できました」になる
      if (p === 'codex') {
        setTimeout(() => {
          if (flow?.provider === 'codex' && flow.state === 'waiting') {
            flow = { ...flow, state: 'done', expires_in: 0 };
            engines.codex = 'ok';
          }
        }, 4000);
      }
      return { ...flow };
    },
    async pollLogin() {
      return flow ? { ...flow } : idle();
    },
    async sendCode(code) {
      if (!flow || flow.state !== 'waiting' || flow.provider !== 'claude') {
        throw new EngineError(
          'いまはコードを受け付けていません。もう一度サインインからどうぞ',
          409,
        );
      }
      if (!/^[A-Za-z0-9_\-#.~]{8,1024}$/.test(code.trim())) {
        throw new EngineError(
          'コードの形が違うようです。ブラウザーに出たコードを、そのまま貼り付けてください',
          400,
        );
      }
      flow = { ...flow, state: 'done', needs_input: false, expires_in: 0 };
      engines.claude = 'ok';
      return { ...flow };
    },
    async cancelLogin() {
      if (flow && ACTIVE_LOGIN.includes(flow.state))
        flow = { ...flow, state: 'cancelled', needs_input: false };
      return flow ? { ...flow } : idle();
    },
    async logout(p) {
      engines[p] = 'missing';
      return { provider: p, ok: true, state: 'missing' as EngineState };
    },
  };
}
