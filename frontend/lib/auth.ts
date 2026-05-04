import NextAuth from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { z } from 'zod';
import { env } from '@/lib/config/env';
import type { AppRole } from '@/types/auth';

const credentialsSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

const loginResponseSchema = z.object({
  id: z.union([z.string(), z.number()]).transform((v) => String(v)),
  email: z.string().email(),
  name: z.string().optional(),
  role: z.enum(['admin', 'manager', 'staff']),
  accessToken: z.string().min(1),
});

export const { handlers, auth, signIn, signOut } = NextAuth({
  session: { strategy: 'jwt' },
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
          return {
            id: payload.data.id,
            email: payload.data.email,
            name: payload.data.name ?? payload.data.email.split('@')[0] ?? 'user',
            role: payload.data.role,
            accessToken: payload.data.accessToken,
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
        token.accessToken = user.accessToken;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.role = token.role ?? 'staff';
      }
      session.accessToken = token.accessToken;
      return session;
    },
  },
});

export type { AppRole };
