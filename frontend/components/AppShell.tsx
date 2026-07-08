'use client';

import { useEffect } from 'react';

import { Sidebar } from '@/components/Sidebar';
import { Header } from '@/components/Header';
import { useUIStore } from '@/lib/stores/ui';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);

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
          <main className="flex-1 overflow-y-auto bg-bg-app p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
