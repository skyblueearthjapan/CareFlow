'use client';

/**
 * useGenerateWeek — Wave 17 Phase B-3 (Layer 1 のみ).
 *
 * POST /api/v1/schedule/generate-week-only  (W17-A の generate-week-only endpoint を呼ぶ)
 *
 * 1 トランザクションで:
 *   1. 当該週の auto-source visit を全削除
 *   2. patient_fixed_visits / weekly_pattern からその週の visits を再構築
 *
 * Wave 17 では Layer 3 (スタッフ割付) は本フックでは行わず、
 * `useAssignStaffOnly` を別途呼ぶ二段階フローに分離した:
 *   - 「週を生成」 = Layer 1 (visits 再構築のみ)
 *   - 「自動割付」 = Layer 3 (スタッフ自動割付)
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 *
 * BE 仕様 (Phase A 並行実装):
 *   - GenerateWeekRequest: { iso_year, iso_week, office_id? }
 *   - GenerateWeekResponse: { iso_year, iso_week, visits_created, message }
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const GENERATE_WEEK_PATH = '/api/v1/schedule/generate-week-only';

// ---------------------------------------------------------------------------
// Zod schemas — BE GenerateWeekRequest / Response とミラー
// ---------------------------------------------------------------------------

export const generateWeekRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  office_id: z.string().uuid().nullable().optional(),
});

export type GenerateWeekRequest = z.infer<typeof generateWeekRequestSchema>;

export const generateWeekResponseSchema = z.object({
  iso_year: z.number().int(),
  iso_week: z.number().int(),
  visits_created: z.number().int().nonnegative(),
  message: z.string(),
});

export type GenerateWeekResponse = z.infer<typeof generateWeekResponseSchema>;

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
 * POST /api/v1/schedule/generate-week-only — Layer 1 のみを実行する mutation.
 *
 * 呼出側 (CourseDayTablePanel) は
 *   - admin / manager のときのみ「週を生成」ボタンを描画
 *   - mutateAsync 完了後に toast.success / 失敗で toast.error を投げる
 * 想定で利用する。
 */
export function useGenerateWeek(): UseMutationResult<
  GenerateWeekResponse,
  Error,
  GenerateWeekRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<GenerateWeekResponse, Error, GenerateWeekRequest>({
    mutationFn: async (raw) => {
      const payload = generateWeekRequestSchema.parse(raw);
      // BE は extra="forbid" のため不要キーを送らない。
      const body: Record<string, unknown> = {
        iso_year: payload.iso_year,
        iso_week: payload.iso_week,
      };
      if (payload.office_id) body.office_id = payload.office_id;
      return fetcher<GenerateWeekResponse>(GENERATE_WEEK_PATH, {
        method: 'POST',
        body: JSON.stringify(body),
        accessToken,
        refreshToken,
      });
    },
    onSuccess: () => {
      // 当該週の visits は全面置換される。courses は (proposed) 行のみ可能性あり。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
    },
  });
}
