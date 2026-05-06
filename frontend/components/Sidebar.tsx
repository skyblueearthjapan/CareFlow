'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  LayoutDashboard,
  Users,
  UserCircle2,
  CalendarDays,
  Heart,
  Building2,
  Plug,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface SidebarProps {
  collapsed: boolean;
}

const NAV_ITEMS = [
  { href: '/dashboard', label: 'ダッシュボード', icon: LayoutDashboard },
  { href: '/schedule', label: 'スケジュール', icon: CalendarDays },
  { href: '/patients', label: '患者', icon: Users },
  { href: '/staff', label: 'スタッフ', icon: UserCircle2 },
  { href: '/offices', label: '拠点', icon: Building2 },
  { href: '/integrations', label: '連携', icon: Plug, adminOnly: true },
  // 申請履歴 (admin/manager) / 監査ログ (admin) / AIヘルプ (全員) は
  // 上部ヘッダーのアイコンボタン群に移動済 (Header.tsx 参照)。
  // 特別訪問週間 は患者マスタの設定として取り込む方針となったため、
  // 独立したサイドバー項目は削除（v2 設計 §3.4 参照）。
  // /special-weeks 配下のページ・API は W6-MIG2 で削除済。
] as const;

export function Sidebar({ collapsed }: SidebarProps) {
  const pathname = usePathname();
  const { data: session } = useSession();
  const role = session?.user?.role;
  const width = collapsed ? 'w-[72px]' : 'w-[232px]';

  const items = NAV_ITEMS.filter((item) => {
    // strictAdmin items are visible only to `admin`. Other adminOnly items
    // remain visible to admin + manager (the long-standing convention).
    if ('strictAdmin' in item && item.strictAdmin) {
      return role === 'admin';
    }
    if ('adminOnly' in item && item.adminOnly) {
      return role !== 'staff';
    }
    return true;
  });

  return (
    <aside
      className={cn(
        'flex h-full flex-col border-r border-border-default bg-bg-base transition-[width] duration-200 ease-out',
        width,
      )}
      aria-label="Primary navigation"
    >
      {/* Brand area: 60px to align with header */}
      <div className="flex h-[60px] items-center gap-2 border-b border-border-default px-4">
        <Heart className="h-6 w-6 text-brand-primary" strokeWidth={1.75} />
        {!collapsed && (
          <span className="font-serif text-lg font-bold text-text-primary">CareFlow</span>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto p-3">
        <ul className="space-y-1">
          {items.map(({ href, label, icon: Icon }) => {
            const active = pathname?.startsWith(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={cn(
                    'flex h-11 items-center gap-3 rounded-md px-3 text-sm transition-colors',
                    active
                      ? 'bg-brand-primary-light text-brand-primary-hover font-medium'
                      : 'text-text-secondary hover:bg-bg-muted',
                  )}
                  title={collapsed ? label : undefined}
                >
                  <Icon className="h-5 w-5 shrink-0" strokeWidth={1.75} />
                  {!collapsed && <span>{label}</span>}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer card slot */}
      <div className="border-t border-border-default p-3">
        {!collapsed && <p className="text-xs text-text-muted">v0.1.0 — D2 Foundation</p>}
      </div>
    </aside>
  );
}
