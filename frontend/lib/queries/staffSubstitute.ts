'use client';

/**
 * P3-① 当日欠勤の代替スタッフ提案 — TanStack Query hooks.
 *
 *   GET  /api/v1/schedule/staff-substitute/candidates?absent_staff_id&target_date
 *   POST /api/v1/schedule/staff-substitute/apply
 *
 * 契約は `frontend/lib/schemas/v2/staffSubstitute.ts` (BE Pydantic と 1:1)。
 * RBAC: admin / manager のみ (BE 側で 403 担保)。
 */
import { useMutation, useQuery, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  substituteApplyRequestSchema,
  substituteApplyResponseSchema,
  substituteCandidatesResponseSchema,
  type SubstituteApplyRequest,
  type SubstituteApplyResponse,
  type SubstituteCandidatesResponse,
} from '@/lib/schemas/v2/staffSubstitute';

const CANDIDATES_PATH = '/api/v1/schedule/staff-substitute/candidates';
const APPLY_PATH = '/api/v1/schedule/staff-substitute/apply';

/** 共有キャッシュキー接頭辞 (apply 後の invalidate を全 variant にマッチさせる)。 */
export const staffSubstituteKey = ['staff-substitute', 'candidates'] as const;

function useAuthTokens() {
  const { data: session, status } = useSession();
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
    isAuthenticated: status === 'authenticated',
  };
}

/**
 * GET candidates — 欠勤スタッフの当日 planned visit + 候補ランキングを取得。
 *
 * `enabled` false のときは fetch しない (検索ボタン押下まで待つ用途)。
 */
export function useSubstituteCandidates(
  absentStaffId: string | null | undefined,
  targetDate: string | null | undefined,
  enabled: boolean,
) {
  const { accessToken, refreshToken, isAuthenticated } = useAuthTokens();

  return useQuery<SubstituteCandidatesResponse>({
    queryKey: [...staffSubstituteKey, absentStaffId ?? '__none__', targetDate ?? '__none__'],
    queryFn: async () => {
      if (!absentStaffId || !targetDate) throw new Error('absent_staff_id / target_date is required');
      const qs = new URLSearchParams({
        absent_staff_id: absentStaffId,
        target_date: targetDate,
      });
      const raw = await fetcher<unknown>(`${CANDIDATES_PATH}?${qs.toString()}`, {
        accessToken,
        refreshToken,
      });
      return substituteCandidatesResponseSchema.parse(raw);
    },
    enabled: enabled && isAuthenticated && !!absentStaffId && !!targetDate,
  });
}

/**
 * POST apply — 選択済みの差替えをまとめて適用 (all-or-nothing)。
 *
 * onSuccess で visits / courses / candidates を invalidate し、
 * コーステーブルと候補リストを最新化する。
 */
export function useApplySubstitutions(): UseMutationResult<
  SubstituteApplyResponse,
  Error,
  SubstituteApplyRequest
> {
  const qc = useQueryClient();
  const { accessToken, refreshToken } = useAuthTokens();

  return useMutation<SubstituteApplyResponse, Error, SubstituteApplyRequest>({
    mutationFn: async (raw) => {
      const payload = substituteApplyRequestSchema.parse(raw);
      const res = await fetcher<unknown>(APPLY_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return substituteApplyResponseSchema.parse(res);
    },
    onSuccess: () => {
      // visits は primary/secondary_staff_id が差替えられる
      void qc.invalidateQueries({ queryKey: ['visits'] });
      // courses は担当スタッフ表示が変わる可能性
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // 候補リストは差替え済みで再計算が必要
      void qc.invalidateQueries({ queryKey: staffSubstituteKey });
    },
  });
}
