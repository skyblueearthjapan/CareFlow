'use client';

/**
 * useVisitMoveWeekOnly — Wave U-2 (変更反映先統一) の「この週だけ移動」フック。
 *
 *   POST /api/v1/schedule/v2/visit-move-week-only
 *
 * 改善提案の move 採用を **B (この週だけ)** で適用するための専用エンドポイント。
 * 対象患者の今週 visit を (old_weekday, old_start_time) → (new_weekday, new_start_time)
 * へ移す。型 (PFV) は変更しない。pinned 一致は BE 側 422 でブロックされる。
 *
 * BE は並行実装中。契約 (下記 schema) を正として寛容パースで先行ミラーする。
 * RBAC: admin / manager (BE 側で 403 担保)。
 *
 * 成功後は visits キャッシュを invalidate して親機テーブルへ即時反映する
 * (PFV は不変なので固定枠キャッシュは触らない)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { z } from 'zod';

import { fetcher } from '@/lib/api/fetcher';

const VISIT_MOVE_WEEK_ONLY_PATH = '/api/v1/schedule/v2/visit-move-week-only';

const timeStringSchema = z
  .string()
  .regex(/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/, '時刻は HH:MM 形式');

export const visitMoveWeekOnlyRequestSchema = z.object({
  iso_year: z.number().int().min(2000).max(2100),
  iso_week: z.number().int().min(1).max(53),
  patient_id: z.string().uuid(),
  old_weekday: z.number().int().min(0).max(6),
  old_start_time: timeStringSchema,
  new_weekday: z.number().int().min(0).max(6),
  new_start_time: timeStringSchema,
  /** 移動先コースの template id。省略時は BE 側で解決 (同拠点・容量から)。 */
  new_course_template_id: z.string().uuid().nullable().optional(),
  /**
   * Wave U-3: 1 ユーザー操作 = 1 UUID。同一操作の複数 API コールに同値を送り
   * BE 側で op-log をグループ化する。省略可 (旧 BE 互換)。
   */
  op_group_id: z.string().uuid().nullish(),
  /**
   * NG スタッフ / 性別制限 (§7-2): 422 `constraint_confirmation_required` を
   * 確認ダイアログで通したときだけ true で再送する。省略時 BE default=false。
   */
  acknowledge_constraint_warnings: z.boolean().optional(),
});
export type VisitMoveWeekOnlyRequest = z.input<typeof visitMoveWeekOnlyRequestSchema>;

/** レスポンス。visits_moved 欠落は寛容に 0 扱い。 */
export const visitMoveWeekOnlyResponseSchema = z.object({
  visits_moved: z.number().int().min(0).default(0).catch(0),
});
export type VisitMoveWeekOnlyResponse = z.infer<typeof visitMoveWeekOnlyResponseSchema>;

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/** POST /api/v1/schedule/v2/visit-move-week-only — この週だけ visit を移動する。 */
export function useVisitMoveWeekOnly(): UseMutationResult<
  VisitMoveWeekOnlyResponse,
  Error,
  VisitMoveWeekOnlyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<VisitMoveWeekOnlyResponse, Error, VisitMoveWeekOnlyRequest>({
    mutationFn: async (raw) => {
      const payload = visitMoveWeekOnlyRequestSchema.parse(raw);
      const result = await fetcher<unknown>(VISIT_MOVE_WEEK_ONLY_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return visitMoveWeekOnlyResponseSchema.parse(result);
    },
    onSuccess: () => {
      // PFV は不変。今週の visits のみ変わるため visits キャッシュだけ失効。
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
