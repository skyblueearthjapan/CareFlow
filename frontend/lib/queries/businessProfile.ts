/**
 * TanStack Query hooks for /api/v1/business-profile — 事業所の情報 (mig 0089)。
 *
 *   GET /api/v1/business-profile → 5 項目 (未設定は null) (全ログイン可)
 *   PUT /api/v1/business-profile → 部分更新 (admin)
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  businessProfileSchema,
  type BusinessProfile,
  type BusinessProfileUpdate,
} from '@/lib/schemas/businessProfile';

const BUSINESS_PROFILE_KEY = ['business-profile'] as const;
const BUSINESS_PROFILE_PATH = '/api/v1/business-profile';

/** GET /api/v1/business-profile — 事業所の情報。 */
export function useBusinessProfile() {
  const { data: session, status } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useQuery<BusinessProfile>({
    queryKey: BUSINESS_PROFILE_KEY,
    queryFn: async () => {
      const raw = await fetcher<unknown>(BUSINESS_PROFILE_PATH, { accessToken, refreshToken });
      return businessProfileSchema.parse(raw);
    },
    enabled: status === 'authenticated',
    // 編集中の入力をウィンドウ再フォーカスの取り直しで上書きしない。
    refetchOnWindowFocus: false,
  });
}

/** PUT /api/v1/business-profile — 部分更新 (省略=不変 / null=未設定に戻す)。 */
export function useUpdateBusinessProfile() {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;
  const qc = useQueryClient();

  return useMutation<BusinessProfile, Error, BusinessProfileUpdate>({
    mutationFn: async (payload) => {
      const raw = await fetcher<unknown>(BUSINESS_PROFILE_PATH, {
        method: 'PUT',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return businessProfileSchema.parse(raw);
    },
    onSuccess: (data) => {
      qc.setQueryData(BUSINESS_PROFILE_KEY, data);
    },
  });
}
