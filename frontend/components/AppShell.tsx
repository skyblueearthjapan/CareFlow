'use client';

import { Sidebar } from '@/components/Sidebar';
import { Header } from '@/components/Header';
import { useUIStore } from '@/lib/stores/ui';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);

  return (
    // 100dvh: WebView/PWA では 100vh がビューポート実寸より大きく計算され下端が
    // 画面外に欠けるため、対応環境では dvh を優先する (非対応は h-screen にフォールバック)。
    <div
      className="flex h-screen w-screen overflow-hidden bg-bg-window p-[14px]"
      style={{ height: '100dvh' }}
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
