'use client';

import { useState } from 'react';
import { Sidebar } from '@/components/Sidebar';
import { Header } from '@/components/Header';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-bg-window p-[14px]">
      <div className="flex h-full w-full overflow-hidden rounded-xl bg-bg-base shadow-md">
        <Sidebar collapsed={collapsed} />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Header onToggleSidebar={() => setCollapsed((v) => !v)} />
          <main className="flex-1 overflow-y-auto bg-bg-app p-6">{children}</main>
        </div>
      </div>
    </div>
  );
}
