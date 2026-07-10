'use client';

/**
 * ルート error boundary — 全ルートのクライアント例外を受ける。
 *
 * 主目的はデプロイ跨ぎの「旧ビルドのタブが新デプロイ後に旧チャンク 404 を踏む」
 * ChunkLoadError の救済。Next.js 既定の「Application error: a client-side
 * exception」は操作手段が無く画面が固まって見えるため、
 *   1. チャンク読込系エラーなら 1 回だけ自動リロード (新ビルドへ乗り換え)
 *   2. それ以外 / 自動リロード済みなら「再読み込み」ボタンを出す
 * とする。sessionStorage で自動リロードを 1 回に制限し、リロードループを防ぐ。
 */

import { useEffect, useState } from 'react';

import { Rakusuke } from '@/components/brand/Rakusuke';

const RELOAD_GUARD_KEY = 'cf-chunk-error-reloaded';

function isChunkLoadError(error: unknown): boolean {
  const name = error instanceof Error ? error.name : '';
  const msg = error instanceof Error ? error.message : String(error);
  return (
    name === 'ChunkLoadError' ||
    /Loading chunk [^ ]+ failed|ChunkLoadError|Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(
      msg,
    )
  );
}

/** 1 回だけ true (2 回目以降 false)。sessionStorage 不可 (private mode) は false。 */
function tryReloadOnce(): boolean {
  try {
    if (sessionStorage.getItem(RELOAD_GUARD_KEY)) return false;
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
    return true;
  } catch {
    return false;
  }
}

function clearReloadGuard() {
  try {
    sessionStorage.removeItem(RELOAD_GUARD_KEY);
  } catch {
    // noop
  }
}

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [autoReloading, setAutoReloading] = useState(false);

  useEffect(() => {
    // デプロイ跨ぎの旧チャンク 404 → リロードで新ビルドに載れば自己回復する。
    if (isChunkLoadError(error) && tryReloadOnce()) {
      setAutoReloading(true);
      window.location.reload();
    }
  }, [error]);

  if (autoReloading) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-text-secondary">最新版に更新しています…</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-6 text-center">
      <Rakusuke pose="puzzled" className="h-20" />
      <div className="space-y-1">
        <h2 className="font-serif text-lg font-bold text-text-primary">画面の表示に失敗しました</h2>
        <p className="text-sm text-text-secondary">
          ごめんなさい、アプリが更新された可能性があります。再読み込みをお試しください。
        </p>
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => {
            clearReloadGuard();
            window.location.reload();
          }}
          className="rounded-md bg-brand-primary px-4 py-2 text-sm font-semibold text-white"
        >
          再読み込み
        </button>
        <button
          type="button"
          onClick={() => reset()}
          className="rounded-md border border-border-default bg-bg-base px-4 py-2 text-sm text-text-secondary"
        >
          もう一度試す
        </button>
      </div>
      {error.digest && <p className="text-xs text-text-muted">code: {error.digest}</p>}
    </div>
  );
}
