'use client';

/**
 * Patient sync — TanStack Query mutation hook for 今週 visits → 固定枠 反映.
 *
 *   POST /api/v1/patients/{patient_id}/sync-week-visits-to-fixed
 *
 * Backend 仕様: `backend/app/schemas/v2/patient_sync.py` と一致.
 * RBAC: admin / manager のみ (BE 側で 403 担保).
 */
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import { FIXED_VISITS_KEY } from '@/lib/queries/patient_fixed_visits';
import {
  syncWeekToFixedRequestSchema,
  syncWeekToFixedResponseSchema,
  type SyncWeekToFixedRequest,
  type SyncWeekToFixedResponse,
} from '@/lib/schemas/patientSync';

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

function buildPath(patientId: string): string {
  return `/api/v1/patients/${patientId}/sync-week-visits-to-fixed`;
}

/**
 * POST /api/v1/patients/{patient_id}/sync-week-visits-to-fixed
 *
 * 今週 visits を patient_fixed_visits (mode='normal', slot_index=0) に upsert.
 * dry_run=true (default) なら DB 変更なしで diff のみ返す。
 *
 * apply 成功時は当該 patient の固定枠キャッシュを invalidate する。
 */
export function useSyncWeekVisitsToFixedMutation(
  patientId: string | null | undefined,
): UseMutationResult<SyncWeekToFixedResponse, Error, SyncWeekToFixedRequest> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<SyncWeekToFixedResponse, Error, SyncWeekToFixedRequest>({
    mutationFn: async (raw) => {
      if (!patientId) {
        throw new Error('patient_id is required');
      }
      const payload = syncWeekToFixedRequestSchema.parse(raw);
      const result = await fetcher<unknown>(buildPath(patientId), {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return syncWeekToFixedResponseSchema.parse(result);
    },
    onSuccess: (res) => {
      // apply 経路 (transaction_applied=true) のときのみ PFV キャッシュを更新する.
      if (res.transaction_applied && patientId) {
        void qc.invalidateQueries({ queryKey: FIXED_VISITS_KEY(patientId) });
        void qc.invalidateQueries({ queryKey: FIXED_VISITS_KEY(patientId, 'normal') });
      }
    },
  });
}
