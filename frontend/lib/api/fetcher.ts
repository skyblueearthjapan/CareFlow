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
import { checkAccessSession, notifyAccessExpired, reportPossibleAccessIssue } from '@/lib/cfAccess';

function resolveBaseUrl(): string {
  // In the browser, use relative URLs so requests hit the same origin (e.g.
  // https://carelink.kaipoke-api.net) and Cloudflared's path-based ingress
  // routes /api/v1/* to the backend container. This avoids leaking
  // Docker-internal URLs (http://backend:8000) into the client bundle and
  // sidesteps NEXT_PUBLIC_* build-arg complexity.
  if (typeof window !== 'undefined') {
    return '';
  }
  // Server-side: prefer the docker-network URL.
  return (
    process.env.BACKEND_API_BASE_URL ??
    process.env.NEXT_PUBLIC_BACKEND_API_BASE_URL ??
    'http://localhost:8000'
  );
}

export interface FetcherOptions extends RequestInit {
  accessToken?: string | null;
  refreshToken?: string | null;
  /** Internal: prevents recursive refresh loops. */
  _retried?: boolean;
}

async function refreshAccessToken(
  refreshToken: string,
): Promise<{ accessToken: string; refreshToken: string } | null> {
  try {
    const res = await fetch(`${resolveBaseUrl()}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: 'no-store',
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { access_token?: string; refresh_token?: string };
    if (typeof body.access_token === 'string' && typeof body.refresh_token === 'string') {
      return { accessToken: body.access_token, refreshToken: body.refresh_token };
    }
    return null;
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

  let res: Response;
  try {
    res = await fetch(url, { ...init, headers: h, cache: init.cache ?? 'no-store' });
  } catch (err) {
    // ネットワーク層の失敗。Cloudflare Access の期限切れ (Access ログインへの
    // クロスオリジン 302) でも TypeError になるため、探針で判定してバナー通知する
    // (2026-07-11 handoff §6-3)。throw 自体はそのまま呼び出し側へ。
    if (typeof window !== 'undefined') {
      void reportPossibleAccessIssue();
    }
    throw err;
  }

  if (res.status === 401 && !_retried && refreshToken) {
    const tokens = await refreshAccessToken(refreshToken);
    if (tokens) {
      return fetcher<T>(path, {
        ...init,
        headers,
        accessToken: tokens.accessToken,
        refreshToken: tokens.refreshToken,
        _retried: true,
      });
    }
    if (typeof window !== 'undefined') {
      // Refresh 失敗が Cloudflare Access 起因なら signOut しない — /login への
      // 遷移も Access に弾かれて悪化するだけなので、再ログインバナーへ誘導する。
      if (await checkAccessSession()) {
        // Access は生きている → 純粋にアプリ側のセッション切れ。従来どおり logout。
        void signOut({ callbackUrl: '/login' });
      } else {
        notifyAccessExpired();
      }
    }
  } else if (res.status === 401 && typeof window !== 'undefined') {
    // refresh 経路に乗らない 401 (トークン無し/リトライ後) も Access 切れの
    // 可能性があるため throttle 付き探針に流す (Access 正常時は何もしない)。
    void reportPossibleAccessIssue();
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
