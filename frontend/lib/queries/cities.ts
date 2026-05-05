/**
 * TanStack Query hooks for /api/v1/cities — Phase 3-13.
 */
'use client';

import { useQuery } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type { City } from '@/lib/schemas/city';

type CitiesResponse = City[] | { items?: City[] };

function normalizeCities(data: CitiesResponse | undefined): City[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}

export interface UseCitiesParams {
  /** Substring filter passed as `q` to the server (name/prefecture ILIKE). */
  search?: string;
  /** Exact prefecture filter forwarded to the server. */
  prefecture?: string;
  limit?: number;
  offset?: number;
}

export function useCities(params: UseCitiesParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const limit = params.limit ?? 2000;
  const offset = params.offset ?? 0;
  const search = params.search?.trim() || undefined;
  const prefecture = params.prefecture?.trim() || undefined;

  const query = useQuery<CitiesResponse>({
    queryKey: ['cities', limit, offset, search ?? null, prefecture ?? null],
    queryFn: () => {
      const usp = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (search) usp.set('q', search);
      if (prefecture) usp.set('prefecture', prefecture);
      return fetcher<CitiesResponse>(`/api/v1/cities?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: status === 'authenticated',
  });

  const all = normalizeCities(query.data);
  return { ...query, cities: all, allCities: all };
}
