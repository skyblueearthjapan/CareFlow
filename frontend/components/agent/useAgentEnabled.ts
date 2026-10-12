'use client';

import { useSession } from 'next-auth/react';

import { isAdminRole } from '@/lib/rbac';

import { useAgentFlag } from './useAgentFlag';

/**
 * 小窓・相談タブを出してよいか。次の 3 つが全部そろった時だけ true(設計書 §4-2・§4-5)。
 *   1. 管理者(admin / 旧 manager)
 *   2. 試験の許可リストに入っている(NEXT_PUBLIC_AGENT_PILOT_USERS = ユーザー ID かメールのカンマ区切り。
 *      空なら誰にも出さない・'*' なら管理者全員)
 *   3. 機能の旗 rakusuke_agent が有効
 * 開発時だけ NEXT_PUBLIC_AGENT_FLAG_FORCE_ON=1 で 2・3 を飛ばせる(本番ビルドでは無効)。
 * 中継側も同じ条件を確かめる(画面はあくまで出し分け)。
 */
export function parseAllowList(raw: string | undefined): string[] {
  return (raw ?? '')
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export function isInPilot(
  user: { id?: string | null; email?: string | null } | undefined,
  allow: string[],
): boolean {
  if (!user) return false;
  // '*' = 管理者全員（PO 2026-10-13）。管理者かどうかは呼び出し側で別に確かめる
  if (allow.includes('*')) return true;
  const keys = [user.id, user.email].filter(Boolean).map((s) => String(s).toLowerCase());
  return keys.some((k) => allow.includes(k));
}

export function useAgentEnabled(): boolean {
  const { data: session } = useSession();
  const user = session?.user as { id?: string; email?: string; role?: string } | undefined;
  const admin = isAdminRole(user?.role);
  const forced =
    process.env.NODE_ENV !== 'production' && process.env.NEXT_PUBLIC_AGENT_FLAG_FORCE_ON === '1';
  const pilot = isInPilot(user, parseAllowList(process.env.NEXT_PUBLIC_AGENT_PILOT_USERS));
  const flag = useAgentFlag(admin && (pilot || forced));
  if (!admin) return false;
  if (forced) return true;
  return pilot && flag;
}
