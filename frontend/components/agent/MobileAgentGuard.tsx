'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { useSession } from 'next-auth/react';

import { isAdminRole } from '@/lib/rbac';

/** 相談タブは管理者だけ。一般職員がURLを直接開いてもホームへ戻す。 */
export function MobileAgentGuard({ children }: { children: React.ReactNode }) {
  const { data: session, status } = useSession();
  const router = useRouter();
  const admin = isAdminRole((session?.user as { role?: string } | undefined)?.role);
  const denied = status === 'authenticated' && !admin;
  useEffect(() => {
    if (denied) router.replace('/m/home');
  }, [denied, router]);
  if (status !== 'authenticated' || !admin) return null;
  return <>{children}</>;
}
