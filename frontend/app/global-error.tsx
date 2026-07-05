'use client';

/**
 * global error boundary — root layout 自体の描画例外を受ける最終防波堤。
 * root layout ごと差し替わるため globals.css は当たらない (インラインスタイルで描く)。
 * 方針は app/error.tsx と同じ: チャンク読込系は 1 回だけ自動リロード、
 * それ以外は「再読み込み」ボタンで復帰手段を必ず残す。
 */

import { useEffect, useState } from 'react';

const RELOAD_GUARD_KEY = 'cf-chunk-error-reloaded-global';

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

function tryReloadOnce(): boolean {
  try {
    if (sessionStorage.getItem(RELOAD_GUARD_KEY)) return false;
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
    return true;
  } catch {
    return false;
  }
}

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  const [autoReloading, setAutoReloading] = useState(false);

  useEffect(() => {
    if (isChunkLoadError(error) && tryReloadOnce()) {
      setAutoReloading(true);
      window.location.reload();
    }
  }, [error]);

  return (
    <html lang="ja">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 16,
          padding: 24,
          textAlign: 'center',
          background: '#FAF7F2',
          color: '#1C1917',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        {autoReloading ? (
          <p style={{ fontSize: 14, color: '#57534E' }}>最新版に更新しています…</p>
        ) : (
          <>
            <div>
              <h2 style={{ margin: '0 0 6px', fontSize: 18 }}>画面の表示に失敗しました</h2>
              <p style={{ margin: 0, fontSize: 14, color: '#57534E' }}>
                アプリが更新された可能性があります。再読み込みをお試しください。
              </p>
            </div>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                border: 'none',
                borderRadius: 8,
                background: '#0D9488',
                color: '#fff',
                padding: '10px 20px',
                fontSize: 14,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              再読み込み
            </button>
            {error.digest && (
              <p style={{ margin: 0, fontSize: 11, color: '#A8A29E' }}>code: {error.digest}</p>
            )}
          </>
        )}
      </body>
    </html>
  );
}
