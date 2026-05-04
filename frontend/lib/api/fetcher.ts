/**
 * Lightweight fetcher used by TanStack Query hooks while we wait for the
 * fully typed openapi-fetch contract (see lib/api/types.ts TODO).
 *
 * Once `openapi-typescript` codegen lands, prefer the typed client from
 * `lib/api/client.ts` and remove this module.
 */
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
}

export async function fetcher<T = unknown>(
  path: string,
  { accessToken, headers, ...init }: FetcherOptions = {},
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
