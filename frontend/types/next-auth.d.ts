import type { DefaultSession } from 'next-auth';
import type { AppRole } from '@/types/auth';

declare module 'next-auth' {
  interface Session {
    user: {
      id: string;
      role: AppRole;
      staffId?: string | null;
      mustChangePassword?: boolean;
    } & DefaultSession['user'];
    accessToken?: string;
    refreshToken?: string;
    /** 'RefreshAccessTokenError' when the jwt callback could not refresh tokens. */
    error?: string;
  }

  interface User {
    role: AppRole;
    staffId?: string | null;
    mustChangePassword?: boolean;
    accessToken: string;
    refreshToken: string;
  }
}

declare module 'next-auth/jwt' {
  interface JWT {
    role?: AppRole;
    staffId?: string | null;
    mustChangePassword?: boolean;
    accessToken?: string;
    refreshToken?: string;
    /** Unix ms at which accessToken expires. Used by jwt callback to auto-refresh. */
    accessTokenExpires?: number;
    /** Set to 'RefreshAccessTokenError' when refresh fails. */
    error?: string;
  }
}
