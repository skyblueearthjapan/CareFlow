/**
 * TanStack Query hooks for /api/v1/offices — Phase 3-12.
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  Office,
  OfficeCreate,
  OfficeUpdate,
  OfficeResolveResponse,
} from '@/lib/schemas/office';
import { officeResolveResponseSchema } from '@/lib/schemas/office';

type OfficesResponse = Office[] | { items?: Office[] };

function normalizeOffices(data: OfficesResponse | undefined): Office[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return Array.isArray(data.items) ? data.items : [];
}

export interface UseOfficesParams {
  search?: string;
  limit?: number;
  offset?: number;
}

export function useOffices(params: UseOfficesParams = {}) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  const limit = params.limit ?? 100;
  const offset = params.offset ?? 0;
  const search = params.search?.trim() || undefined;

  const query = useQuery<OfficesResponse>({
    queryKey: ['offices', limit, offset, search ?? null],
    queryFn: () => {
      const usp = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (search) usp.set('q', search);
      return fetcher<OfficesResponse>(`/api/v1/offices?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: status === 'authenticated',
  });

  const all = normalizeOffices(query.data);
  return { ...query, offices: all, allOffices: all };
}

export function useOffice(id: string | undefined) {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<Office>({
    queryKey: ['offices', 'detail', id],
    queryFn: () => fetcher<Office>(`/api/v1/offices/${id}`, { accessToken, refreshToken }),
    enabled: status === 'authenticated' && Boolean(id),
  });
}

export function useCreateOffice() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<Office, Error, OfficeCreate>({
    mutationFn: (payload) =>
      fetcher<Office>('/api/v1/offices', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['offices'] });
    },
  });
}

export function useUpdateOffice(id: string) {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<Office, Error, OfficeUpdate>({
    mutationFn: (payload) =>
      fetcher<Office>(`/api/v1/offices/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['offices'] });
    },
  });
}

export function useDeleteOffice() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (id) =>
      fetcher<void>(`/api/v1/offices/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['offices'] });
    },
  });
}

/**
 * POST /api/v1/offices/resolve — W12-FE.
 *
 * 住所文字列から主担当拠点を自動判定する。
 * 失敗時は silent (best-effort)。呼び出し側でエラーを握りつぶす。
 */
export function useResolveOffice() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<OfficeResolveResponse, Error, string>({
    mutationFn: async (address: string): Promise<OfficeResolveResponse> => {
      const raw = await fetcher<unknown>('/api/v1/offices/resolve', {
        method: 'POST',
        body: JSON.stringify({ address }),
        accessToken,
        refreshToken,
      });
      return officeResolveResponseSchema.parse(raw);
    },
    // 失敗時は toast 出さず silent (ユーザー手動選択にフォールバック)
  });
}

/** POST /api/v1/offices/{office_id}/area-cities レスポンス — W-7. */
export interface OfficeAreaCityResult {
  office_id: string;
  city_id: string;
  city_name: string;
}

/**
 * POST /api/v1/offices/{office_id}/area-cities — W-7 (地域ルールの学習).
 *
 * office_cities に 1 件だけ追加する additive API。冪等 (既存なら 200)。
 * 拠点フォーム全体を触らせずに患者登録の途中で担当地域を学習させる。
 */
export function useAddOfficeAreaCity() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<OfficeAreaCityResult, Error, { officeId: string; cityId: string }>({
    mutationFn: ({ officeId, cityId }) =>
      fetcher<OfficeAreaCityResult>(`/api/v1/offices/${officeId}/area-cities`, {
        method: 'POST',
        body: JSON.stringify({ city_id: cityId }),
        accessToken,
        refreshToken,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['offices'] });
    },
  });
}

/**
 * POST /api/v1/offices/area-prompt-dismissals — W-7 (地域ルールの学習).
 *
 * その City について「今回だけ」= 二度と呼びかけない、を組織全体で記憶する。
 * 冪等 (既に却下済みでも 204)。
 */
export function useDismissAreaPrompt() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<void, Error, string>({
    mutationFn: (cityId: string) =>
      fetcher<void>('/api/v1/offices/area-prompt-dismissals', {
        method: 'POST',
        body: JSON.stringify({ city_id: cityId }),
        accessToken,
        refreshToken,
      }),
  });
}
