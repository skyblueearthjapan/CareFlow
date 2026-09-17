'use client';

/**
 * 認証付き音声プレーヤー（訪問の音声記録）。
 *
 * `GET /api/v1/visit-recordings/{id}/audio` は Bearer 必須なので、素の
 * `<audio src>` では `{"detail":"Authentication required"}` になる
 * （写真の `AuthedPhoto` と同じ罠）。Authorization ヘッダ付き fetch で blob を
 * 取り、objectURL を `<audio controls>` に渡す。unmount で revoke する。
 *
 * 保持期間を過ぎた音声はサーバが 410 を返す（文字起こし・要約は残る）。
 */

import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { recordingAudioUrl } from '@/lib/queries/visit-recordings';

/** 再生速度の選択肢（現場は「速く聞き返す」需要が中心）。 */
const SPEEDS = [1.0, 1.5] as const;

interface AuthedAudioProps {
  /** `visit_recordings.id`。 */
  recordingId: string;
  accessToken: string | null;
  className?: string;
}

export function AuthedAudio({ recordingId, accessToken, className }: AuthedAudioProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [speed, setSpeed] = useState<number>(1.0);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // トークンは ref に置き、effect の依存には入れない（レビュー M-2）。
  // NextAuth は 55 分ごとにセッションを更新するので、依存に入れると**再生中に
  // 音声を取り直して頭出しに戻る**。取得はブラウザが `<audio>` に渡す前に
  // 済んでおり、後から変わったトークンで引き直す理由は無い。
  const tokenRef = useRef(accessToken);
  tokenRef.current = accessToken;

  useEffect(() => {
    let created: string | null = null;
    let cancelled = false;
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

  if (error) {
    return <p className={`text-sm text-text-muted ${className ?? ''}`}>{error}</p>;
  }
  if (!objectUrl) {
    return (
      <p className={`flex items-center gap-2 text-sm text-text-muted ${className ?? ''}`}>
        <Loader2 className="h-4 w-4 animate-spin" />
        音声を読み込んでいます…
      </p>
    );
  }
  return (
    <div className={`space-y-2 ${className ?? ''}`}>
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- 音声記録（字幕は文字起こしが担う） */}
      <audio ref={audioRef} src={objectUrl} controls className="w-full" data-testid="authed-audio">
        お使いの端末では音声を再生できません。
      </audio>
      <div className="flex items-center gap-2">
        <span className="text-sm text-text-muted">再生速度</span>
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSpeed(s)}
            aria-pressed={speed === s}
            className={
              speed === s
                ? 'rounded-full border border-brand-primary bg-brand-primary-50 px-3 py-1 text-sm font-semibold text-brand-primary'
                : 'rounded-full border border-border-default px-3 py-1 text-sm text-text-secondary'
            }
          >
            {s.toFixed(1)}×
          </button>
        ))}
      </div>
    </div>
  );
}
