/**
 * Lightweight fetcher used by TanStack Query hooks while we wait for the
 * fully typed openapi-fetch contract (see lib/api/types.ts TODO).
 *
 * Once `openapi-typescript` codegen lands, prefer the typed client from
 * `lib/api/client.ts` and remove this module.
 *
 * 401 handling (Phase 2 Codex review):
 *   - On a first 401 with a refresh token in hand, POST /api/v1/auth/refresh
 *     once and retry the original request with the new access token.
 *   - On a second 401 (or refresh failure) we call `signOut()` so the user is
 *     bounced back to /login. Linear retry only — concurrent 401s may still
 *     race; Phase 3 will add coalescing.
 */
import { signOut } from 'next-auth/react';

import { ApiError } from '@/lib/api-client';

function resolveBaseUrl(): string {
  return (
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    process.env.BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

export interface FetcherOptions extends RequestInit {
  accessToken?: string | null;
  refreshToken?: string | null;
  /** Internal: prevents recursive refresh loops. */
  _retried?: boolean;
}

async function refreshAccessToken(refreshToken: string): Promise<string | null> {
  try {
    const res = await fetch(`${resolveBaseUrl()}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: 'no-store',
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { access_token?: string };
    return typeof body.access_token === 'string' ? body.access_token : null;
  } catch {
    return null;
  }
}

export async function fetcher<T = unknown>(
  path: string,
  { accessToken, refreshToken, headers, _retried, ...init }: FetcherOptions = {},
): Promise<T> {
  const url = path.startsWith('http') ? path : `${resolveBaseUrl()}${path}`;
  const h = new Headers(headers ?? {});
  if (!h.has('Content-Type')) {
    h.set('Content-Type', 'application/json');
  }
  if (accessToken) {
    h.set('Authorization', `Bearer ${accessToken}`);
  }

  const res = await fetch(url, { ...init, headers: h, cache: init.cache ?? 'no-store' });

  if (res.status === 401 && !_retried && refreshToken) {
    const newToken = await refreshAccessToken(refreshToken);
    if (newToken) {
      return fetcher<T>(path, {
        ...init,
        headers,
        accessToken: newToken,
        refreshToken,
        _retried: true,
      });
    }
    // Refresh failed → clear session and bounce to login.
    if (typeof window !== 'undefined') {
      void signOut({ callbackUrl: '/login' });
    }
  }

  const text = await res.text();
  const body: unknown = text ? safeJsonParse(text) : null;

  if (!res.ok) {
    throw new ApiError(`API ${res.status} ${res.statusText} (${path})`, res.status, body);
  }
  return body as T;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
