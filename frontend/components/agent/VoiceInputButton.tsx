'use client';

/**
 * 🎙 声で入力(らく助エージェントの入力欄の横)。
 * docs/plans/voice-input-and-karte-engine-research-2026-10-13.md §4-A (A-1)。
 *
 * - 押すと録音を始め、もう一度押すと止めて送る(押している間だけ、にはしない:
 *   iPhone は長押しで文字の選択やメニューが出るうえ、初回はマイクの許可の確認で
 *   指が離れてしまうため)。✕ でやめる。60 秒で自動で止めて送る。
 * - 録音はメモリの上だけ(端末にも保存しない)。サーバーが東京で文字にして返し、
 *   入力欄の文に足す。**自動で送らない**(人が確かめて ➤ を押す)。
 * - iPhone のホーム画面アプリは audio/mp4、ほかは webm/opus(`pickMimeType`)。
 *   画面が隠れると iPhone は録音が止まるので、隠れた時点で止めて送る。
 * - サーバーが「使えない」と言う間(Vertex の設定なし)は何も描かない。
 */
import { useSession } from 'next-auth/react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError } from '@/lib/api-client';
import { fetcher } from '@/lib/api/fetcher';
import { formatElapsed, pickMimeType, recordingUnsupportedReason } from '@/lib/voice/recorder';

export const VOICE_MAX_SEC = 60;
/** これより短い録音は送らない(押し間違い)。 */
export const VOICE_MIN_MS = 600;
export const TRANSCRIBE_PATH = '/api/v1/agent/transcribe';

type Phase = 'idle' | 'starting' | 'recording' | 'sending';

/** 使えるかの問い合わせは 1 ページで 1 回(失敗したら次に描く時に聞き直す)。 */
let availability: Promise<boolean> | null = null;

/** テスト用: 問い合わせの控えを捨てる。 */
export function resetVoiceAvailability(): void {
  availability = null;
}

interface Tokens {
  accessToken: string | null;
  refreshToken: string | null;
}

function useTokens() {
  const { data: session, status } = useSession();
  const s = session as { accessToken?: string; refreshToken?: string } | null | undefined;
  const tokens = useRef<Tokens>({ accessToken: null, refreshToken: null });
  tokens.current = { accessToken: s?.accessToken ?? null, refreshToken: s?.refreshToken ?? null };
  return { tokens, authenticated: status === 'authenticated' };
}

export function useTranscribeAvailable(): boolean {
  const { tokens, authenticated } = useTokens();
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!authenticated) return;
    let alive = true;
    if (!availability) {
      availability = fetcher<{ available?: boolean }>(TRANSCRIBE_PATH, { ...tokens.current })
        .then((b) => b?.available === true)
        .catch(() => {
          availability = null;
          return false;
        });
    }
    void availability.then((v) => {
      if (alive) setOk(v);
    });
    return () => {
      alive = false;
    };
  }, [authenticated, tokens]);
  return ok;
}

function micErrorMessage(err: unknown): string {
  const name = (err as { name?: string } | null)?.name ?? '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'マイクが許可されていません。ブラウザーか端末の設定で、このサイトのマイクを許可してください。';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'マイクが見つかりませんでした。';
  }
  if (name === 'NotReadableError') {
    return 'マイクをほかのアプリが使っているようです。そちらを閉じてからお試しください。';
  }
  return 'マイクを使えませんでした。文字で入力してください。';
}

function serverMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string' && detail) return detail;
    if (err.status === 429) return '続けて使われたので、少し休ませてください。';
  }
  return '文字にできませんでした。通信の具合を確かめて、もう一度お試しください。';
}

function fileName(mime: string): string {
  if (mime.includes('mp4') || mime.includes('aac')) return 'voice.m4a';
  if (mime.includes('ogg')) return 'voice.ogg';
  return 'voice.webm';
}

