'use client';

/**
 * Auto-schedule v2.0 (Wave 41 v2) — TanStack Query mutation hook 群.
 *
 *   - POST /api/v1/schedule/v2/diff-add          → useDiffAddProposalsMutation
 *   - POST /api/v1/schedule/v2/full-optimize     → useFullOptimizeMutation
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
import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';

import { fetcher } from '@/lib/api/fetcher';
import {
  applyIndividualRequestSchema,
  applyIndividualResponseSchema,
  applyWeekOnlyRequestSchema,
  applyWeekOnlyResponseSchema,
  diffAddRequestSchema,
  diffAddResponseSchema,
  fullOptimizeRequestSchema,
  fullOptimizeResponseSchema,
  resetToFixedRequestSchema,
  resetToFixedResponseSchema,
  updateFixedTimeMasterRequestSchema,
  updateFixedTimeMasterResponseSchema,
  updateFixedTimeWeekOnlyRequestSchema,
  updateFixedTimeWeekOnlyResponseSchema,
  type ApplyIndividualRequest,
  type ApplyIndividualResponse,
  type ApplyWeekOnlyRequest,
  type ApplyWeekOnlyResponse,
  type DiffAddRequest,
  type DiffAddResponse,
  type FullOptimizeRequest,
  type FullOptimizeResponse,
  type ResetToFixedRequest,
  type ResetToFixedResponse,
  type UpdateFixedTimeMasterRequest,
  type UpdateFixedTimeMasterResponse,
  type UpdateFixedTimeWeekOnlyRequest,
  type UpdateFixedTimeWeekOnlyResponse,
} from '@/lib/schemas/v2/autoScheduleV2';

const DIFF_ADD_PATH = '/api/v1/schedule/v2/diff-add';
const FULL_OPTIMIZE_PATH = '/api/v1/schedule/v2/full-optimize';
const APPLY_INDIVIDUAL_PATH = '/api/v1/schedule/v2/apply-individual';
const RESET_TO_FIXED_PATH = '/api/v1/schedule/v2/reset-to-fixed';
const APPLY_WEEK_ONLY_PATH = '/api/v1/schedule/v2/apply-week-only';
const UPDATE_FIXED_TIME_MASTER_PATH = '/api/v1/schedule/v2/update-fixed-time-master';
const UPDATE_FIXED_TIME_WEEK_ONLY_PATH = '/api/v1/schedule/v2/update-fixed-time-week-only';

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
 * POST /api/v1/schedule/v2/full-optimize — 全 active 患者で週単位の再構築提案を生成する.
 *
 * 計算は重い (spinner 必須). BE 側は何も書き込まない.
 * 採用は ``useApplyIndividualMutation`` を 1 患者ずつ呼ぶ.
 */
export function useFullOptimizeMutation(): UseMutationResult<
  FullOptimizeResponse,
  Error,
  FullOptimizeRequest
> {
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<FullOptimizeResponse, Error, FullOptimizeRequest>({
    mutationFn: async (raw) => {
      const payload = fullOptimizeRequestSchema.parse(raw);
      const result = await fetcher<unknown>(FULL_OPTIMIZE_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return fullOptimizeResponseSchema.parse(result);
    },
  });
}

/**
 * POST /api/v1/schedule/v2/apply-individual — 1 患者の提案を採用する.
 *
 * 差分追加 / 全面最適化のどちらから来てもこのエンドポイントを使う.
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
 * POST /api/v1/schedule/v2/apply-week-only — この週だけ反映 (固定枠は変更しない).
 *
 * 全面最適化の提案を visits のみへ反映する慎重モード. patient_fixed_visits は
 * 触らないので翌週からは元の固定枠ベースのスケジュールに戻る.
 * visits / courses cache のみ invalidate (固定枠は除外).
 */
export function useApplyWeekOnlyMutation(): UseMutationResult<
  ApplyWeekOnlyResponse,
  Error,
  ApplyWeekOnlyRequest
> {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const { accessToken, refreshToken } = authPair(session);

  return useMutation<ApplyWeekOnlyResponse, Error, ApplyWeekOnlyRequest>({
    mutationFn: async (raw) => {
      const payload = applyWeekOnlyRequestSchema.parse(raw);
      const result = await fetcher<unknown>(APPLY_WEEK_ONLY_PATH, {
        method: 'POST',
        body: JSON.stringify(payload),
        accessToken,
        refreshToken,
      });
      return applyWeekOnlyResponseSchema.parse(result);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['visits'] });
      void qc.invalidateQueries({ queryKey: ['courses'] });
      // patient-fixed-visits は触らないので invalidate しない.
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
