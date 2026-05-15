'use client';

/**
 * useAutoAllocate / useApplyProposal / useDiscardProposal — Wave 41 Phase 5.
 *
 * 自動スケジュール算出 (auto-allocate) 3 endpoint の TanStack Query mutation hook 群:
 *   - POST /api/v1/schedule/auto-allocate                       → 提案バッチを生成
 *   - POST /api/v1/schedule/proposal/{batch_id}/apply           → 提案を採用 (commit)
 *   - POST /api/v1/schedule/proposal/{batch_id}/discard         → 提案を破棄 (delete)
 *
 * BE schema 仕様: ``backend/app/schemas/v2/auto_allocate.py`` と完全一致。
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  autoAllocateRequestSchema,
  autoAllocateResponseSchema,
  proposalApplyResponseSchema,
  proposalDiscardResponseSchema,
  type AutoAllocateRequest,
  type AutoAllocateResponse,
  type ProposalApplyResponse,
  type ProposalDiscardResponse,
} from '@/lib/schemas/v2/autoAllocate';

const AUTO_ALLOCATE_PATH = '/api/v1/schedule/auto-allocate';
const PROPOSAL_BASE_PATH = '/api/v1/schedule/proposal';

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * POST /api/v1/schedule/auto-allocate — 自動算出を実行して提案バッチを生成する。
 *
 * 本フックは BE の DB を即時更新しない (= proposed 状態の course/visit を作るのみ)。
 * 採用は ``useApplyProposal``、破棄は ``useDiscardProposal`` を別途呼ぶ。
 *
 * Usage:
 * ```tsx
 * const mut = useAutoAllocate();
 * const res = await mut.mutateAsync({
 *   iso_year: 2026,
 *   iso_week: 20,
 *   office_ids: [],            // 空 = 全拠点
 *   mode: 'mode_1',
 * });
 * // res.proposal_batch_id / res.proposals / res.kpi / res.warnings
 * ```
 */
export function useAutoAllocate(): UseMutationResult<
  AutoAllocateResponse,
  Error,
  AutoAllocateRequest
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<AutoAllocateResponse, Error, AutoAllocateRequest>({
    mutationFn: async (raw) => {
      const payload = autoAllocateRequestSchema.parse(raw);
      const result = await fetcher<unknown>(AUTO_ALLOCATE_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return autoAllocateResponseSchema.parse(result);
    },
    // 提案段階では UI cache は invalidate しない (採用時に invalidate する).
  });
}

/**
 * POST /api/v1/schedule/proposal/{batch_id}/apply — 提案を採用 (commit) する。
 *
 * proposed → course_fixed → staff_assigned に遷移させる。
 * 採用後は courses / visits の cache を invalidate して UI を更新する。
 */
export function useApplyProposal(): UseMutationResult<ProposalApplyResponse, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ProposalApplyResponse, Error, string>({
    mutationFn: async (proposalBatchId) => {
      const result = await fetcher<unknown>(
        `${PROPOSAL_BASE_PATH}/${encodeURIComponent(proposalBatchId)}/apply`,
        {
          method: 'POST',
          accessToken,
          refreshToken,
        },
      );
      return proposalApplyResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}

/**
 * POST /api/v1/schedule/proposal/{batch_id}/discard — 提案を破棄 (delete) する。
 *
 * proposed 状態の course / visit を物理削除する。
 * 破棄後は courses / visits の cache を invalidate する (proposed が消えるため)。
 */
export function useDiscardProposal(): UseMutationResult<ProposalDiscardResponse, Error, string> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ProposalDiscardResponse, Error, string>({
    mutationFn: async (proposalBatchId) => {
      const result = await fetcher<unknown>(
        `${PROPOSAL_BASE_PATH}/${encodeURIComponent(proposalBatchId)}/discard`,
        {
          method: 'POST',
          accessToken,
          refreshToken,
        },
      );
      return proposalDiscardResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}
