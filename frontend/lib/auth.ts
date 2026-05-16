import NextAuth from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { z } from 'zod';
import { env } from '@/lib/config/env';
import type { AppRole } from '@/types/auth';

const credentialsSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

// Backend returns the LoginResponse shape from `backend/app/schemas/auth.py`:
//   { user: { id, email, role, staff_id }, tokens: { access_token, refresh_token } }
// (See Phase 2 Codex review — frontend was previously parsing a flat object.)
const loginResponseSchema = z.object({
  user: z.object({
    id: z.union([z.string(), z.number()]).transform((v) => String(v)),
    email: z.string().email(),
    name: z.string().optional(),
    role: z.enum(['admin', 'manager', 'staff']),
    staff_id: z.string().nullable().optional(),
    // Wave 40: BE LoginResponse now carries this flag. Defaults to false to
    // stay tolerant of older builds while a redeploy rolls out.
    must_change_password: z.boolean().optional().default(false),
  }),
  tokens: z.object({
    access_token: z.string().min(1),
    refresh_token: z.string().min(1),
  }),
});

export const { handlers, auth, signIn, signOut } = NextAuth({
  session: { strategy: 'jwt' },
  trustHost: true,
  pages: {
    signIn: '/login',
  },
  providers: [
    Credentials({
      name: 'Credentials',
      credentials: {
        email: { label: 'Email', type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(raw) {
        const parsed = credentialsSchema.safeParse(raw);
        if (!parsed.success) return null;
        const { email, password } = parsed.data;

        try {
          const res = await fetch(`${env.BACKEND_API_BASE_URL}/api/v1/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
            cache: 'no-store',
          });
          if (res.status !== 200) return null;
          const json: unknown = await res.json();
          const payload = loginResponseSchema.safeParse(json);
          if (!payload.success) return null;
          const { user, tokens } = payload.data;
          return {
            id: user.id,
            email: user.email,
            name: user.name ?? user.email.split('@')[0] ?? 'user',
            role: user.role,
            staffId: user.staff_id ?? null,
            mustChangePassword: user.must_change_password,
            accessToken: tokens.access_token,
            refreshToken: tokens.refresh_token,
          };
        } catch {
          return null;
        }
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user, trigger, session: updateSession }) {
      // Early-exit: if refresh already failed, do not retry on every request.
      if (token.error === 'RefreshAccessTokenError') {
        return token;
      }

      if (user) {
        token.role = user.role;
        token.staffId = user.staffId ?? null;
        token.mustChangePassword = user.mustChangePassword ?? false;
        token.accessToken = user.accessToken;
        token.refreshToken = user.refreshToken;
        // Record when the access token expires (backend issues 1-hour tokens).
        token.accessTokenExpires = Date.now() + 55 * 60 * 1000;
        token.error = undefined;
        if (user.id) {
          token.sub = user.id;
        }
      }
      // Wave 40: after a successful self-service password change the FE calls
      // `update({ mustChangePassword: false })` which lands here as a `update`
      // trigger. We trust the FE to clear the flag without round-tripping the
      // BE because the BE already wrote false to the DB; the next /me lookup
      // would corroborate.
      if (trigger === 'update' && updateSession && typeof updateSession === 'object') {
        const next = (updateSession as { mustChangePassword?: boolean }).mustChangePassword;
        if (typeof next === 'boolean') {
          token.mustChangePassword = next;
        }
      }

      // Auto-refresh: if the access token is still valid, pass through as-is.
      const expires = token.accessTokenExpires as number | undefined;
      if (expires && Date.now() < expires) {
        return token;
      }

      // Access token has expired (or no expiry recorded) — attempt refresh.
      if (token.refreshToken) {
        try {
          const res = await fetch(`${env.BACKEND_API_BASE_URL}/api/v1/auth/refresh`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: token.refreshToken }),
            cache: 'no-store',
          });
          if (res.ok) {
            const data = (await res.json()) as {
              access_token?: string;
              refresh_token?: string;
            };
            if (data.access_token && data.refresh_token) {
              return {
                ...token,
                accessToken: data.access_token,
                refreshToken: data.refresh_token,
                accessTokenExpires: Date.now() + 55 * 60 * 1000,
                error: undefined,
              };
            }
          }
        } catch (err) {
          console.error('[NextAuth] Token refresh failed:', err);
        }
        // Refresh failed — signal the client to re-authenticate.
        return { ...token, error: 'RefreshAccessTokenError' };
      }

      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.role = (token.role as AppRole | undefined) ?? 'staff';
        session.user.staffId = (token.staffId as string | null | undefined) ?? null;
        session.user.mustChangePassword =
          (token.mustChangePassword as boolean | undefined) ?? false;
        if (token.sub) {
          session.user.id = token.sub;
        }
      }
      session.accessToken = token.accessToken as string | undefined;
      session.refreshToken = token.refreshToken as string | undefined;
      session.error = token.error as string | undefined;
      return session;
    },
  },
  events: {
    // PHI-adjacent local fallback records (mobile check-in/out) must not
    // outlive the session — purge every `checkin:*` key on sign-out so a
    // user-switch on a shared device cannot read the previous account's
    // data. Runs on the client; on the server `localStorage` is undefined
    // and `clearAllCheckins()` is a no-op.
    async signOut() {
      if (typeof window !== 'undefined') {
        const { clearAllCheckins } = await import('@/lib/checkin-storage');
        clearAllCheckins();
      }
    },
  },
});

export type { AppRole };
