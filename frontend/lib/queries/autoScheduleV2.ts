'use client';

/**
 * Auto-schedule v2.0 (Wave 41 v2) — TanStack Query mutation hook 群.
 *
 *   - POST /api/v1/schedule/v2/diff-add          → useDiffAddProposalsMutation
 *   - POST /api/v1/schedule/v2/apply-individual  → useApplyIndividualMutation
 *   - POST /api/v1/schedule/v2/reset-to-fixed    → useResetToFixedMutation
 *
 * BE 仕様: ``backend/app/schemas/v2/auto_schedule_v2.py`` と一致.
 * 設計仕様書: ``docs/plans/auto-schedule-v2.md`` v0.2
 *
 * Backend は ``office_ids: UUID[]`` (空配列 = 全拠点) を受け取る点に注意.
 *
 * RBAC: admin / manager のみ (BE 側で 403 担保).
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  applyIndividualRequestSchema,
  applyIndividualResponseSchema,
  diffAddRequestSchema,
  diffAddResponseSchema,
  resetToFixedRequestSchema,
  resetToFixedResponseSchema,
  unassignAllStaffRequestSchema,
  unassignAllStaffResponseSchema,
  updateFixedTimeMasterRequestSchema,
  updateFixedTimeMasterResponseSchema,
  updateFixedTimeWeekOnlyRequestSchema,
  updateFixedTimeWeekOnlyResponseSchema,
  type ApplyIndividualRequest,
  type ApplyIndividualResponse,
  type DiffAddRequest,
  type DiffAddResponse,
  type ResetToFixedRequest,
  type ResetToFixedResponse,
  type UnassignAllStaffRequest,
  type UnassignAllStaffResponse,
  type UpdateFixedTimeMasterRequest,
  type UpdateFixedTimeMasterResponse,
  type UpdateFixedTimeWeekOnlyRequest,
  type UpdateFixedTimeWeekOnlyResponse,
} from '@/lib/schemas/v2/autoScheduleV2';

const DIFF_ADD_PATH = '/api/v1/schedule/v2/diff-add';
const APPLY_INDIVIDUAL_PATH = '/api/v1/schedule/v2/apply-individual';
const RESET_TO_FIXED_PATH = '/api/v1/schedule/v2/reset-to-fixed';
const UPDATE_FIXED_TIME_MASTER_PATH = '/api/v1/schedule/v2/update-fixed-time-master';
const UPDATE_FIXED_TIME_WEEK_ONLY_PATH = '/api/v1/schedule/v2/update-fixed-time-week-only';
const UNASSIGN_ALL_STAFF_PATH = '/api/v1/schedule/v2/unassign-all-staff';

function authPair(session: ReturnType<typeof useSession>['data']) {
  return {
    accessToken: session?.accessToken ?? null,
    refreshToken: session?.refreshToken ?? null,
  };
}

/**
 * POST /api/v1/schedule/v2/diff-add — プール患者の差分追加候補を生成する.
 *
 * 「差分追加」ボタン押下 → このフックを叩いて候補リスト取得.
 * 候補ごとに ``useApplyIndividualMutation`` で 1 件ずつ採用する.
 * BE 側は何も書き込まない (read-only な提案計算).
 */
export function useDiffAddProposalsMutation(): UseMutationResult<
  DiffAddResponse,
  Error,
  DiffAddRequest
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<DiffAddResponse, Error, DiffAddRequest>({
    mutationFn: async (raw) => {
      const payload = diffAddRequestSchema.parse(raw);
      const result = await fetcher<unknown>(DIFF_ADD_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return diffAddResponseSchema.parse(result);
    },
  });
}

/**
 * GET (実体は POST) /api/v1/schedule/v2/diff-add — diff-add 提案を **キャッシュ可能** に取得する.
 *
 * 用途: 単体表示 (PatientScheduleDetailDialog のプール投入セクション) で、
 * 患者カードをクリックするたびに同じ拠点・週の提案を再計算しないよう、
 * TanStack Query のキャッシュに載せて共有する。提案リスト全体を取得し、
 * 呼出側で対象患者 1 件を ``patient_id`` で抽出する (クライアント側フィルタ).
 *
 * ドリフト防止: 一括表示 (DiffAddDialog) と **同一エンドポイント / 同一引数** を叩くため、
 * 同じ患者は両表示で必ず同一の提案になる。office スコープを揃えること (呼出側責務).
 *
 * BE 側は read-only (write なし). queryKey に office_ids をソートして含めることで
 * 拠点スコープ違いを別キャッシュに分離する。
 *
 * 算出は重い (full pipeline) ため staleTime を長めに取り、不要な再 fetch を避ける。
 */
