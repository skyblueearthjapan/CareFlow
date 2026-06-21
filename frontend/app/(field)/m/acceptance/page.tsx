'use client';

/**
 * /m/acceptance — CareFlow Mobile 受け入れ枠マトリックス (P3)。
 *
 * 認証ガードは /m (現場ボード) と同一: manager / admin のみ。staff は /m/home へ。
 * (field) レイアウトのフルスクリーン・フォントスコープをそのまま継承する。
 */

import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useEffect } from 'react';

import { AcceptanceFieldView } from '@/components/field/AcceptanceFieldView';

export default function FieldAcceptancePage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const allowed = role === 'admin' || role === 'manager';

  useEffect(() => {
    if (status === 'authenticated' && !allowed) {
      router.replace('/m/home');
    }
  }, [status, allowed, router]);

  if (status !== 'authenticated' || !allowed) {
    return null;
  }

  return (
    <div style={{ height: '100%' }}>
      <AcceptanceFieldView />
    </div>
  );
}
