'use client';

/**
 * ⚙ 設定 —「AI のつなぎ先」(ChatGPT / Claude のサインイン・サインアウト)と「新しい相談で使う AI」。
 * 管理者だけ(小窓・相談タブ自体が管理者だけ。中継も確かめる)。SSH なしで、画面からサインインできる。
 * サインインは同時に 1 つだけ・15 分で打ち切り(中継と箱の runner が守る)。
 * 人ごとの席(PO 2026-10-13: ChatGPT Business/Team は 1 席 1 人): ここに出るのは「あなたの」アカウント。
 * 相談はその人のアカウントで動く。サインイン・サインアウト・既定の AI も、その人の分だけが変わる。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useAgentMaybe } from './AgentProvider';
import {
  ACTIVE_LOGIN,
  ENGINE_NAME,
  type EngineAdmin,
  type EngineProvider,
  type EngineState,
  type EnginesView,
  type KarteMode,
  type KarteSettings,
  type LoginFlow,
  type SeatsView,
} from './engines';

import './engineSettings.css';

const POLL_MS = 2000;

const STATE_LABEL: Record<EngineState, string> = {
  ok: 'サインインしています',
  missing: 'サインインしていません',
  api_key: '使えないつなぎ方になっています',
  unknown: 'いまの様子が分かりません',
};
const STATE_TONE: Record<EngineState, 'ok' | 'warn' | 'bad' | 'muted'> = {
  ok: 'ok',
  missing: 'warn',
  api_key: 'bad',
  unknown: 'muted',
};
const ACCOUNT_OF: Record<EngineProvider, string> = {
  codex: 'ChatGPT のアカウント（Plus・Pro・Business など）',
  claude: 'Claude のアカウント（Pro・Max など）',
};

function mmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** 見出しに置く ⚙ ボタン。中継がつなぎ先の設定を持たない時は出さない。 */
export function EngineSettingsButton({ label = '設定' }: { label?: string }) {
  const a = useAgentMaybe();
  const [open, setOpen] = useState(false);
  const engines = a?.engines ?? null;
  if (!engines) return null;
  return (
    <>
      <button
        type="button"
        className="rk-hbtn"
        aria-label="AI のつなぎ先の設定"
        title="AI のつなぎ先の設定"
        onClick={() => setOpen(true)}
      >
        ⚙{label ? ` ${label}` : ''}
      </button>
      {open &&
        typeof document !== 'undefined' &&
        createPortal(
          <EngineSettingsSheet engines={engines} onClose={() => setOpen(false)} />,
          document.body,
        )}
    </>
  );
}

