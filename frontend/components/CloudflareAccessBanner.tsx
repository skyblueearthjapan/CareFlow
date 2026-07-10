'use client';

/**
 * CloudflareAccessBanner — Cloudflare Access セッション切れの再ログイン導線
 * (2026-07-11 handoff §6-3)。
 *
 * lib/cfAccess.ts が API の 401 / ネットワークエラーから Access 切れを検知すると
 * CF_ACCESS_EXPIRED_EVENT を発火し、このバナーが全 UI (PC/現場ボード/モバイル) の
 * 最上部に出る。PWA はアドレスバーが無く自力で認証フローへ戻れないため、
 * 「再ログインする」= 窓ごとのトップレベル遷移 (location.reload) を提供する。
 * Access のログイン画面は frame-ancestors 'none' のため iframe 内では復旧不可能 —
 * 必ずトップレベル遷移にすること。
 */
import { useEffect, useState } from 'react';

import { CF_ACCESS_EXPIRED_EVENT } from '@/lib/cfAccess';

export function CloudflareAccessBanner() {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const onExpired = () => setShown(true);
    window.addEventListener(CF_ACCESS_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(CF_ACCESS_EXPIRED_EVENT, onExpired);
  }, []);

  if (!shown) return null;

  const relogin = () => {
    // トップレベル再読込 → Access が 302 でログイン画面へ → 認証後に元の URL へ戻る。
    // SW はナビゲーションを network-only で通すためキャッシュに阻まれない。
    window.location.reload();
  };

  return (
    <div
      role="alert"
      className="fixed inset-x-0 top-0 z-[100] flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-warning bg-warning-bg px-4 py-2 text-xs text-warning-strong shadow-md"
    >
      <span className="font-medium">
        ⚠ Cloudflare
        のログイン期限が切れている可能性があります。データが読み込めない場合は再ログインしてください。
      </span>
      <button type="button" onClick={relogin} className="font-bold underline">
        再ログインする
      </button>
      <button
        type="button"
        onClick={() => setShown(false)}
        className="font-medium underline opacity-70"
      >
        閉じる
      </button>
    </div>
  );
}
