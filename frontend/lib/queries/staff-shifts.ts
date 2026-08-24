/**
 * TanStack Query hooks for the staff weekly-shift endpoints.
 *
 *   GET /api/v1/staff/{staff_id}/shifts        -> ShiftsResponse
 *   PUT /api/v1/staff/{staff_id}/shifts        -> ShiftsResponse  (bulk; 7 rows)
 *
 * Cache key: ['staff-shifts', staffId]
 * Mirrors `lib/queries/staff.ts` patterns.
 */
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseMutationOptions,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { type ShiftsResponse, type StaffShiftItem } from '@/lib/schemas/staff-shifts';

const STAFF_BASE = '/api/v1/staff';

export const staffShiftsKey = (staffId: string) => ['staff-shifts', staffId] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useStaffShifts(staffId: string | null | undefined) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const normalizedId = staffId ?? '__none__';

  return useQuery<ShiftsResponse>({
    queryKey: staffShiftsKey(normalizedId),
    queryFn: () => {
      if (!staffId) throw new Error('staff id is required');
      return fetcher<ShiftsResponse>(`${STAFF_BASE}/${staffId}/shifts`, {
        accessToken,
        refreshToken,
      });
    },
    enabled: isAuthenticated && !!staffId,
  });
}

/**
 * 複数スタッフの週間シフトを並列取得する
 * (固定イベント一括登録の「☀ 9:00出勤の全員を選択」用 —
 *  staff-event-history-design.md §2 Phase 3)。
 *
 * BE に一括取得 API が無いため useQueries でまとめる。対象は事業所の active
 * スタッフ (数名〜十数名) 規模を想定。キャッシュキーは `useStaffShifts` と
 * 共通なので、詳細ページで開いた分はそのまま再利用される。
 */
export function useManyStaffShifts(staffIds: readonly string[]) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  const results = useQueries({
    queries: staffIds.map((id) => ({
      queryKey: staffShiftsKey(id),
      queryFn: () =>
        fetcher<ShiftsResponse>(`${STAFF_BASE}/${id}/shifts`, { accessToken, refreshToken }),
      enabled: isAuthenticated,
      staleTime: 5 * 60 * 1000,
    })),
  });

  const byStaffId = new Map<string, StaffShiftItem[]>();
  staffIds.forEach((id, i) => {
    const shifts = results[i]?.data?.shifts;
    if (shifts) byStaffId.set(id, shifts);
  });

  return { byStaffId, isLoading: results.some((r) => r.isLoading) };
}

interface UpdateShiftsContext {
  previous: ShiftsResponse | undefined;
}

type UpdateShiftsOptions = UseMutationOptions<
  ShiftsResponse,
  Error,
  StaffShiftItem[],
  UpdateShiftsContext
>;

/**
 * Bulk PUT — 7 weekday rows in one round-trip. Uses an optimistic update
 * so the dialog feels instant; on error we roll back to the snapshot.
 */
export function useUpdateStaffShifts(staffId: string, options: UpdateShiftsOptions = {}) {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();
  const key = staffShiftsKey(staffId);

  return useMutation<ShiftsResponse, Error, StaffShiftItem[], UpdateShiftsContext>({
    mutationFn: (shifts) =>
      fetcher<ShiftsResponse>(`${STAFF_BASE}/${staffId}/shifts`, {
        method: 'PUT',
        body: JSON.stringify({ shifts }),
        accessToken,
        refreshToken,
      }),
    onMutate: async (shifts) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<ShiftsResponse>(key);
      qc.setQueryData<ShiftsResponse>(key, { shifts });
      return { previous };
    },
    onError: (err, vars, context, mutationCtx) => {
      if (context?.previous) {
        qc.setQueryData(key, context.previous);
      }
      options.onError?.(err, vars, context, mutationCtx);
    },
    ...options,
    onSuccess: (data, variables, onMutateResult, context) => {
      void qc.invalidateQueries({ queryKey: key });
      options.onSuccess?.(data, variables, onMutateResult, context);
    },
  });
}
