'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, CalendarCheck, CalendarRange, UserCircle2 } from 'lucide-react';
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
  return (
    <div className="flex h-screen w-screen flex-col bg-bg-app">
      <main
        className="flex-1 overflow-y-auto p-4"
        style={{ paddingBottom: 'calc(64px + env(safe-area-inset-bottom))' }}
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
          const active =
            pathname === href || pathname?.startsWith(`${href}/`);
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
