'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  Home,
  CalendarCheck,
  CalendarRange,
  UserCircle2,
  LayoutGrid,
  ChevronRight,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const TABS = [
  { href: '/m/home', label: 'ホーム', icon: Home },
  { href: '/m/today', label: '今日', icon: CalendarCheck },
  { href: '/m/this-week', label: '今週', icon: CalendarRange },
  { href: '/m/mypage', label: 'マイページ', icon: UserCircle2 },
] as const;

interface MobileShellProps {
  children: React.ReactNode;
}

export function MobileShell({ children }: MobileShellProps) {
  const pathname = usePathname();
  const { data: session } = useSession();
  // 現場ボード (/m) は全ロールが閲覧できる (staff は閲覧専用・編集系はボード内で出さない)。
  const showFieldBoard = !!session;
  return (
    // h-screen(100vh) はモバイルブラウザの URL バー表示時に可視領域より高くなり、
    // 最下行が固定下タブの裏に隠れる。dvh 対応ブラウザでは動的ビューポート高
    // (100dvh) を使い、可視領域とスクロール範囲を一致させる (非対応は 100vh のまま)。
    <div className="flex h-screen w-screen flex-col bg-bg-app supports-[height:100dvh]:h-dvh">
      {showFieldBoard && (
        <Link
          href="/m"
          className="flex shrink-0 items-center justify-center gap-1.5 border-b border-brand-primary-light bg-brand-primary-50 px-4 py-2 text-xs font-medium text-brand-primary transition-colors hover:bg-brand-primary-light"
        >
          <LayoutGrid className="h-3.5 w-3.5" strokeWidth={2} />
          <span>現場ボードを開く</span>
          <ChevronRight className="h-3.5 w-3.5" strokeWidth={2} />
        </Link>
      )}
      <main
        className="flex-1 overflow-y-auto p-4"
        // 下タブ (h-16=64px + safe-area) + 視認用の余白 16px を常に確保する。
        style={{ paddingBottom: 'calc(64px + env(safe-area-inset-bottom) + 16px)' }}
      >
        {children}
      </main>
      <nav
        className="fixed inset-x-0 bottom-0 z-40 flex h-16 border-t border-border-default bg-bg-base"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
        aria-label="Bottom navigation"
      >
        {TABS.map(({ href, label, icon: Icon }) => {
          // Exact match OR a strict path-segment prefix (e.g. `/m/today/123`).
          // Avoid the loose `startsWith(href)` which would also match
          // `/m/home123` or `/m/todayspecial`.
          const active = pathname === href || pathname?.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'flex flex-1 flex-col items-center justify-center gap-1 text-xs',
                active ? 'text-brand-primary' : 'text-text-muted',
              )}
            >
              <Icon className="h-5 w-5" strokeWidth={1.75} />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
