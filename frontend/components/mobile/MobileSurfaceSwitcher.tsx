'use client';

/**
 * らく助モバイル 共通切替ピルバー (R-8・PO決定 2026-07-10)。
 *
 * モバイル3面 (ホーム=/m/home系 / ボード=/m / 受け入れ枠=/m/acceptance) の最上部に
 * 同じ見た目・同じ位置で置く。「現場ボードを開く」「〜へ戻る」等のバラバラな文言を
 * 廃止し、どの画面からでも直接行き来できるタブ型にする。
 */
import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

const SURFACES = [
  {
    href: '/m/home',
    label: 'ホーム',
    match: (p: string) => p.startsWith('/m/') && !p.startsWith('/m/acceptance'),
  },
  { href: '/m', label: 'ボード', match: (p: string) => p === '/m' },
  {
    href: '/m/acceptance',
    label: '受け入れ枠',
    match: (p: string) => p.startsWith('/m/acceptance'),
  },
] as const;

export function MobileSurfaceSwitcher() {
  const pathname = usePathname() ?? '';
  return (
    <div
      className="flex shrink-0 items-center gap-2 border-b border-brand-primary-light bg-brand-primary-50 px-3 pb-1.5"
      style={{ paddingTop: 'calc(env(safe-area-inset-top) + 6px)' }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- 静的ブランド画像 */}
      <img
        src="/brand/rakusuke-icon-round.png"
        alt="らく助モバイル"
        className="h-6 w-auto shrink-0"
      />
      <nav className="flex flex-1 gap-1 rounded-full bg-white/60 p-0.5" aria-label="画面切替">
        {SURFACES.map((s) => {
          const active = s.match(pathname);
          return (
            <Link
              key={s.href}
              href={s.href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex-1 rounded-full px-2 py-1.5 text-center text-xs font-bold',
                active
                  ? 'bg-white text-brand-primary-hover shadow-sm'
                  : 'text-brand-primary hover:bg-white/50',
              )}
            >
              {s.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
