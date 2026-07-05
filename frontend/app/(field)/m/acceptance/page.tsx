'use client';

/**
 * /m/acceptance — CareFlow Mobile 受け入れ枠マトリックス (P3)。
 *
 * 認証ガードは /m (現場ボード) と同一: ログイン済みの全ロールが閲覧できる
 * (モバイル版マトリックスはもともと読み取り専用)。
 * (field) レイアウトのフルスクリーン・フォントスコープをそのまま継承する。
 */

import { useSession } from 'next-auth/react';

import { AcceptanceFieldView } from '@/components/field/AcceptanceFieldView';

export default function FieldAcceptancePage() {
  const { status } = useSession();

  if (status !== 'authenticated') {
    return null;
  }

  return (
    <div style={{ height: '100%' }}>
      <AcceptanceFieldView />
    </div>
  );
}
