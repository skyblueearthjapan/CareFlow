'use client';

/**
 * /m — CareFlow Mobile 現場ボード (Phase 1: フロント完結・ダミーデータ)。
 *
 * 認証ガード: manager / admin のみ。staff には非表示 (/m/home へ戻す)。
 *   - 認証そのものは middleware (decideRoute) が担保済み。
 *   - ここでは role が staff の場合に早期リダイレクトする
 *     ((app)/staff/new と同じクライアントガードパターン)。
 *
 * ⚠️ バックエンド接続なし。ボード/カルテ/提案はすべて `components/field/mockData`。
 */

import { useRouter } from 'next/navigation';
import { useSession } from 'next-auth/react';
import { useEffect } from 'react';

import { FieldBoard } from '@/components/field/FieldBoard';

export default function FieldBoardPage() {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = session?.user?.role;
  const allowed = role === 'admin' || role === 'manager';

  useEffect(() => {
    // 現場ボードは管理者・管理職向け。staff はモバイル staff ホームへ戻す。
    if (status === 'authenticated' && !allowed) {
      router.replace('/m/home');
    }
  }, [status, allowed, router]);

  // セッション確定前 / 権限なしのときはボードを描画しない (フラッシュ防止)。
  if (status !== 'authenticated' || !allowed) {
    return null;
  }

  // ノッチ/ステータスバーぶんの安全余白を確保しつつ、現場ボードを全画面表示。
  return (
    <div style={{ height: '100%', paddingTop: 'env(safe-area-inset-top)' }}>
      <FieldBoard topPad={20} />
    </div>
  );
}
