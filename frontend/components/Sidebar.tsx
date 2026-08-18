'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession } from 'next-auth/react';
import {
  LayoutDashboard,
  Users,
  UserCircle2,
  CalendarDays,
  CalendarHeart,
  Grid3x3,
  Building2,
  Plug,
  ScrollText,
  Inbox,
  MapPin,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface SidebarProps {
  collapsed: boolean;
}

const NAV_ITEMS = [
  { href: '/dashboard', label: 'ダッシュボード', icon: LayoutDashboard },
  { href: '/schedule', label: 'スケジュール', icon: CalendarDays },
  { href: '/acceptance', label: '受け入れ枠', icon: Grid3x3 },
  // RB (PO決定 2026-07-08): PC版は全ロール同一表示。モニターは閲覧を staff にも開放
  // (BE GET も staff 許可済み。確認済み操作等の書込みは admin/manager のまま)。
  { href: '/monitor', label: '訪問モニター', icon: MapPin },
  // 現場ボード (/m) は PC 盤(親機サイドバー)では不要のため非表示。
  // モバイル現場ビュー自体は /m に存在し、モバイル導線 (MobileShell) から開ける。
  { href: '/offices', label: '拠点', icon: Building2 },
  { href: '/staff', label: 'スタッフ', icon: UserCircle2 },
  { href: '/patients', label: '患者', icon: Users },
  // 「連携」= カイポケ ジョブセンター（操作卓）を直接開く。Geocoding キャッシュ等の
  // 管理ユーティリティはジョブセンター画面内のリンクから /integrations へ。
  // 連携はページ側が admin 限定 (認証情報等のセンシティブ) のためメニューも admin のみに揃える
  // (旧: manager にも表示されるが中身で拒否される矛盾があった)。
  { href: '/integrations/kaipoke', label: '連携', icon: Plug, adminOnly: true, strictAdmin: true },
  { href: '/admin/pending-requests', label: '申請履歴', icon: Inbox, adminOnly: true },
  // スタッフ別の休み調整 + 月次出勤カレンダー確定 (staff-shift-confirmation-design.md §3)
  { href: '/admin/staff-leave', label: '休み・月確定', icon: CalendarHeart, adminOnly: true },
  // Wave 4-F: admin audit logs (admin role only). User management was moved
  // to the header rightmost button so admins can reach it from any screen.
  {
    href: '/admin/audit-logs',
    label: '監査ログ',
    icon: ScrollText,
    adminOnly: true,
    strictAdmin: true,
  },
  // 特別訪問週間 は患者マスタの設定として取り込む方針となったため、
  // 独立したサイドバー項目は削除（v2 設計 §3.4 参照）。
  // /special-weeks 配下のページ自体は v2 実装時にデプリケート予定。
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
        {/* eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 */}
        <img src="/brand/rakusuke-icon-round.png" alt="らく助" className="h-9 w-auto shrink-0" />
        {!collapsed && (
          <span className="min-w-0">
            <span className="block font-serif text-lg font-bold leading-tight text-text-primary">
              らく助
            </span>
            <span className="block truncate text-[10px] leading-tight text-text-muted">
              訪問看護 楽々スケジュール
            </span>
          </span>
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
