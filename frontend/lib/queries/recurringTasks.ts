/**
 * TanStack Query hooks for 毎月のやること (繰り返しの設定・管理者だけ).
 *
 *   GET   /api/v1/recurring-task-rules
 *   GET   /api/v1/recurring-task-rules/candidates?categories=...
 *   POST  /api/v1/recurring-task-rules
 *   PATCH /api/v1/recurring-task-rules/{id}
 *   GET   /api/v1/recurring-task-rules/{id}/preview?month=YYYY-MM
 *   POST  /api/v1/recurring-task-rules/{id}/cancel-open
 *
 * 正典 = docs/plans/patient-tasks-recurring-design-2026-11-02.md。
 * 変更系は設定の一覧と、やること (['tasks'] 配下) をまとめて読み直す
 * (保存したときに今月の分を作る・まとめて取り消すとやることが変わるため)。
 */
'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { tasksKeys } from '@/lib/queries/tasks';
import {
  recurringTaskRuleInputSchema,
  type RecurringCandidate,
  type RecurringPreview,
  type RecurringTaskRuleInput,
  type RecurringTaskRuleRead,
} from '@/lib/schemas/recurring-task';

const RULES = '/api/v1/recurring-task-rules';

export const recurringKeys = {
  all: ['recurring-task-rules'] as const,
  list: () => ['recurring-task-rules', 'list'] as const,
  candidates: (categories: string[]) =>
    ['recurring-task-rules', 'candidates', [...categories].sort().join(',')] as const,
  preview: (ruleId: string, month: string) =>
    ['recurring-task-rules', 'preview', ruleId, month] as const,
};

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

export function useRecurringRules(options: { enabled?: boolean } = {}) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  return useQuery<RecurringTaskRuleRead[]>({
    queryKey: recurringKeys.list(),
    queryFn: () => fetcher<RecurringTaskRuleRead[]>(RULES, { accessToken, refreshToken }),
    enabled: isAuthenticated && (options.enabled ?? true),
  });
}

/** 外す方の候補 (区分が合い、終了でない方)。区分が空なら呼ばない。 */
export function useRecurringCandidates(categories: string[]) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  const search = new URLSearchParams();
  categories.forEach((c) => search.append('categories', c));
  return useQuery<RecurringCandidate[]>({
    queryKey: recurringKeys.candidates(categories),
    queryFn: () =>
      fetcher<RecurringCandidate[]>(`${RULES}/candidates?${search.toString()}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated && categories.length > 0,
  });
}

/** その月の試し表示 (作らない)。month = YYYY-MM。 */
export function useRecurringPreview(ruleId: string | null, month: string) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();
  return useQuery<RecurringPreview>({
    queryKey: recurringKeys.preview(ruleId ?? '', month),
    queryFn: () =>
      fetcher<RecurringPreview>(`${RULES}/${ruleId}/preview?month=${month}`, {
        accessToken,
        refreshToken,
      }),
    enabled: isAuthenticated && !!ruleId,
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: recurringKeys.all }),
      qc.invalidateQueries({ queryKey: tasksKeys.all }),
    ]);
}

type SaveVars = { id: string | null; payload: RecurringTaskRuleInput };

/** 作る (id = null)・変える。応答の created_now が、保存したときに今月の分を作った件数。 */
export function useSaveRecurringRule() {
  const invalidate = useInvalidate();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<RecurringTaskRuleRead, Error, SaveVars>({
    mutationFn: ({ id, payload }) =>
      fetcher<RecurringTaskRuleRead>(id ? `${RULES}/${id}` : RULES, {
        method: id ? 'PATCH' : 'POST',
        body: JSON.stringify(recurringTaskRuleInputSchema.parse(payload)),
        accessToken,
        refreshToken,
      }),
    onSuccess: async () => {
      await invalidate();
    },
  });
}

/** この設定から作った未完了の分をまとめて取り消す。 */
export function useCancelRecurringOpen() {
  const invalidate = useInvalidate();
  const { accessToken, refreshToken } = useAuthTokens();
  return useMutation<{ cancelled_count: number }, Error, string>({
    mutationFn: (ruleId) =>
      fetcher<{ cancelled_count: number }>(`${RULES}/${ruleId}/cancel-open`, {
        method: 'POST',
        accessToken,
        refreshToken,
      }),
    onSuccess: async () => {
      await invalidate();
    },
  });
}