export function EngineSettingsSheet({
  engines,
  onClose,
}: {
  engines: EngineAdmin;
  onClose: () => void;
}) {
  const [view, setView] = useState<EnginesView | null>(null);
  const [flow, setFlow] = useState<LoginFlow | null>(null);
  const [left, setLeft] = useState(0);
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<EngineProvider | null>(null);
  const [code, setCode] = useState('');
  const [copied, setCopied] = useState(false);
  const [seats, setSeats] = useState<SeatsView | null>(null);
  const [seatsOpen, setSeatsOpen] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const v = await engines.view();
      if (!alive.current) return;
      setView(v);
      if (v.login) setFlow(v.login);
    } catch (e) {
      if (alive.current)
        setMsg({ tone: 'bad', text: (e as Error).message || '設定を読めませんでした' });
    }
  }, [engines]);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  const active = !!flow && ACTIVE_LOGIN.includes(flow.state);

  // サインインの途中は 2 秒ごとに様子を見る。終わったら一覧を読み直す
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      engines
        .pollLogin()
        .then((f) => {
          if (!alive.current) return;
          setFlow(f);
          if (!ACTIVE_LOGIN.includes(f.state)) void load();
        })
        .catch(() => {
          /* 一度読めなくても次でまた見る */
        });
    }, POLL_MS);
    return () => clearInterval(t);
  }, [active, engines, load]);

  // 残り時間(画面の中で 1 秒ずつ減らす。中継の返事で合わせ直す)
  useEffect(() => {
    setLeft(flow && ACTIVE_LOGIN.includes(flow.state) ? flow.expires_in : 0);
  }, [flow]);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [active]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg({ tone: 'bad', text: (e as Error).message || 'うまくいきませんでした' });
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const signIn = (p: EngineProvider) =>
    run(async () => {
      setConfirm(null);
      setCode('');
      setCopied(false);
      const f = await engines.startLogin(p);
      setFlow(f);
    });

  const signOut = (p: EngineProvider, thenSignIn: boolean) =>
    run(async () => {
      setConfirm(null);
      const r = await engines.logout(p);
      await load();
      setMsg(
        r.ok
          ? { tone: 'ok', text: `${ENGINE_NAME[p]} からサインアウトしました。` }
          : { tone: 'bad', text: 'サインアウトできませんでした。少し時間をおいて、もう一度どうぞ' },
      );
      if (thenSignIn && r.ok) {
        const f = await engines.startLogin(p);
        setFlow(f);
      }
    });

  const sendCode = () =>
    run(async () => {
      const f = await engines.sendCode(code);
      setFlow(f);
      setCode('');
    });

  const cancel = () =>
    run(async () => {
      setFlow(await engines.cancelLogin());
    });

  const choose = (p: EngineProvider) =>
    run(async () => {
      await engines.setDefault(p);
      await load();
      setMsg({ tone: 'ok', text: `新しい相談は ${ENGINE_NAME[p]} で始めます。` });
    });

  const copyCode = async (c: string) => {
    try {
      await navigator.clipboard.writeText(c);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const toggleSeats = async () => {
    const next = !seatsOpen;
    setSeatsOpen(next);
    if (!next || !engines.seats) return;
    try {
      const v = await engines.seats();
      if (alive.current) setSeats(v);
    } catch (e) {
      if (alive.current)
        setMsg({ tone: 'bad', text: (e as Error).message || '一覧を読めませんでした' });
    }
  };

  const providers = view?.providers ?? [];

  return (
    <>
      <div className="rk-es-scrim" data-testid="rk-es-scrim" onClick={onClose} />
      <section className="rk-es" role="dialog" aria-modal="true" aria-labelledby="rk-es-title">
        <header className="rk-es-head">
          <h2 id="rk-es-title">AI のつなぎ先</h2>
          <button type="button" className="rk-es-x" aria-label="閉じる" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="rk-es-body">
          <p className="rk-es-lead">
            ぼくが相談に使う AI です。相談は、あなた自身のアカウントで動きます（1 人に 1 つ）。
            サインインやサインアウトは、ここからできます。
          </p>
          {view && !view.can_sign_in && (
            <p className="rk-es-note">この環境では、画面からのサインインはできません。</p>
          )}
          {msg && (
            <p className="rk-es-msg" data-tone={msg.tone} role="status">
              {msg.text}
            </p>
          )}
          {!view && !msg && <p className="rk-es-note">読み込んでいます…</p>}

          {providers.map((p) => {
            const st: EngineState = view?.engines[p]?.state ?? 'unknown';
            const mine = flow && flow.provider === p ? flow : null;
            const showFlow = !!mine && mine.state !== 'idle';
            return (
              <article className="rk-es-card" key={p} data-testid={`rk-es-card-${p}`}>
                <div className="rk-es-card-top">
                  <div>
                    <h3>あなたの {ENGINE_NAME[p]}</h3>
                    <p className="rk-es-sub">{ACCOUNT_OF[p]}</p>
                  </div>
                  <span className="rk-es-state" data-tone={STATE_TONE[st]}>
                    {STATE_LABEL[st]}
                  </span>
                </div>
                {st === 'api_key' && (
                  <p className="rk-es-note">
                    契約しているアカウントで、サインインし直してください。
                  </p>
                )}

                {showFlow && mine ? (
                  <LoginPanel
                    flow={mine}
                    left={left}
                    code={code}
                    copied={copied}
                    busy={busy}
                    onCode={setCode}
                    onSend={sendCode}
                    onCopy={copyCode}
                    onCancel={cancel}
                    onClear={() => setFlow(null)}
                  />
                ) : confirm === p ? (
                  <div
                    className="rk-es-confirm"
                    role="group"
                    aria-label={`${ENGINE_NAME[p]} からサインアウトしますか`}
                  >
                    <p>
                      サインアウトすると、あなたの {ENGINE_NAME[p]}
                      での相談は、もう一度サインインするまで止まります（ほかの方の分は変わりません）。どうしますか？
                    </p>
                    <div className="rk-es-choices">
                      <button
                        type="button"
                        className="rk-es-btn"
                        data-kind="danger"
                        disabled={busy}
                        onClick={() => void signOut(p, false)}
                      >
                        サインアウトする
                      </button>
                      <button
                        type="button"
                        className="rk-es-btn"
                        disabled={busy}
                        onClick={() => void signOut(p, true)}
                      >
                        サインアウトして、別のアカウントでサインインし直す
                      </button>
                      <button
                        type="button"
                        className="rk-es-btn"
                        data-kind="quiet"
                        onClick={() => setConfirm(null)}
                      >
                        やめる
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="rk-es-actions">
                    {st === 'ok' ? (
                      <button
                        type="button"
                        className="rk-es-btn"
                        disabled={busy || active || !view?.can_sign_in}
                        onClick={() => setConfirm(p)}
                      >
                        サインアウト
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="rk-es-btn"
                        data-kind="primary"
                        disabled={busy || active || !view?.can_sign_in}
                        onClick={() => void signIn(p)}
                      >
                        サインイン
                      </button>
                    )}
                  </div>
                )}
              </article>
            );
          })}

          {providers.length > 1 && view && (
            <fieldset className="rk-es-default">
              <legend>あなたの新しい相談で使う AI</legend>
              {providers.map((p) => (
                <label key={p} className="rk-es-radio">
                  <input
                    type="radio"
                    name="rk-es-default"
                    value={p}
                    checked={view.default_provider === p}
                    disabled={busy}
                    onChange={() => void choose(p)}
                  />
                  <span>{ENGINE_NAME[p]}</span>
                  {view.engines[p]?.state !== 'ok' && <small>（サインインしていません）</small>}
                </label>
              ))}
              <p className="rk-es-note">いま開いている相談は、そのままの AI で続きます。</p>
            </fieldset>
          )}

          {view?.can_view_seats && engines.seats && (
            <section className="rk-es-seats" aria-label="管理者のサインインの様子">
              <button
                type="button"
                className="rk-es-btn"
                data-kind="quiet"
                aria-expanded={seatsOpen}
                onClick={() => void toggleSeats()}
              >
                {seatsOpen ? '▾' : '▸'} 管理者のサインインの様子
              </button>
              {seatsOpen && !seats && <p className="rk-es-note">読み込んでいます…</p>}
              {seatsOpen && seats && (
                <table className="rk-es-table">
                  <thead>
                    <tr>
                      <th scope="col">名前</th>
                      {seats.providers.map((p) => (
                        <th scope="col" key={p}>
                          {ENGINE_NAME[p]}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {seats.seats.map((s, i) => (
                      <tr key={`${s.name}-${i}`}>
                        <td>
                          {s.name}
                          {s.me && <small>（あなた）</small>}
                        </td>
                        {seats.providers.map((p) => {
                          const st: EngineState = s.engines[p]?.state ?? 'unknown';
                          return (
                            <td key={p}>
                              <span className="rk-es-state" data-tone={STATE_TONE[st]}>
                                {STATE_LABEL[st]}
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          )}

          {view?.can_view_seats && engines.karte && engines.setKarte && (
            <KarteSeatSection engines={engines} />
          )}
        </div>
        <footer className="rk-es-foot">
          <button type="button" className="rk-es-btn" onClick={onClose}>
            閉じる
          </button>
        </footer>
      </section>
    </>
  );
}

const KARTE_MODE_LABEL: Record<KarteMode, string> = {
  designated: 'ここで選んだ管理者の ChatGPT で作る',
  presser: '録音した本人の ChatGPT で作る（全員が ChatGPT の席を持ってから）',
};

/**
 * カルテの下書きに使う席(管理者だけ・PO 2026-10-13)。訪問の録音から作るカルテの下書きを、
 * 選んだ管理者の ChatGPT で作る。選べるのは ChatGPT にサインインしている管理者だけ。
 * ChatGPT が使えないときは Gemini で作る(カルテは止まらない)。拠点ごとの ON/OFF は「機能の切り替え」。
 */
export function KarteSeatSection({ engines }: { engines: EngineAdmin }) {
  const [k, setK] = useState<KarteSettings | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    engines
      .karte?.()
      .then((v) => alive.current && setK(v))
      .catch((e: Error) => alive.current && setErr(e.message || '読めませんでした'));
    return () => {
      alive.current = false;
    };
  }, [engines]);

  const save = async (patch: { mode?: KarteMode; seat_user_id?: string | null }) => {
    if (!engines.setKarte) return;
    setSaving(true);
    setErr(null);
    try {
      const v = await engines.setKarte(patch);
      if (alive.current) setK(v);
    } catch (e) {
      if (alive.current) setErr((e as Error).message || '変えられませんでした');
    } finally {
      if (alive.current) setSaving(false);
    }
  };

  const choices = k ? k.seats.filter((s) => s.state === 'ok' || s.user_id === k.seat_user_id) : [];

  return (
    <section className="rk-es-seats" aria-label="カルテの下書きに使う席">
      <h3 className="rk-es-subhead">カルテの下書きに使う席</h3>
      <p className="rk-es-note">
        訪問の録音から作るカルテの下書きを、ChatGPT で作ります（「機能の切り替え」で ON
        にした拠点だけ）。ChatGPT が使えないときは、いままでどおり Gemini で作ります。
      </p>
      {err && (
        <p className="rk-es-msg" data-tone="bad" role="alert">
          {err}
        </p>
      )}
      {!k && !err && <p className="rk-es-note">読み込んでいます…</p>}
      {k && (
        <>
          <fieldset className="rk-es-default" disabled={saving}>
            <legend>だれの ChatGPT で作るか</legend>
            {k.modes.map((m) => (
              <label key={m} className="rk-es-radio">
                <input
                  type="radio"
                  name="rk-karte-mode"
                  value={m}
                  checked={k.mode === m}
                  onChange={() => void save({ mode: m })}
                />
                <span>{KARTE_MODE_LABEL[m]}</span>
              </label>
            ))}
          </fieldset>
          {k.mode === 'designated' && (
            <fieldset className="rk-es-default" disabled={saving}>
              <legend>使う席（ChatGPT にサインインしている管理者）</legend>
              {choices.length === 0 && (
                <p className="rk-es-note">
                  ChatGPT にサインインしている管理者がいません。上の「あなたの
                  ChatGPT」からサインインしてください。
                </p>
              )}
              {choices.map((s) => (
                <label key={s.user_id} className="rk-es-radio">
                  <input
                    type="radio"
                    name="rk-karte-seat"
                    value={s.user_id}
                    checked={k.seat_user_id === s.user_id}
                    onChange={() => void save({ seat_user_id: s.user_id })}
                  />
                  <span>
                    {s.name}
                    {s.me && <small>（あなた）</small>}
                  </span>
                  {s.state !== 'ok' && (
                    <small>（サインインしていません。いまは Gemini で作ります）</small>
                  )}
                </label>
              ))}
              {k.seat_user_id && !k.seat_known && (
                <p className="rk-es-note">
                  前に選んだ方は、いまの管理者の一覧にいません。選び直してください。
                </p>
              )}
              {!k.seat_user_id && choices.length > 0 && (
                <p className="rk-es-note">まだ選んでいません（選ぶまでは Gemini で作ります）。</p>
              )}
              <p className="rk-es-note">
                いまは 1 つの席で全員の分を作ります（ChatGPT の「1 席 1
                人」の約束から外れる、当面の決め方です）。
              </p>
            </fieldset>
          )}
        </>
      )}
    </section>
  );
}

function LoginPanel({
  flow,
  left,
  code,
  copied,
  busy,
  onCode,
  onSend,
  onCopy,
  onCancel,
  onClear,
}: {
  flow: LoginFlow;
  left: number;
  code: string;
  copied: boolean;
  busy: boolean;
  onCode: (v: string) => void;
  onSend: () => void;
  onCopy: (c: string) => void;
  onCancel: () => void;
  onClear: () => void;
}) {
  const name = flow.provider ? ENGINE_NAME[flow.provider] : 'AI';
  if (flow.state === 'starting') return <p className="rk-es-note">準備しています…</p>;
  if (flow.state === 'done')
    return (
      <p className="rk-es-msg" data-tone="ok" role="status">
        サインインできました。ありがとうございます。
      </p>
    );
  if (flow.state === 'failed' || flow.state === 'timeout' || flow.state === 'cancelled') {
    const text =
      flow.state === 'timeout'
        ? '時間切れになりました（15 分）。お手数ですが、もう一度サインインからお願いします。'
        : flow.state === 'cancelled'
          ? 'サインインをやめました。'
          : flow.error === 'no_url'
            ? 'サインインの準備ができませんでした。少し時間をおいて、もう一度どうぞ。'
            : 'サインインできませんでした。もう一度お試しください。';
    return (
      <div className="rk-es-flow">
        <p
          className="rk-es-msg"
          data-tone={flow.state === 'cancelled' ? 'ok' : 'bad'}
          role="status"
        >
          {text}
        </p>
        <button type="button" className="rk-es-btn" data-kind="quiet" onClick={onClear}>
          わかりました
        </button>
      </div>
    );
  }
  return (
    <div className="rk-es-flow" data-testid="rk-es-flow">
      <ol>
        <li>
          下のリンクをスマホか PC のブラウザーで開いて、{name} にサインインしてください。
          {flow.url && (
            <a className="rk-es-link" href={flow.url} target="_blank" rel="noopener noreferrer">
              {flow.provider === 'codex' ? flow.url : `${name} のサインインのページを開く`}
            </a>
          )}
        </li>
        {flow.code && (
          <li>
            この 1 回限りのコードを入れてください。
            <span className="rk-es-coderow">
              <code className="rk-es-code" data-testid="rk-es-code">
                {flow.code}
              </code>
              <button type="button" className="rk-es-btn" onClick={() => onCopy(flow.code ?? '')}>
                {copied ? 'コピーしました' : 'コピー'}
              </button>
            </span>
          </li>
        )}
        {flow.provider === 'claude' && (
          <li>
            ブラウザーに出たコードを、ここに貼り付けてください。
            <span className="rk-es-coderow">
              <input
                className="rk-es-input"
                aria-label="ブラウザーに出たコード"
                value={code}
                autoComplete="off"
                spellCheck={false}
                disabled={!flow.needs_input || busy}
                onChange={(e) => onCode(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && code.trim()) onSend();
                }}
              />
              <button
                type="button"
                className="rk-es-btn"
                data-kind="primary"
                disabled={!flow.needs_input || busy || !code.trim()}
                onClick={onSend}
              >
                送る
              </button>
            </span>
            {flow.error === 'invalid_code' && (
              <span className="rk-es-err" role="alert">
                コードの形が違うようです。ブラウザーに出たコードを、そのまま貼り付けてください。
              </span>
            )}
          </li>
        )}
      </ol>
      <p className="rk-es-wait" role="status">
        {flow.state === 'verifying'
          ? '確かめています…'
          : `サインインが終わるのを待っています（あと ${mmss(left)}）`}
      </p>
      <button type="button" className="rk-es-btn" data-kind="quiet" onClick={onCancel}>
        やめる
      </button>
    </div>
  );
}
