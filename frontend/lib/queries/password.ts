/**
 * Wave 40 — TanStack Query hook for `POST /api/v1/auth/change-password`.
 */
'use client';

import { useMutation, type UseMutationOptions } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';

export interface ChangePasswordPayload {
  current_password: string;
  new_password: string;
}

type Opts = UseMutationOptions<void, Error, ChangePasswordPayload>;

export function useChangePassword(options: Opts = {}) {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<void, Error, ChangePasswordPayload>({
    mutationFn: async (payload) => {
      await fetcher<void>('/api/v1/auth/change-password', {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
    },
    ...options,
  });
}
