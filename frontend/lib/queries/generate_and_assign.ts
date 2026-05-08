'use client';

/**
 * useGenerateAndAssign — Wave 16 Phase B-3.
 *
 * POST /api/v1/schedule/generate-and-assign
 *
 * 1 トランザクションで:
 *   1. 当該週の auto-source visit を全削除し Layer 1 を再実行 (visits 再生成)
 *   2. proposed コースを course_fixed に昇格 (staff_assigned コースは保護)
 *   3. Layer 3 でスタッフ割付 (courses.assigned_staff_id を確定)
 *   4. visits.primary_staff_id を Course の assigned_staff_id 経由で同期
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 *
 * BE 仕様:
 *   - GenerateAndAssignRequest: { iso_year, iso_week, office_id? }
 *   - GenerateAndAssignResponse: { iso_year, iso_week, visits_created, courses_assigned, message }
 *
 * onSuccess で関連キャッシュ (visits / courses / patient-fixed-visits / patients) を
 * invalidate して、スタッフ別週次テーブル UI に最新の割付状態を反映する。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

// ---------------------------------------------------------------------------
// Path constants
// ---------------------------------------------------------------------------

const GENERATE_AND_ASSIGN_PATH = '/api/v1/schedule/generate-and-assign';

// ---------------------------------------------------------------------------
// Zod schemas — BE GenerateAndAssignRequest / Response とミラー
// ---------------------------------------------------------------------------

export const generateAndAssignRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_id: z.string().uuid().nullable().optional(),
});

export type GenerateAndAssignRequest = z.infer<typeof generateAndAssignRequestSchema>;

export const generateAndAssignResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  visits_created: z.number().int().nonnegative(),
  courses_assigned: z.number().int().nonnegative(),
  message: z.string(),
});

export type GenerateAndAssignResponse = z.infer<typeof generateAndAssignResponseSchema>;

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
 * POST /api/v1/schedule/generate-and-assign を呼ぶ TanStack mutation.
 *
 * 呼出側 (StaffWeekTablePanel) は
 *   - admin / manager のときのみ「週を生成」ボタンを描画
 *   - mutateAsync 完了後に toast.success / 失敗で toast.error を投げる
 * 想定で利用する。
 */
export function useGenerateAndAssign(): UseMutationResult<
  GenerateAndAssignResponse,
  Error,
  GenerateAndAssignRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<GenerateAndAssignResponse, Error, GenerateAndAssignRequest>({
    mutationFn: async (raw) => {
      // FE で軽くバリデーション (型ずれの早期検出).
      const payload = generateAndAssignRequestSchema.parse(raw);
      // BE は extra="forbid" のため不要キーを送らない。
      const body: Record<string, unknown> = {
        iso_year: payload.iso_year,
        iso_week: payload.iso_week,
      };
      if (payload.office_id) body.office_id = payload.office_id;
      return fetcher<GenerateAndAssignResponse>(GENERATE_AND_ASSIGN_PATH, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      // 当該週の visits / courses は全面的に置き換わるため広めに invalidate。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
    },
  });
}
