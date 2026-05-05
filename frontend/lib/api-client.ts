/**
 * Bearer Token を自動添付する fetch ラッパ。
 * server-side では `auth()` から、client-side では `useSession()` から取得した accessToken を渡す。
 *
 * @deprecated Phase 2 以降の新規コードは `lib/api/client.ts` (openapi-fetch ベース) を優先してください。
 *             本ファイルは既存呼び出し互換のため legacy として残しています。
 */

function resolveBaseUrl(): string {
  if (typeof window !== 'undefined') return '';
  return process.env.BACKEND_API_BASE_URL ?? 'http://localhost:8000';
}

export interface ApiClientOptions extends RequestInit {
  accessToken?: string | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function apiFetch<T = unknown>(
  path: string,
  { accessToken, headers, ...init }: ApiClientOptions = {},
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
