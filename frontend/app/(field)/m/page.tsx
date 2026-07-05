'use client';

/**
 * /m — CareFlow Mobile 現場ボード (Phase2-3c: 実データ接続)。
 *
 * 認証ガード: ログイン済みの全ロール (admin / manager / staff) が閲覧できる。
 *   - 認証そのものは middleware (decideRoute) が担保済み。
 *   - 編集系 (提案・承認・患者管理・直接配置・カルテ編集) は FieldBoard 内で
 *     manager/admin のみに出す (staff は閲覧専用)。
 *
 * Phase2-3c: 実データ接続済み。ボード/カルテ/提案/承認はすべて実 API
 *   (GET /schedule/v2/board, GET /patients/{id}, POST /schedule/v2/propose-slots,
 *    GET/PATCH /pending-requests) に接続。フォントは (field) レイアウトで読込。
 */

import { useSession } from 'next-auth/react';

import { FieldBoard } from '@/components/field/FieldBoard';

export default function FieldBoardPage() {
  const { status } = useSession();

  // セッション確定前はボードを描画しない (フラッシュ防止)。
  if (status !== 'authenticated') {
    return null;
  }

  // 現場ボードを全画面表示。ノッチ/ステータスバーぶんの安全余白は
  // FieldBoard 最上部の「← モバイルアプリへ」バー (BackToAppBar) が
  // env(safe-area-inset-top) で吸収するため、ここでは padding を持たない
  // (二重に空けない)。
  return (
    <div style={{ height: '100%' }}>
      <FieldBoard />
    </div>
  );
}
