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
    async jwt({ token, user }) {
      if (user) {
        token.role = user.role;
        token.staffId = user.staffId ?? null;
        token.accessToken = user.accessToken;
        token.refreshToken = user.refreshToken;
        if (user.id) {
          token.sub = user.id;
        }
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.role = (token.role as AppRole | undefined) ?? 'staff';
        session.user.staffId = (token.staffId as string | null | undefined) ?? null;
        if (token.sub) {
          session.user.id = token.sub;
        }
      }
      session.accessToken = token.accessToken as string | undefined;
      session.refreshToken = token.refreshToken as string | undefined;
      return session;
    },
  },
});

export type { AppRole };
