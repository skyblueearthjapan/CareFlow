'use client';

/**
 * 認証付き音声プレーヤー（PC `/records` 詳細ダイアログ・モック ⑥）。
 *
 * `components/mobile/AuthedAudio.tsx` の写し。`GET /visit-recordings/{id}/audio`
 * は Bearer 必須なので素の `<audio src>` では 401 になる — Authorization 付き
 * fetch で blob を取り、objectURL を `<audio>` に渡す（写真の `AuthedPhoto` と
 * 同じ罠）。モバイルと分けているのは PC 側だけの要件があるため:
 *
 *   - モックどおりの自前コントロール（再生 / シーク / 経過 / 1.0×・1.5×）
 *   - **再生位置の保持**: 一覧 → 詳細を開き直しても続きから聞ける。要約を直しながら
 *     聞き返す使い方だと、再描画のたびに頭出しへ戻るのは致命的。
 *
 * 保持期間を過ぎた音声はサーバが 410 を返す（文字起こし・要約は残る）。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Pause, Play } from 'lucide-react';

import { recordingAudioUrl } from '@/lib/queries/visit-recordings';
import { formatDurationSec } from '@/components/records/recordFormat';

/** 再生速度の選択肢（現場は「速く聞き返す」需要が中心）。 */
const SPEEDS = [1.0, 1.5] as const;

/**
 * 記録 ID → 最後の再生位置（秒）。ダイアログを閉じても残す必要があるので
 * コンポーネント外のモジュールスコープに置く（同一タブ内のみ・永続化しない）。
 */
const lastPositionByRecording = new Map<string, number>();

interface AuthedAudioPlayerProps {
  /** `visit_recordings.id`。 */
  recordingId: string;
  accessToken: string | null;
  /** ヘッダ行に出す補足（例: `AAC · 8.2 MB`）。 */
  caption?: string | null;
  /**
   * 記録が持つ長さ（秒・`visit_recordings.duration_sec`）。
   *
   * WebM/Opus をストリーミングで書き出した音声は Duration ヘッダを持たないことが
   * あり、`el.duration` が `Infinity` / `NaN` になる（現場の MediaRecorder 録音が
   * まさにこれ）。そうなるとシークバーの上限も残り時間も出せないので、
   * **サーバが知っている長さ**を保険として使う（レビュー M-4）。
   */
  durationSec?: number | null;
  className?: string;
}

export function AuthedAudioPlayer({
  recordingId,
  accessToken,
  caption,
  durationSec,
  className,
}: AuthedAudioPlayerProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [speed, setSpeed] = useState<number>(1.0);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState<number>(0);
  const [duration, setDuration] = useState<number>(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  /** `el.duration` が読めないときに使う、サーバが知っている長さ。 */
  const fallbackDuration =
    typeof durationSec === 'number' && Number.isFinite(durationSec) && durationSec > 0
      ? durationSec
      : 0;

  // トークンは ref に置き、effect の依存には入れない（AuthedAudio のレビュー M-2）。
  // NextAuth は 55 分ごとにセッションを更新するので、依存に入れると**再生中に
  // 音声を取り直して頭出しに戻る**。
  const tokenRef = useRef(accessToken);
  tokenRef.current = accessToken;

  useEffect(() => {
    let created: string | null = null;
    let cancelled = false;
    setObjectUrl(null);
    setError(null);
    setPlaying(false);
    setCurrent(lastPositionByRecording.get(recordingId) ?? 0);
    setDuration(0);
    async function load() {
      try {
        const token = tokenRef.current;
        const res = await fetch(recordingAudioUrl(recordingId), {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          cache: 'no-store',
        });
        if (res.status === 410) {
          if (!cancelled) setError('音声は保持期間を過ぎて削除されました');
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      } catch {
        if (!cancelled) setError('音声を読み込めませんでした');
      }
    }
    void load();
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [recordingId]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = speed;
  }, [speed, objectUrl]);

  /** メタデータが読めた時点で、前回の位置へ戻す。 */
  const onLoadedMetadata = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    // ストリーミング録音は el.duration が Infinity/NaN になる → 記録側の長さに落とす。
    const known = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : fallbackDuration;
    setDuration(known);
    const saved = lastPositionByRecording.get(recordingId);
    if (saved && known > 0 && saved < known) {
      el.currentTime = saved;
      setCurrent(saved);
    }
  }, [recordingId, fallbackDuration]);

  const onTimeUpdate = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    setCurrent(el.currentTime);
    lastPositionByRecording.set(recordingId, el.currentTime);
  }, [recordingId]);

  const toggle = useCallback(() => {
    const el = audioRef.current;
    if (!el) return;
    if (el.paused) {
      void el.play();
    } else {
      el.pause();
    }
  }, []);

  const seek = useCallback(
    (value: number) => {
      const el = audioRef.current;
      if (!el) return;
      el.currentTime = value;
      setCurrent(value);
      lastPositionByRecording.set(recordingId, value);
    },
    [recordingId],
  );

  if (error) {
    return (
      <p className={`text-sm text-text-muted ${className ?? ''}`} data-testid="records-audio-error">
        {error}
      </p>
    );
  }
  if (!objectUrl) {
    return (
      <p className={`flex items-center gap-2 text-sm text-text-muted ${className ?? ''}`}>
        <Loader2 className="h-4 w-4 animate-spin" />
        音声を読み込んでいます…
      </p>
    );
  }

  const known = duration > 0 ? duration : fallbackDuration;
  const max = known > 0 ? known : Math.max(current, 1);

  return (
    <div
      className={`rounded-lg border border-border-default bg-bg-muted p-3 ${className ?? ''}`}
      data-testid="records-audio-player"
    >
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 音声記録（字幕は文字起こしが担う） */}
      <audio
        ref={audioRef}
        src={objectUrl}
        className="hidden"
        data-testid="records-audio-element"
        onLoadedMetadata={onLoadedMetadata}
        onTimeUpdate={onTimeUpdate}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
      />
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={toggle}
          aria-label={playing ? '一時停止' : '再生'}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-primary text-white hover:bg-brand-primary-hover"
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </button>
        <input
          type="range"
          aria-label="再生位置"
          className="min-w-0 flex-1 accent-brand-primary"
          min={0}
          max={max}
          step={1}
          value={Math.min(current, max)}
          onChange={(e) => seek(Number(e.target.value))}
        />
        <span className="shrink-0 tnum text-[13px] text-text-secondary">
          {formatDurationSec(current)} / {known > 0 ? formatDurationSec(known) : '--:--'}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSpeed(s)}
              aria-pressed={speed === s}
              className={
                speed === s
                  ? 'rounded-full border border-brand-primary bg-brand-primary-light px-2.5 py-0.5 text-xs font-semibold text-brand-primary-hover'
                  : 'rounded-full border border-border-default bg-bg-base px-2.5 py-0.5 text-xs text-text-secondary'
              }
            >
              {s.toFixed(1)}×
            </button>
          ))}
        </div>
      </div>
      {caption && <p className="mt-2 text-xs text-text-muted">{caption}</p>}
    </div>
  );
}
