'use client';

/**
 * useCourseMoveWeekdayWeekOnly — 週空間 A2後段 (weekly-space-design.md §4-2) の
 * 「コース丸ごと曜日スライド」フック。
 *
 *   POST /api/v1/schedule/v2/course-move-weekday-week-only
 *
 * course.weekday + 配下 planned visits の visit_date を今週限りで動かす
 * (source='manual_week' = 週生成・固定枠戻で保護)。担当は動かさない。PFV 不変。
 * 422: 移動先に同 code コース既存 / 青ピン配下。undo は op_log 対応済み。
 * RBAC: admin (BE 側で 403 担保)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const COURSE_MOVE_WEEKDAY_PATH = '/api/v1/schedule/v2/course-move-weekday-week-only';

export const courseMoveWeekdayRequestSchema = z.object({
  course_id: z.string().uuid(),
  to_weekday: z.number().int().min(0).max(5),
  /** Wave U-3: 1 ユーザー操作 = 1 UUID (undo グループ化)。省略可。 */
  op_group_id: z.string().uuid().nullish(),
});
export type CourseMoveWeekdayRequest = z.input<typeof courseMoveWeekdayRequestSchema>;

export const courseMoveWeekdayResponseSchema = z.object({
  course_id: z.string().uuid(),
  from_weekday: z.number().int(),
  to_weekday: z.number().int(),
  visits_moved: z.number().int().min(0).default(0).catch(0),
  warnings: z.array(z.string()).default([]).catch([]),
});
export type CourseMoveWeekdayResponse = z.infer<typeof courseMoveWeekdayResponseSchema>;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** POST /api/v1/schedule/v2/course-move-weekday-week-only — コースを別曜日へ。 */
export function useCourseMoveWeekdayWeekOnly(): UseMutationResult<
  CourseMoveWeekdayResponse,
  Error,
  CourseMoveWeekdayRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<CourseMoveWeekdayResponse, Error, CourseMoveWeekdayRequest>({
    mutationFn: async (raw) => {
      const payload = courseMoveWeekdayRequestSchema.parse(raw);
      const result = await fetcher<unknown>(COURSE_MOVE_WEEKDAY_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return courseMoveWeekdayResponseSchema.parse(result);
    },
    onSuccess: () => {
      // courses.weekday と visits.visit_date が変わる。PFV は不変。
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
