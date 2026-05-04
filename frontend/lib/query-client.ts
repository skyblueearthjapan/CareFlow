/**
 * Shared TanStack Query client.
 *
 * - default staleTime: 30s (matches Phase 2 plan)
 * - retry: 1 (avoid noisy retries during early development)
 * - refetchOnWindowFocus: false (UX preference for ops dashboards)
 *
 * Instantiate per-render via `getQueryClient()` to play nicely with
 * React 19 / Next 15 server-component boundaries; the singleton on the
 * browser side is preserved across HMR / route transitions.
 */
import { QueryClient } from '@tanstack/react-query';

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (typeof window === 'undefined') {
    // Server: always create a fresh client per request.
    return makeQueryClient();
  }
  if (!browserQueryClient) {
    browserQueryClient = makeQueryClient();
  }
  return browserQueryClient;
}
