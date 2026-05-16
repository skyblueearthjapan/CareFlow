'use client';

import { useEffect } from 'react';
import { signOut, useSession } from 'next-auth/react';

export function SessionErrorGuard({ children }: { children: React.ReactNode }) {
  const { data: session } = useSession();

  useEffect(() => {
    if (session?.error === 'RefreshAccessTokenError') {
      signOut({ callbackUrl: '/login' });
    }
  }, [session?.error]);

  return <>{children}</>;
}