export function VoiceInputButton({ onText }: { onText: (text: string) => void }) {
  const available = useTranscribeAvailable();
  const { tokens } = useTokens();
  const [phase, setPhase] = useState<Phase>('idle');
  const [elapsed, setElapsed] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);

  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAt = useRef(0);
  const cancelled = useRef(false);
  const alive = useRef(true);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const release = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
  }, []);

  const stop = useCallback(() => {
    const r = rec.current;
    if (r && r.state !== 'inactive') {
      try {
        r.stop();
      } catch {
        /* もう止まっている */
      }
    }
  }, []);

  const cancel = useCallback(() => {
    cancelled.current = true;
    stop();
    release();
    chunks.current = [];
    rec.current = null;
    setPhase('idle');
  }, [release, stop]);

  const send = useCallback(
    async (blob: Blob, ms: number) => {
      setPhase('sending');
      const form = new FormData();
      form.append('audio', blob, fileName(blob.type));
      form.append('duration_sec', (ms / 1000).toFixed(1));
      try {
        const res = await fetcher<{ text?: string }>(TRANSCRIBE_PATH, {
          method: 'POST',
          body: form,
          ...tokens.current,
        });
        const text = (res?.text ?? '').trim();
        if (!alive.current) return;
        if (text) {
          onTextRef.current(text);
          setMsg(null);
        } else {
          setMsg('声が聞き取れませんでした。もう少し近くで、もう一度お話しください。');
        }
      } catch (err) {
        if (alive.current) setMsg(serverMessage(err));
      } finally {
        if (alive.current) setPhase('idle');
      }
    },
    [tokens],
  );

  const start = useCallback(async () => {
    setMsg(null);
    const reason = recordingUnsupportedReason();
    if (reason) {
      setMsg(`${reason}。キーボードのマイクで入力することもできます。`);
      return;
    }
    setPhase('starting');
    cancelled.current = false;
    let s: MediaStream;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      if (alive.current) {
        setMsg(micErrorMessage(err));
        setPhase('idle');
      }
      return;
    }
    if (!alive.current || cancelled.current) {
      s.getTracks().forEach((t) => t.stop());
      return;
    }
    stream.current = s;
    const mime = pickMimeType();
    let r: MediaRecorder;
    try {
      r = new MediaRecorder(
        s,
        mime ? { mimeType: mime, audioBitsPerSecond: 32_000 } : { audioBitsPerSecond: 32_000 },
      );
    } catch {
      release();
      setMsg(
        'この端末のブラウザーでは録音できません。キーボードのマイクで入力することもできます。',
      );
      setPhase('idle');
      return;
    }
    rec.current = r;
    chunks.current = [];
    r.ondataavailable = (e: BlobEvent) => {
      if (e.data && e.data.size > 0) chunks.current.push(e.data);
    };
    r.onstop = () => {
      const ms = Date.now() - startedAt.current;
      const type = r.mimeType || mime || 'audio/webm';
      const blob = new Blob(chunks.current, { type });
      chunks.current = [];
      rec.current = null;
      release();
      if (cancelled.current || !alive.current) return;
      if (ms < VOICE_MIN_MS || blob.size === 0) {
        setMsg('短すぎました。🎙 を押してから話し、話し終えたらもう一度押してください。');
        setPhase('idle');
        return;
      }
      void send(blob, ms);
    };
    r.start(1000);
    startedAt.current = Date.now();
    setElapsed(0);
    setPhase('recording');
    timer.current = setInterval(() => {
      const sec = (Date.now() - startedAt.current) / 1000;
      setElapsed(Math.min(VOICE_MAX_SEC, Math.floor(sec)));
      if (sec >= VOICE_MAX_SEC) stop();
    }, 250);
  }, [release, send, stop]);

  // iPhone は画面が隠れると録音が止まる。尻切れにしないよう、ここで止めて送る。
  useEffect(() => {
    if (phase !== 'recording') return;
    const onHide = () => {
      if (document.hidden) stop();
    };
    document.addEventListener('visibilitychange', onHide);
    return () => document.removeEventListener('visibilitychange', onHide);
  }, [phase, stop]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      cancelled.current = true;
      stop();
      release();
    };
  }, [release, stop]);

  if (!available) return null;

  const recording = phase === 'recording';
  return (
    <div className="rk-voice">
      {(recording || phase === 'sending' || msg) && (
        <div
          className="rk-voice-pop"
          data-tone={msg && !recording && phase !== 'sending' ? 'warn' : undefined}
        >
          {recording ? (
            <span role="status" className="rk-voice-rec">
              <span className="rk-rec-dot" aria-hidden="true" />
              聞いています {formatElapsed(elapsed)} / {formatElapsed(VOICE_MAX_SEC)}
            </span>
          ) : phase === 'sending' ? (
            <span role="status">文字にしています…</span>
          ) : (
            <>
              <span role="alert">{msg}</span>
              <button
                type="button"
                className="rk-voice-x"
                aria-label="お知らせを閉じる"
                onClick={() => setMsg(null)}
              >
                ✕
              </button>
            </>
          )}
          {recording && (
            <button type="button" className="rk-voice-x" aria-label="録音をやめる" onClick={cancel}>
              ✕
            </button>
          )}
        </div>
      )}
      <button
        type="button"
        className="rk-mic"
        data-rec={recording || undefined}
        aria-label={recording ? '話し終えたら押してください（文字にします）' : '声で入力'}
        aria-pressed={recording}
        title={
          recording
            ? '押すと止めて文字にします'
            : '声で入力（押して話し、もう一度押すと文字になります・60 秒まで）'
        }
        disabled={phase === 'starting' || phase === 'sending'}
        onClick={() => (recording ? stop() : void start())}
      >
        {recording ? '■' : '🎙'}
      </button>
    </div>
  );
}
