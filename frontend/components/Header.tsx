'use client';

import { Menu, Bell, User, Heart } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface HeaderProps {
  title?: string;
  onToggleSidebar: () => void;
}

export function Header({ title = 'CareFlow', onToggleSidebar }: HeaderProps) {
  return (
    <header className="flex h-[60px] items-center justify-between border-b border-border-default bg-bg-base px-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="サイドバーを開閉">
          <Menu className="h-5 w-5" strokeWidth={1.75} />
        </Button>
      </div>

      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon" aria-label="通知">
          <Bell className="h-5 w-5" strokeWidth={1.75} />
        </Button>
        <Button variant="ghost" size="icon" aria-label="ユーザーメニュー">
          <User className="h-5 w-5" strokeWidth={1.75} />
        </Button>
        <div className="ml-2 flex items-center gap-2">
          <Heart className="h-6 w-6 text-brand-primary" strokeWidth={1.75} />
          <span className="font-serif text-lg font-bold text-text-primary">{title}</span>
        </div>
        {/* TODO: NextAuth signOut, role badge */}
      </div>
    </header>
  );
}
