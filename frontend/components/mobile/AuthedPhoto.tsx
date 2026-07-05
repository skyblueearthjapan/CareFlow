'use client';

/**
 * 認証付き写真表示 (visit photos)。
 *
 * `GET /visits/{id}/photos/{pid}/download` は Bearer 必須のため、素の
 * `<img src>` や `<a href>` では `{"detail":"Authentication required"}` になる
 * (2026-07-05 本番障害)。Authorization ヘッダ付き fetch で blob を取り、
 * objectURL を <img> に渡す。unmount で revoke する。
 */

import { useEffect, useState } from 'react';

interface AuthedPhotoProps {
  /** 相対 URL (`/api/v1/visits/.../download`)。 */
  url: string;
  accessToken: string | null;
  alt: string;
  className?: string;
  /** 取得済み objectURL を親に渡すタップハンドラ (拡大表示用)。 */
  onOpen?: (objectUrl: string) => void;
}

export function AuthedPhoto({ url, accessToken, alt, className, onOpen }: AuthedPhotoProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(url, {
          headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
          cache: 'no-store',
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        const obj = URL.createObjectURL(blob);
        revoked = obj;
        setObjectUrl(obj);
      } catch {
        if (!cancelled) setFailed(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [url, accessToken]);

  if (failed) {
    return (
      <div
        className={`flex items-center justify-center text-[10px] text-text-muted ${className ?? ''}`}
      >
        読込失敗
      </div>
    );
  }
  if (!objectUrl) {
    return <div className={`animate-pulse bg-bg-muted ${className ?? ''}`} />;
  }
  return (
    <button
      type="button"
      className={className}
      onClick={onOpen ? () => onOpen(objectUrl) : undefined}
      aria-label={`${alt}を拡大表示`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={objectUrl} alt={alt} className="h-full w-full object-cover" loading="lazy" />
    </button>
  );
}
