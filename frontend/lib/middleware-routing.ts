/**
 * Wave 40 — pure routing decision used by `middleware.ts`.
 *
 * Lives in `lib/` (no NextAuth / next/server imports) so it can be unit-tested
 * under vitest+jsdom without dragging the Next.js server runtime in.
 */

const PUBLIC_PATHS = ['/login'];
const ADMIN_PREFIX = '/admin';
const COMMON_PREFIXES = ['/dashboard', '/patients', '/staff', '/schedule'];

/**
 * Path of the forced-password-change screen. The middleware is the only
 * place this constant is consumed at runtime; tests assert on it.
 */
export const FORCED_PASSWORD_PATH = '/settings/password/forced';

export type RouteDecision =
  | { kind: 'next' }
  | { kind: 'redirect'; to: string; preserveCallback?: boolean };

export interface RouteContext {
  pathname: string;
  session: { user?: { role?: string; mustChangePassword?: boolean } } | null;
}

/**
 * Pure routing decision used by the NextAuth middleware. Pinned by
 * `__tests__/middleware.test.ts`.
 */
export function decideRoute({ pathname, session }: RouteContext): RouteDecision {
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return { kind: 'next' };
  }

  if (!session) {
    return { kind: 'redirect', to: '/login', preserveCallback: true };
  }

  const role = session.user?.role;
  const mustChangePassword = session.user?.mustChangePassword === true;

  // Wave 40 — forced password change. Send everything except the forced page
  // (and /api/auth/*, excluded by the route matcher) to the forced screen.
  if (mustChangePassword && pathname !== FORCED_PASSWORD_PATH) {
    return { kind: 'redirect', to: FORCED_PASSWORD_PATH };
  }
  // After clearing the flag, don't strand the user on the forced page.
  if (!mustChangePassword && pathname === FORCED_PASSWORD_PATH) {
    return { kind: 'redirect', to: '/dashboard' };
  }

  // PW運用 (PO決定 2026-07-08): パスワードは全員共通のため、自己変更ページは
  // admin のみ。強制変更ページ (FORCED_PASSWORD_PATH) は上で処理済みなので
  // ここに来る /settings/password/forced は mustChangePassword=false の離脱リダイレクト後だけ。
  // 直リンク対策として非 forced の /settings/password を admin 以外から遮断する。
  if (pathname === '/settings/password' && role !== 'admin') {
    return { kind: 'redirect', to: '/dashboard' };
  }

  // 申請履歴はページ実装が admin+manager 想定 (サイドバーも両者に表示) のため例外。
  // 旧: /admin 一括ガードで manager が弾かれる矛盾があった (RB 2026-07-08 修正)。
  const isPendingRequests = pathname.startsWith('/admin/pending-requests');
  if (
    pathname.startsWith(ADMIN_PREFIX) &&
    // 二軸分離 (2026-08-09): 権限は 2 値。旧 'manager' は admin の別名として許容。
    (isPendingRequests
      ? !['admin', 'manager'].includes(role ?? '')
      : !['admin', 'manager'].includes(role ?? ''))
  ) {
    return { kind: 'redirect', to: '/dashboard' };
  }

  if (
    COMMON_PREFIXES.some((p) => pathname.startsWith(p)) &&
    !['admin', 'manager', 'staff'].includes(role ?? '')
  ) {
    return { kind: 'redirect', to: '/login' };
  }

  return { kind: 'next' };
}
