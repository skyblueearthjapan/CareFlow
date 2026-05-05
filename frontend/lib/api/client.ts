/**
 * openapi-fetch based API client.
 *
 * Authoritative client going forward — prefer this over `lib/api-client.ts`
 * (which is kept as a legacy fallback). Once `openapi-typescript` codegen
 * lands, the `paths` import will become a fully typed contract.
 */
import createClient, { type Middleware } from 'openapi-fetch';

import type { paths } from '@/lib/api/types';

/** Base URL for the backend. Browser uses same-origin relative URLs (handled by
 * Cloudflared path-based routing); server-side uses docker-internal URL. */
function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') {
    return '';
  }
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

export interface CreateApiClientOptions {
  /** Bearer token from NextAuth session, if available. */
  accessToken?: string | null;
  /** Optional override base URL (mainly for tests). */
  baseUrl?: string;
}

/**
 * Build a typed openapi-fetch client.
 *
 * Pass `accessToken` when you have a session in hand (e.g. inside a
 * `useQuery` fetcher with `useSession()`). The middleware wires it into
 * every outbound request.
 */
export function createApiClient(options: CreateApiClientOptions = {}) {
  const { accessToken, baseUrl } = options;
  const client = createClient<paths>({
    baseUrl: baseUrl ?? resolveBaseUrl(),
  });

  const authMiddleware: Middleware = {
    onRequest({ request }) {
      if (accessToken) {
        request.headers.set('Authorization', `Bearer ${accessToken}`);
      }
      if (!request.headers.has('Content-Type')) {
        request.headers.set('Content-Type', 'application/json');
      }
      return request;
    },
  };

  client.use(authMiddleware);
  return client;
}

/** Default unauthenticated client (e.g. for public health endpoints). */
export const apiClient = createApiClient();
