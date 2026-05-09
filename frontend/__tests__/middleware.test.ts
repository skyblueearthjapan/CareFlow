/**
 * Wave 40 — middleware.decideRoute pure-function tests.
 *
 * decideRoute() encapsulates the routing rules used by the next-auth
 * middleware. Testing it directly avoids spinning up next-auth runtime in
 * jsdom while still pinning the high-value redirects (forced password change
 * + RBAC fallbacks).
 */
import { describe, it, expect } from 'vitest';

import { decideRoute, FORCED_PASSWORD_PATH } from '@/lib/middleware-routing';

const adminSession = { user: { role: 'admin' as const, mustChangePassword: false } };
const staffSession = { user: { role: 'staff' as const, mustChangePassword: false } };
const forcedSession = { user: { role: 'staff' as const, mustChangePassword: true } };

describe('decideRoute', () => {
  // ── public ────────────────────────────────────────────────────────────
  it('lets /login through unchanged', () => {
    expect(decideRoute({ pathname: '/login', session: null })).toEqual({ kind: 'next' });
    expect(decideRoute({ pathname: '/login?x=1', session: null })).toEqual({ kind: 'next' });
  });

  // ── unauthenticated ───────────────────────────────────────────────────
  it('redirects unauthenticated requests to /login with callbackUrl', () => {
    expect(decideRoute({ pathname: '/dashboard', session: null })).toEqual({
      kind: 'redirect',
      to: '/login',
      preserveCallback: true,
    });
  });

  // ── forced password change ────────────────────────────────────────────
  it('forces /settings/password/forced when mustChangePassword=true on dashboard', () => {
    expect(decideRoute({ pathname: '/dashboard', session: forcedSession })).toEqual({
      kind: 'redirect',
      to: FORCED_PASSWORD_PATH,
    });
  });

  it('forces /settings/password/forced from /admin/users', () => {
    expect(
      decideRoute({
        pathname: '/admin/users',
        session: { user: { role: 'admin', mustChangePassword: true } },
      }),
    ).toEqual({ kind: 'redirect', to: FORCED_PASSWORD_PATH });
  });

  it('lets the forced page itself through (no redirect loop)', () => {
    expect(decideRoute({ pathname: FORCED_PASSWORD_PATH, session: forcedSession })).toEqual({
      kind: 'next',
    });
  });

  it('bounces away from the forced page once mustChangePassword is false', () => {
    expect(decideRoute({ pathname: FORCED_PASSWORD_PATH, session: staffSession })).toEqual({
      kind: 'redirect',
      to: '/dashboard',
    });
  });

  // ── RBAC (regression — must still work after Wave 40 changes) ─────────
  it('redirects non-admin away from /admin/* to /dashboard', () => {
    expect(decideRoute({ pathname: '/admin/users', session: staffSession })).toEqual({
      kind: 'redirect',
      to: '/dashboard',
    });
  });

  it('lets admin access /admin/users', () => {
    expect(decideRoute({ pathname: '/admin/users', session: adminSession })).toEqual({
      kind: 'next',
    });
  });

  it('lets staff access /dashboard', () => {
    expect(decideRoute({ pathname: '/dashboard', session: staffSession })).toEqual({
      kind: 'next',
    });
  });

  it('lets admin access the patients page', () => {
    expect(decideRoute({ pathname: '/patients/abc', session: adminSession })).toEqual({
      kind: 'next',
    });
  });

  // ── must_change_password absent on session is treated as false ────────
  it('treats missing mustChangePassword as false (regression / older sessions)', () => {
    expect(
      decideRoute({
        pathname: '/dashboard',
        session: { user: { role: 'staff' } },
      }),
    ).toEqual({ kind: 'next' });
  });
});