export function useDiffAddProposalsQuery(
  params: { isoYear: number; isoWeek: number; officeId: string | null },
  options?: { enabled?: boolean },
): UseQueryResult<DiffAddResponse, Error> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);
  const { isoYear, isoWeek, officeId } = params;
  const officeIds = officeId ? [officeId] : [];

  return useQuery<DiffAddResponse, Error>({
    // office_ids は [] / [id] のみだが将来の複数拠点に備えソートして安定化する.
    queryKey: ['diff-add', isoYear, isoWeek, [...officeIds].sort()],
    queryFn: async () => {
      const payload = diffAddRequestSchema.parse({
        iso_year: isoYear,
        iso_week: isoWeek,
        office_ids: officeIds,
      });
      const result = await fetcher<unknown>(DIFF_ADD_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return diffAddResponseSchema.parse(result);
    },
    enabled: options?.enabled ?? true,
    // full pipeline 再計算は高コスト. 5 分は同一週・拠点のバッチを再利用する.
    staleTime: 5 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
}

/**
 * POST /api/v1/schedule/v2/apply-individual — 1 患者の提案を採用する.
 *
 * 差分追加の候補採用でこのエンドポイントを使う.
 * 当該患者の ``patient_fixed_visits`` を更新するため、関連キャッシュを invalidate する.
 *
 * confirm=true を BE が強制するため schema 側で literal(true) に固定済み.
 */
export function useApplyIndividualMutation(): UseMutationResult<
  ApplyIndividualResponse,
  Error,
  ApplyIndividualRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ApplyIndividualResponse, Error, ApplyIndividualRequest>({
    mutationFn: async (raw) => {
      const payload = applyIndividualRequestSchema.parse(raw);
      const result = await fetcher<unknown>(APPLY_INDIVIDUAL_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return applyIndividualResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
      // 採用で当該患者の固定枠が変わるため、キャッシュした diff-add 提案も無効化する.
      // 一括ダイアログ (DiffAddDialog) は mutation ベースで本キーを購読しないため再計算storm は起きない.
      void qc.invalidateQueries({ queryKey: ['diff-add'] });
    },
  });
}

/**
 * POST /api/v1/schedule/v2/reset-to-fixed — 対象週の visits を patient_fixed_visits から再生成する.
 *
 * §13.6 機能 D: 「📌 固定枠に戻す」.
 * 1 週間単位 (Q4 確定) で全 visits を soft-delete + 再生成する.
 */
export function useResetToFixedMutation(): UseMutationResult<
  ResetToFixedResponse,
  Error,
  ResetToFixedRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ResetToFixedResponse, Error, ResetToFixedRequest>({
    mutationFn: async (raw) => {
      const payload = resetToFixedRequestSchema.parse(raw);
      const result = await fetcher<unknown>(RESET_TO_FIXED_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return resetToFixedResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
    },
  });
}

/**
 * POST /api/v1/schedule/v2/update-fixed-time-master — 固定時間マスター更新.
 *
 * 同住所集約警告の「マスター更新」アクションから呼ぶ. patient_fixed_visits の
 * start_time / duration_min と patients.weekly_pattern.entries[weekday] の
 * preferred_start / preferred_end / time_type を更新する (永続).
 */
export function useUpdateFixedTimeMasterMutation(): UseMutationResult<
  UpdateFixedTimeMasterResponse,
  Error,
  UpdateFixedTimeMasterRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<UpdateFixedTimeMasterResponse, Error, UpdateFixedTimeMasterRequest>({
    mutationFn: async (raw) => {
      const payload = updateFixedTimeMasterRequestSchema.parse(raw);
      const result = await fetcher<unknown>(UPDATE_FIXED_TIME_MASTER_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return updateFixedTimeMasterResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['patient-fixed-visits'] });
      void qc.invalidateQueries({ queryKey: ['patients'] });
    },
  });
}

/**
 * POST /api/v1/schedule/v2/update-fixed-time-week-only — 今週限定の時刻変更.
 *
 * apply-week-only 適用後に DB に入った visit 1 件の start_time / end_time を
 * 上書きする. 自動算出由来 (source='auto_alloc_v2*') の visit のみ許可.
 */
export function useUpdateFixedTimeWeekOnlyMutation(): UseMutationResult<
  UpdateFixedTimeWeekOnlyResponse,
  Error,
  UpdateFixedTimeWeekOnlyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<UpdateFixedTimeWeekOnlyResponse, Error, UpdateFixedTimeWeekOnlyRequest>({
    mutationFn: async (raw) => {
      const payload = updateFixedTimeWeekOnlyRequestSchema.parse(raw);
      const result = await fetcher<unknown>(UPDATE_FIXED_TIME_WEEK_ONLY_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return updateFixedTimeWeekOnlyResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visits'] });
    },
  });
}

/**
 * POST /api/v1/schedule/v2/unassign-all-staff — Phase G-17: 一斉未割当.
 *
 * 表示中の週の全 Course 担当 + visit_staff_assignments を一括解除する.
 * courses / visits 自体は残し、担当 (assigned_staff_id) と
 * visit_staff_assignments のみを消す. RBAC: admin / manager のみ (BE 担保).
 *
 * onSuccess で courses / visits / visit_staff_assignments cache を invalidate.
 */
export function useUnassignAllStaffMutation(): UseMutationResult<
  UnassignAllStaffResponse,
  Error,
  UnassignAllStaffRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<UnassignAllStaffResponse, Error, UnassignAllStaffRequest>({
    mutationFn: async (raw) => {
      const payload = unassignAllStaffRequestSchema.parse(raw);
      const result = await fetcher<unknown>(UNASSIGN_ALL_STAFF_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return unassignAllStaffResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      void qc.invalidateQueries({ queryKey: ['visit-staff-assignments'] });
    },
  });
}
