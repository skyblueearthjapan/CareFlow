import NextAuth, { type DefaultSession } from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { z } from 'zod';

export type AppRole = 'admin' | 'manager' | 'staff';

const credentialsSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
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

        // TODO: replace with real call to BACKEND_API_BASE_URL /api/v1/auth/login
        // const res = await fetch(`${process.env.BACKEND_API_BASE_URL}/auth/login`, { ... })
        // expected response: { id, email, name, role: 'admin'|'manager'|'staff', accessToken }
        if (!email || !password) return null;

        return {
          id: 'placeholder-user-id',
          email,
          name: email.split('@')[0] ?? 'user',
          role: 'staff' satisfies AppRole,
          accessToken: 'placeholder-bearer-token',
        };
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.role = (user as { role?: AppRole }).role ?? 'staff';
        token.accessToken = (user as { accessToken?: string }).accessToken;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.role = (token.role as AppRole | undefined) ?? 'staff';
      }
      (session as DefaultSession & { accessToken?: string }).accessToken =
        token.accessToken as string | undefined;
      return session;
    },
  },
});
