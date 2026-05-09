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

  if (pathname.startsWith(ADMIN_PREFIX) && role !== 'admin') {
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
