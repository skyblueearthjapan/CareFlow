import type { DefaultSession } from 'next-auth';

export type AppRole = 'admin' | 'manager' | 'staff';

declare module 'next-auth' {
  interface Session {
    user: {
      role: AppRole;
    } & DefaultSession['user'];
    accessToken?: string;
  }

  interface User {
    role?: AppRole;
    accessToken?: string;
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    role?: AppRole;
    accessToken?: string;
  }
}
