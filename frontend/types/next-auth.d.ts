import type { DefaultSession } from 'next-auth';
import type { AppRole } from '@/types/auth';

declare module 'next-auth' {
  interface Session {
    user: {
      id: string;
      role: AppRole;
    } & DefaultSession['user'];
    accessToken?: string;
    refreshToken?: string;
  }

  interface User {
    role: AppRole;
    accessToken: string;
    refreshToken: string;
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    role?: AppRole;
    accessToken?: string;
    refreshToken?: string;
  }
}
