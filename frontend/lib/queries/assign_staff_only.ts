'use client';

/**
 * useAssignStaffOnly — Wave 17 Phase B-3 (Layer 3 のみ).
 *
 * POST /api/v1/schedule/assign-staff-only
 *
 * 当該週の course (course_fixed) に対してスタッフ自動割付を実行する。
 * Layer 1 (visits 再構築) は事前に `useGenerateWeekOnly` で済ませてある前提。
 *
 * Wave 16 までの `generate-and-assign` (1 ボタン) を分離したうちの後半。
 * UI は「週を生成」「自動割付」の 2 ボタンに分かれている (Excel 準拠)。
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 *
 * BE 仕様 (Phase A 並行実装):
 *   - AssignStaffOnlyRequest: { iso_year, iso_week, office_id? }
 *   - AssignStaffOnlyResponse: { iso_year, iso_week, courses_assigned, message }
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const ASSIGN_STAFF_ONLY_PATH = '/api/v1/schedule/assign-staff-only';

// ---------------------------------------------------------------------------
// Zod schemas — BE AssignStaffOnlyRequest / Response とミラー
// ---------------------------------------------------------------------------

export const assignStaffOnlyRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_id: z.string().uuid().nullable().optional(),
});

export type AssignStaffOnlyRequest = z.infer<typeof assignStaffOnlyRequestSchema>;

export const assignStaffOnlyResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  courses_assigned: z.number().int().nonnegative(),
  message: z.string(),
});

export type AssignStaffOnlyResponse = z.infer<typeof assignStaffOnlyResponseSchema>;

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * POST /api/v1/schedule/assign-staff-only — Layer 3 のみを実行する mutation.
 *
 * onSuccess で courses / visits を invalidate し、
 * 担当スタッフ dropdown / コーステーブル下のヘッダーが最新化される。
 */
export function useAssignStaffOnly(): UseMutationResult<
  AssignStaffOnlyResponse,
  Error,
  AssignStaffOnlyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<AssignStaffOnlyResponse, Error, AssignStaffOnlyRequest>({
    mutationFn: async (raw) => {
      const payload = assignStaffOnlyRequestSchema.parse(raw);
      const body: Record<string, unknown> = {
        iso_year: payload.iso_year,
        iso_week: payload.iso_week,
      };
      if (payload.office_id) body.office_id = payload.office_id;
      return fetcher<AssignStaffOnlyResponse>(ASSIGN_STAFF_ONLY_PATH, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      // courses は assigned_staff_id が変わるので必ず再取得
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // visits は primary_staff_id が同期される可能性
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
