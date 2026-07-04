'use client';

/**
 * W-12d 詰まり解消相談 (propose-unblock) mutation hooks。
 *
 *   POST /api/v1/schedule/v2/propose-unblock       (read-only 探索)
 *   POST /api/v1/schedule/v2/propose-unblock/apply  (1 TX 適用)
 *
 * BE 仕様: `docs/plans/unblock-consult-design.md` §2.2 (契約は
 * `lib/schemas/v2/unblock.ts` と 1:1)。RBAC: admin / manager (BE 側で担保)。
 *
 * 探索 (propose-unblock) は POST だが read-only の計算トリガーなので useMutation
 * で表現する (ボタン押下のたびに最新状態で再探索。キャッシュ再利用はしない)。
 * apply 成功後は週スケジュール (visits / courses)・患者一覧 (プール判定)・固定枠
 * (patient-fixed-visits)・操作ログ状態を invalidate する (poolBulk の invalidate
 * 集合を踏襲)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { OP_LOG_STATE_KEY } from '@/lib/queries/opLog';
import {
  proposeUnblockRequestSchema,
  proposeUnblockResponseSchema,
  unblockApplyRequestSchema,
  unblockApplyResponseSchema,
  type ProposeUnblockRequest,
  type ProposeUnblockResponse,
  type UnblockApplyRequest,
  type UnblockApplyResponse,
} from '@/lib/schemas/v2/unblock';

const UNBLOCK_BASE = '/api/v1/schedule/v2';

/** POST /api/v1/schedule/v2/propose-unblock (read-only 探索)。 */
export function useProposeUnblockMutation(): UseMutationResult<
  ProposeUnblockResponse,
  Error,
  ProposeUnblockRequest
> {
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<ProposeUnblockResponse, Error, ProposeUnblockRequest>({
    mutationFn: async (raw) => {
      const payload = proposeUnblockRequestSchema.parse(raw);
      const result = await fetcher<unknown>(`${UNBLOCK_BASE}/propose-unblock`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return proposeUnblockResponseSchema.parse(result);
    },
  });
}

/**
 * POST /api/v1/schedule/v2/propose-unblock/apply (1 TX 適用)。
 * plan の moves を逐次適用し対象患者を配置する (pattern_and_week 固定)。成功後は
 * 週スケジュール・プール・固定枠・操作ログ状態のキャッシュを invalidate する。
 * state_token 不一致は BE が 409 を返す (FE は再探索導線を出す)。
 */
export function useUnblockApplyMutation(): UseMutationResult<
  UnblockApplyResponse,
  Error,
  UnblockApplyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const accessToken = session?.accessToken ?? null;
  const refreshToken = session?.refreshToken ?? null;

  return useMutation<UnblockApplyResponse, Error, UnblockApplyRequest>({
    mutationFn: async (raw) => {
      const payload = unblockApplyRequestSchema.parse(raw);
      const result = await fetcher<unknown>(`${UNBLOCK_BASE}/propose-unblock/apply`, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return unblockApplyResponseSchema.parse(result);
    },
    onSuccess: (_data, variables) => {
      // 週スケジュール (実 visit / コース) — 移動 + 配置で当週が再生成される。
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // 患者一覧 (プール判定 / shortage 再計算)。
      void qc.invalidateQueries({ queryKey: ['patients'] });
      // 固定枠 (pattern_and_week で移動患者・対象患者の PFV が変わる)。
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
      // 操作ログ状態 (undoable=false だが監査サマリが記録される)。
      void qc.invalidateQueries({
        queryKey: [OP_LOG_STATE_KEY, variables.iso_year, variables.iso_week],
      });
    },
  });
}
