'use client';

/**
 * useVisitAssignStaffWeek — 週空間 A2 (weekly-space-design.md §4-2) の
 * 「患者個別の貼り替え」フック。
 *
 *   POST /api/v1/schedule/v2/visit-assign-staff-week
 *
 * 訪問 1 件だけの担当を今週限りで付け替える (staff_id=null で担当解除)。
 * BE は primary_staff_id + manual_staff_override + VSA を 3 点セットで書き、
 * コース担当 (courses.assigned_staff_id) と PFV には触れない。
 * NG/性別は 422 `constraint_confirmation_required` → acknowledge 再送
 * (useConstraintConfirmRetry と組み合わせる)。undo は op_log 対応済み。
 * RBAC: admin (BE 側で 403 担保)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const VISIT_ASSIGN_STAFF_WEEK_PATH = '/api/v1/schedule/v2/visit-assign-staff-week';

export const visitAssignStaffWeekRequestSchema = z.object({
  visit_id: z.string().uuid(),
  /** 新しい担当。null = この訪問だけ担当解除 (未割当へ)。 */
  staff_id: z.string().uuid().nullable(),
  /** NG/性別 422 を確認ダイアログで通したときだけ true で再送。 */
  acknowledge_constraint_warnings: z.boolean().optional(),
  /** Wave U-3: 1 ユーザー操作 = 1 UUID (undo グループ化)。省略可。 */
  op_group_id: z.string().uuid().nullish(),
});
export type VisitAssignStaffWeekRequest = z.input<typeof visitAssignStaffWeekRequestSchema>;

export const visitAssignStaffWeekResponseSchema = z.object({
  visit_id: z.string().uuid(),
  staff_id: z.string().uuid().nullable(),
  changed: z.boolean().default(true).catch(true),
});
export type VisitAssignStaffWeekResponse = z.infer<typeof visitAssignStaffWeekResponseSchema>;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** POST /api/v1/schedule/v2/visit-assign-staff-week — 訪問 1 件の担当を今週だけ変更。 */
export function useVisitAssignStaffWeek(): UseMutationResult<
  VisitAssignStaffWeekResponse,
  Error,
  VisitAssignStaffWeekRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitAssignStaffWeekResponse, Error, VisitAssignStaffWeekRequest>({
    mutationFn: async (raw) => {
      const payload = visitAssignStaffWeekRequestSchema.parse(raw);
      const result = await fetcher<unknown>(VISIT_ASSIGN_STAFF_WEEK_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return visitAssignStaffWeekResponseSchema.parse(result);
    },
    onSuccess: () => {
      // コース担当・PFV は不変。今週の visits (primary/VSA) だけ変わる。
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
