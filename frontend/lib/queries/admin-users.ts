/**
 * TanStack Query hooks for `/api/v1/admin/users` (Wave 4-F).
 *
 * Mirrors `backend/app/api/v1/admin.py`:
 *   GET    /api/v1/admin/users
 *   POST   /api/v1/admin/users
 *   PATCH  /api/v1/admin/users/:id
 *   DELETE /api/v1/admin/users/:id
 *   POST   /api/v1/admin/users/:id/reset-password
 *
 * All endpoints require the `admin` role; the page-level guard happens in
 * `frontend/app/(app)/admin/users/page.tsx`.
 */
'use client';

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import type {
  AdminPasswordResetResponse,
  AdminUserCreate,
  AdminUserCreateResponse,
  AdminUserRead,
  AdminUserUpdate,
} from '@/lib/schemas/admin-user';

const BASE = '/api/v1/admin/users';
const adminUsersKey = ['admin-users'] as const;

export interface AdminUsersPage {
  items: AdminUserRead[];
  total: number;
  limit: number;
  offset: number;
}

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
    role: session?.user?.role,
  };
}

export interface UseAdminUsersParams {
  role?: 'admin' | 'manager' | 'staff';
  q?: string;
  includeDeleted?: boolean;
  limit?: number;
  offset?: number;
}

export function useAdminUsers(params: UseAdminUsersParams = {}) {
  const { accessToken, refreshToken, isAuthenticated, role } = useAuthTokens();
  const { role: roleFilter, q, includeDeleted = false, limit = 100, offset = 0 } = params;

  return useQuery<AdminUsersPage>({
    queryKey: [...adminUsersKey, 'list', { roleFilter, q, includeDeleted, limit, offset }],
    enabled: isAuthenticated && role === 'admin',
    queryFn: () => {
      const usp = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (roleFilter) usp.set('role', roleFilter);
      if (q) usp.set('q', q);
      if (includeDeleted) usp.set('include_deleted', 'true');
      return fetcher<AdminUsersPage>(`${BASE}?${usp.toString()}`, {
        accessToken,
        refreshToken,
      });
    },
  });
}

type CreateOpts = UseMutationOptions<AdminUserCreateResponse, Error, AdminUserCreate>;

export function useCreateAdminUser(options: CreateOpts = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<AdminUserCreateResponse, Error, AdminUserCreate>({
    mutationFn: (payload) =>
      fetcher<AdminUserCreateResponse>(BASE, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: adminUsersKey });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

interface UpdateVars {
  id: string;
  payload: AdminUserUpdate;
}
type UpdateOpts = UseMutationOptions<AdminUserRead, Error, UpdateVars>;

export function useUpdateAdminUser(options: UpdateOpts = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<AdminUserRead, Error, UpdateVars>({
    mutationFn: ({ id, payload }) =>
      fetcher<AdminUserRead>(`${BASE}/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: adminUsersKey });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type DeleteOpts = UseMutationOptions<void, Error, string>;

export function useDeleteAdminUser(options: DeleteOpts = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await fetcher<void>(`${BASE}/${id}`, {
        method: 'DELETE',
        accessToken,
        refreshToken,
      });
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: adminUsersKey });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}

type ResetOpts = UseMutationOptions<AdminPasswordResetResponse, Error, string>;

export function useResetAdminUserPassword(options: ResetOpts = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<AdminPasswordResetResponse, Error, string>({
    mutationFn: (id) =>
      fetcher<AdminPasswordResetResponse>(`${BASE}/${id}/reset-password`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: adminUsersKey });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
