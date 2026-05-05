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
  /** Substring filter applied client-side over name + prefecture. */
  search?: string;
  prefecture?: string;
  limit?: number;
  offset?: number;
}

export function useCities(params: UseCitiesParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const limit = params.limit ?? 500;
  const offset = params.offset ?? 0;

  const query = useQuery<CitiesResponse>({
    queryKey: ['cities', limit, offset],
    queryFn: () =>
      fetcher<CitiesResponse>(
        `/api/v1/cities?limit=${limit}&offset=${offset}`,
        { accessToken, refreshToken },
      ),
    enabled: status === 'authenticated',
  });

  const all = normalizeCities(query.data);
  const filtered = all.filter((city) => {
    if (params.prefecture && city.prefecture !== params.prefecture) return false;
    if (params.search) {
      const q = params.search.toLowerCase();
      const haystack = `${city.prefecture}${city.name}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  return { ...query, cities: filtered, allCities: all };
}
