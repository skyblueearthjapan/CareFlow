'use client';

import { useEffect } from 'react';

import { Sidebar } from '@/components/Sidebar';
import { Header } from '@/components/Header';
import { useUIStore } from '@/lib/stores/ui';

interface AppShellProps {
  children: React.ReactNode;
}

/**
 * これ未満の画面幅では初回表示時にサイドバーを自動で畳む (px)。
 * MacBook の論理幅は Air M1=1440 / Air M2=1470 / Pro 14=1512 なので、それらを全て
 * 含む 1536 (Tailwind の 2xl と同じ) を境にする (レビュー HIGH-1・2026-08-29)。
 * マウント時のみ判定 (resize では畳まない: 作業中に勝手に畳まれると困るため)。
 */
const SIDEBAR_AUTO_COLLAPSE_MAX_WIDTH = 1536;

export function AppShell({ children }: AppShellProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const applySidebarAutoCollapse = useUIStore((s) => s.applySidebarAutoCollapse);

  // 狭い画面 (MacBook 13 の 1280〜1440px や 13 インチ Windows ノート) では初回だけ
  // サイドバーを畳んで盤面に +160px を回す (mac-ui-crossplatform-design.md §2-B2)。
  // 一度適用したら永続フラグで二度と自動では畳まない (利用者の開閉を尊重)。
  useEffect(() => {
    if (window.innerWidth < SIDEBAR_AUTO_COLLAPSE_MAX_WIDTH) applySidebarAutoCollapse();
  }, [applySidebarAutoCollapse]);

  // WebView/PWA では 100vh (環境によっては 100dvh も) がビューポート実寸より大きく
  // 計算され、アプリ枠の下端が画面外に欠ける (= 内部スクロールが最後まで届かない)。
  // JS で実測した window.innerHeight を --app-vh に流し込み、それを高さの正とする。
  useEffect(() => {
    const set = () =>
      document.documentElement.style.setProperty('--app-vh', `${window.innerHeight}px`);
    set();
    window.addEventListener('resize', set);
    window.addEventListener('orientationchange', set);
    return () => {
      window.removeEventListener('resize', set);
      window.removeEventListener('orientationchange', set);
    };
  }, []);

  return (
    <div
      className="flex h-screen w-screen overflow-hidden bg-bg-window p-[14px]"
      style={{ height: 'var(--app-vh, 100dvh)' }}
    >
      <div className="flex h-full w-full overflow-hidden rounded-xl bg-bg-base shadow-md">
        <Sidebar collapsed={collapsed} />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Header onToggleSidebar={() => setSidebarCollapsed(!collapsed)} />
          {/* 余白は 1536px 未満で 16px に縮める (狭い画面の盤面幅確保・§2-B4)。FHD 以上は従来どおり。 */}
          <main className="flex-1 overflow-y-auto bg-bg-app p-4 2xl:p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
